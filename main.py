#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTCC USDT 선물 롱/숏 시그널 봇 (텔레그램 알림 전용, 자동 주문 없음)
- 기존 Gate.io 봇의 상위 타임프레임(4h) 추세 필터 + 거래량 모멘텀 필터 로직을 BTCC API 환경으로 이식
"""

import csv
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

# ──────────────────────────── 설정 ────────────────────────────
TELEGRAM_TOKEN = os.getenv("BTCC_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("BTCC_TELEGRAM_CHAT_ID")

STATE_FILE = "btcc_state.json"
TRADES_FILE = "trades.csv"

# 💡 BTCC 퍼블릭 선물 API 베이스 URL
BASE_URL = "https://a.btcc.com/api/v1/market"

MAX_SLOTS = 5                  # 최대 슬롯 5개

# 진입 조건 
MIN_CHANGE_PCT = 2.5          # 24시간 변동률 기준(%)
MIN_QUOTE_VOLUME = 300_000    # 24시간 거래대금 최소값(USDT)
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
BODY_RATIO_MIN = 0.25         # 캔들 몸통 비율
LEVERAGED_TOKEN_RE = re.compile(r"\d+[LS]_USDT$")  # 레버리지 토큰 제외

# 목표가/손절가 (ATR 배수)
ATR_SL = 1.5
ATR_TP_MULTS = (1.0, 2.5, 4.0)   
MAX_SL_PCT = 6.0              
MAX_PER_THEME = 1             

# 비용/리스크 가정 및 수익형 본전 방어 오프셋
FEE_ROUNDTRIP = 0.001         
PROFIT_LOCK_ATR = 0.2         
RISK_PER_TRADE_PCT = 1.5      
MAX_MARGIN_LOSS = 0.5         

# 보유 시간 및 쿨다운 규칙
ROTATE_HOURS = 18             
ROTATE_MIN_PROFIT_PCT = 0.5   
TIME_STOP_HOURS = 24          
COOLDOWN_HOURS = 4            

# 결과 분류: 무승부 밴드
DRAW_BAND_R = 0.05

MAX_CANDIDATES = 50           
SCAN_BUDGET_SEC = 150

FAIL_ALERT_THRESHOLD = 3      
DAILY_REPORT_ENABLED = True
DAILY_REPORT_HOUR_UTC = 0     

CSV_FIELDS = [
    "closed_at", "symbol", "direction", "category", "leverage",
    "entry_time", "entry_price", "exit_price", "exit_reason",
    "holding_hours", "gross_r", "net_r", "mfe_r", "mae_r",
    "leverage_pct", "outcome"
]

REASON_LABEL = {
    "SL": "손절",
    "PROFIT_LOCK": "수익 방어",
    "TP1_PROTECT": "TP1 방어",
    "TP3": "TP3",
    "ROTATE": "회전",
    "TIME_STOP": "타임스탑",
}

NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


# ──────────────────────────── 공통 유틸 ────────────────────────────
def utcnow():
    return datetime.now(timezone.utc)


def parse_iso(s, default=None):
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return default


def fmt_price(p):
    if p >= 1000:
        return f"{p:.2f}"
    if p >= 1:
        return f"{p:.4f}"
    if p >= 0.01:
        return f"{p:.5f}"
    return f"{p:.8f}"


def fmt_hours(hours):
    h = int(hours)
    m = int((hours - h) * 60)
    return f"{h}시간 {m:02d}분"


def dir_tag(direction):
    return "🟢 롱" if direction == "LONG" else "🔴 숏"


def chart_link(symbol):
    return f"https://www.btcc.com"  # BTCC 거래소 링크 대응


def pct_move(direction, entry, price):
    if entry <= 0:
        return 0.0
    if direction == "LONG":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def r_move(direction, entry, price, risk):
    if risk <= 0:
        return 0.0
    if direction == "LONG":
        return (price - entry) / risk
    return (entry - price) / risk


def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"Failed to send telegram message: {response.text}")
            return False
        return True
    except Exception as e:
        print(f"Error sending telegram message: {e}")
        return False


# ──────────────────────────── 상태 저장/불러오기 ────────────────────────────
def default_state():
    return {
        "active_slots": {},
        "stats": {
            "wins": 0, "draws": 0, "losses": 0, "total": 0,
            "r_count": 0, "sum_r": 0.0, "win_r": 0.0, "loss_r": 0.0,
            "recent_r": [],
        },
        "closed_symbols": {},
        "fail_count": 0,
        "last_report_date": "",
    }


def load_state():
    state = default_state()
    if not os.path.exists(STATE_FILE):
        return state
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading state file: {e}")
        try:
            os.replace(STATE_FILE, STATE_FILE + ".corrupt")
        except Exception:
            pass
        return state

    if not isinstance(data, dict):
        return state
    if isinstance(data.get("active_slots"), dict):
        state["active_slots"] = data["active_slots"]
    if isinstance(data.get("stats"), dict):
        state["stats"].update(data["stats"])
    
    closed = data.get("closed_symbols", {})
    if isinstance(closed, list):
        state["closed_symbols"] = {sym: {"time": utcnow().isoformat(), "direction": "LONG", "reason": "SL"} for sym in closed}
    elif isinstance(closed, dict):
        new_closed = {}
        for sym, val in closed.items():
            if isinstance(val, str):
                new_closed[sym] = {"time": val, "direction": "LONG", "reason": "SL"}
            elif isinstance(val, dict):
                new_closed[sym] = val
        state["closed_symbols"] = new_closed

    try:
        state["fail_count"] = int(data.get("fail_count", 0))
    except Exception:
        state["fail_count"] = 0
    state["last_report_date"] = str(data.get("last_report_date", ""))
    return state


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def append_trade_csv(row):
    exists = os.path.exists(TRADES_FILE) and os.path.getsize(TRADES_FILE) > 0
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction='ignore')
        if not exists:
            writer.writeheader()
        clean_row = {field: row.get(field, "") for field in CSV_FIELDS}
        writer.writerow(clean_row)


# ──────────────────────────── BTCC API 연동 (경로 변경) ────────────────────────────
def api_get(path, params=None):
    try:
        response = requests.get(f"{BASE_URL}{path}", params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if response.status_code == 200:
            return response.json()
        print(f"API error {response.status_code} {path}: {response.text[:200]}")
    except Exception as e:
        print(f"API request failed {path}: {e}")
    return None


def fetch_tickers():
    data = api_get("/tickers")
    if isinstance(data, dict):
        return data.get("data", data.get("list", []))
    return data if isinstance(data, list) else []


def parse_candles(raw):
    out = []
    for c in raw or []:
        try:
            out.append({
                "t": int(c.get("t", c.get("time", 0))),
                "o": float(c.get("o", c.get("open", 0))),
                "h": float(c.get("h", c.get("high", 0))),
                "l": float(c.get("l", c.get("low", 0))),
                "c": float(c.get("c", c.get("close", 0))),
                "v": float(c.get("v", c.get("volume", 0))),
            })
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["t"])
    return out


def fetch_completed_candles(symbol, interval, from_ts, now_ts):
    if from_ts >= now_ts:
        return []
    raw = api_get("/candlesticks", {
        "symbol": symbol, "interval": interval,
        "from": int(from_ts), "to": int(now_ts),
    })
    if isinstance(raw, dict):
        raw = raw.get("data", raw.get("list", []))
    step_sec = 300 if interval == "5m" else (14400 if interval == "4h" else 3600)
    return [c for c in parse_candles(raw) if c["t"] >= from_ts and c["t"] + step_sec <= now_ts]


def fetch_completed_5m(symbol, from_ts, now_ts):
    return fetch_completed_candles(symbol, "5m", from_ts, now_ts)


# ──────────────────────────── 지표 및 고도화 필터 ────────────────────────────
def wilder_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


def calc_ma(closes, period=50):
    if len(closes) < period:
        return sum(closes) / len(closes) if closes else None
    return sum(closes[-period:]) / period


def calc_ema(closes, period=20):
    if len(closes) < period:
        return sum(closes) / len(closes) if closes else None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_adx(candles, period=ADX_PERIOD):
    if len(candles) < period * 2:
        return 25.0  
    
    tr_list, plus_dm_list, minus_dm_list = [], [], []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
        
        up_move = h - candles[i - 1]["h"]
        down_move = candles[i - 1]["l"] - l
        
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        
    atr_val = sum(tr_list[:period]) / period
    plus_di = sum(plus_dm_list[:period]) / period
    minus_di = sum(minus_dm_list[:period]) / period
    
    dx_list = []
    for i in range(period, len(tr_list)):
        atr_val = (atr_val * (period - 1) + tr_list[i]) / period
        plus_di = (plus_di * (period - 1) + plus_dm_list[i]) / period
        minus_di = (minus_di * (period - 1) + minus_dm_list[i]) / period
        
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx = 0
        else:
            dx = abs(plus_di - minus_di) / di_sum * 100
        dx_list.append(dx)
        
    if not dx_list:
        return 25.0
    return sum(dx_list[-period:]) / len(dx_list[-period:])


def check_order_block_retest(candles, direction, current_price):
    if len(candles) < 5:
        return None, None

    last = candles[-1]
    for i in range(len(candles) - 2, max(0, len(candles) - 6), -1):
        c = candles[i]
        if direction == "LONG":
            if c["o"] > c["c"]:  # 음봉 (Demand OB)
                zone_low, zone_high = c["l"], c["o"]
                if zone_low * 0.97 <= current_price <= zone_high * 1.04:
                    return zone_low, zone_high
        else:
            if c["c"] > c["o"]:  # 양봉 (Supply OB)
                zone_low, zone_high = c["o"], c["h"]
                if zone_low * 0.96 <= current_price <= zone_high * 1.03:
                    return zone_low, zone_high
                    
    return last["l"], last["h"]


def analyze_signal(symbol, direction, now_ts, current_price):
    raw = api_get("/candlesticks", {"symbol": symbol, "interval": "1h", "limit": 80})
    if isinstance(raw, dict):
        raw = raw.get("data", raw.get("list", []))
    candles = [c for c in parse_candles(raw) if c["t"] + 3600 <= now_ts]
    if len(candles) < 50:
        return None

    closes = [c["c"] for c in candles]
    
    ma50 = calc_ma(closes, 50)
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    adx = calc_adx(candles, ADX_PERIOD)
    atr = calc_atr(candles, ATR_PERIOD)
    rsi = wilder_rsi(closes, RSI_PERIOD)
    
    if not ma50 or not ema20 or not ema50 or not atr or atr <= 0 or rsi is None:
        return None

    last = candles[-1]
    total_range = last["h"] - last["l"]
    if total_range <= 0:
        return None
    body_ratio = abs(last["c"] - last["o"]) / total_range
    if body_ratio < BODY_RATIO_MIN:
        return None

    ema_distance = abs(current_price - ema20)

    if direction == "LONG":
        if not (ema20 > ema50 and current_price > ma50):
            return None
        if adx < 14:
            return None
        if ema_distance > (1.5 * atr):
            return None
        if rsi < 45 or last["c"] <= last["o"] or body_ratio < BODY_RATIO_MIN:
            return None
    else:
        if not (ema20 < ema50 and current_price < ma50):
            return None
        if adx < 14:
            return None
        if ema_distance > (1.5 * atr):
            return None
        if rsi > 55 or last["c"] >= last["o"] or body_ratio < BODY_RATIO_MIN:
            return None

    volumes = [c["v"] for c in candles]
    if len(volumes) >= 20:
        avg_vol = sum(volumes[-21:-1]) / 20
        if avg_vol > 0 and last["v"] < avg_vol * 1.1:
            return None

    candles_4h = fetch_completed_candles(symbol, "4h", now_ts - (60 * 14400), now_ts)
    if len(candles_4h) >= 30:
        closes_4h = [c["c"] for c in candles_4h]
        ema20_4h = calc_ema(closes_4h, 20)
        ema50_4h = calc_ema(closes_4h, 50)
        if ema20_4h and ema50_4h:
            if direction == "LONG" and ema20_4h <= ema50_4h:
                return None
            if direction == "SHORT" and ema20_4h >= ema50_4h:
                return None

    zone_low, zone_high = check_order_block_retest(candles, direction, current_price)
    if not zone_low or not zone_high:
        return None

    return {
        "rsi": rsi, 
        "atr": atr, 
        "adx": adx,
        "ema20": ema20,
        "body_ratio": body_ratio, 
        "ob_low": zone_low, 
        "ob_high": zone_high
    }


# ──────────────────────────── 분류/레버리지/테마 ────────────────────────────
STOCK_BASES = {"SKHYNIX", "TSLA", "AAPL", "NVDA", "MSFT", "AMZN", "META", "NFLX",
               "GOOGL", "BABA", "AMD", "INTC", "PLTR", "COIN", "MSTR",
               "SOXL", "SOXS", "TQQQ", "SQQQ"}
INDEX_BASES = {"SPX", "NAS", "NDX", "DJI", "US30", "US100", "NIKKEI", "VIX", "DAX"}


def get_asset_category(symbol):
    base = symbol.upper().replace("_USDT", "").replace("USDT", "")
    if base in STOCK_BASES:
        return "주식"
    if base in INDEX_BASES:
        return "지수"
    return "코인"


THEMES = {
    "반도체": {"SOXL": 1, "SOXS": -1, "SKHYNIX": 1, "NVDA": 1, "AMD": 1, "INTC": 1,
             "MU": 1, "TSM": 1},
    "미국지수": {"NAS": 1, "NDX": 1, "SPX": 1, "US100": 1, "US30": 1, "DJI": 1,
             "TQQQ": 1, "SQQQ": -1},
}


def theme_exposure(symbol, direction):
    base = symbol.upper().replace("_USDT", "").replace("USDT", "")
    for theme, members in THEMES.items():
        if base in members:
            return theme, members[base] * (1 if direction == "LONG" else -1)
    return None, 0


def count_theme_slots(active, theme, sign):
    count = 0
    for sym, info in active.items():
        t, s = theme_exposure(sym, info.get("direction", "LONG"))
        if t == theme and s == sign:
            count += 1
    return count


def calculate_dynamic_leverage(category, change_24h, atr, current_price):
    abs_change = abs(change_24h)
    if category == "코인":
        base_lev = 5 if abs_change > 10.0 else (7 if abs_change > 6.0 else 10)
    else:
        base_lev = 8 if category == "주식" else 12

    if current_price > 0 and atr > 0:
        atr_pct = (atr / current_price) * 100
        if atr_pct > 3.5:
            base_lev = max(3, base_lev - 3)
        elif atr_pct > 2.0:
            base_lev = max(4, base_lev - 2)

    return base_lev


# ──────────────────────────── 전적 통계 ────────────────────────────
def record_stats(stats, net_r, outcome):
    stats["total"] += 1
    stats[{"win": "wins", "draw": "draws", "loss": "losses"}[outcome]] += 1
    stats["r_count"] += 1
    stats["sum_r"] += net_r
    if net_r > 0:
        stats["win_r"] += net_r
    elif net_r < 0:
        stats["loss_r"] += abs(net_r)
    stats["recent_r"] = (stats.get("recent_r", []) + [round(net_r, 3)])[-50:]


def classify_trade(info, reason, net_r):
    if reason == "TP3":
        return "win"
    if reason == "TP1_PROTECT":
        return "win"
    if reason == "PROFIT_LOCK":
        return "draw"
    if reason == "SL":
        return "loss"
    if reason in ("ROTATE", "TIME_STOP"):
        if info.get("tp2_reached"):
            return "win"
        if info.get("tp1_reached"):
            return "draw"
        if net_r > DRAW_BAND_R:
            return "win"
        if net_r < -DRAW_BAND_R:
            return "loss"
        return "draw"
    if net_r > DRAW_BAND_R:
        return "win"
    if net_r < -DRAW_BAND_R:
        return "loss"
    return "draw"


def build_stats_line(stats):
    total = stats.get("total", 0)
    wins, draws, losses = stats.get("wins", 0), stats.get("draws", 0), stats.get("losses", 0)
    decisive = wins + losses
    win_rate = round(wins / decisive * 100, 1) if decisive > 0 else 0.0
    
    lines = [
        "📈 <b>[누적 통계]</b>",
        f"<code>{total}전 {wins}승 {draws}무 {losses}패</code>",
        f"<code>승률 {win_rate}% ({wins}승 {losses}패 / 무승부 제외)</code>",
    ]
    n = stats.get("r_count", 0)
    if n > 0:
        avg_r = stats["sum_r"] / n
        loss_r = stats.get("loss_r", 0)
        pf = f"{stats['win_r'] / loss_r:.2f}" if loss_r > 0 else "-"
        recent = stats.get("recent_r", [])[-20:]
        lines.append(
            f"<code>평균 R {avg_r:+.2f} | PF {pf} | 최근 {len(recent)}전 {sum(recent):+.1f}R</code>"
        )
    else:
        lines.append("<code>R 통계: 기록 대기 중</code>")
    return "\n".join(lines)


# ──────────────────────────── 포지션 관리 ────────────────────────────
def migrate_slot(info):
    entry = float(info.get("entry_price", 0) or 0)
    atr = float(info.get("atr", info.get("risk", entry) / ATR_SL))
    if "risk" not in info:
        info["risk"] = abs(float(info.get("tp1", entry)) - entry)
    info.setdefault("direction", "LONG")
    info.setdefault("leverage", 10)
    info.setdefault("tp1_reached", False)
    info.setdefault("tp2_reached", False)
    info.setdefault("max_fav", entry)
    info.setdefault("max_adv", entry)
    if "last_candle_ts" not in info:
        dt = parse_iso(info.get("entry_time", ""), utcnow())
        info["last_candle_ts"] = int(dt.timestamp()) // 300 * 300
        
    info.setdefault("stop_loss", info.get("initial_stop", entry))
    info.setdefault("tp1", entry)
    info.setdefault("tp2", entry)
    info.setdefault("tp3", entry)

    profit_lock_target = entry + (atr * PROFIT_LOCK_ATR) if info["direction"] == "LONG" else entry - (atr * PROFIT_LOCK_ATR)

    if info["direction"] == "LONG":
        if info["tp1_reached"]:
            info["stop_loss"] = max(info["stop_loss"], profit_lock_target)
        if info["tp2_reached"]:
            info["stop_loss"] = max(info["stop_loss"], info["tp1"])
    else:
        if info["tp1_reached"]:
            info["stop_loss"] = min(info["stop_loss"], profit_lock_target)
        if info["tp2_reached"]:
            info["stop_loss"] = min(info["stop_loss"], info["tp1"])


def step(info, high, low):
    direction, entry = info["direction"], info["entry_price"]
    atr = float(info.get("atr", info.get("risk", entry) / ATR_SL))
    profit_lock_target = entry + (atr * PROFIT_LOCK_ATR) if direction == "LONG" else entry - (atr * PROFIT_LOCK_ATR)
    
    events = []
    if direction == "LONG":
        info["max_fav"] = max(info["max_fav"], high)
        info["max_adv"] = min(info["max_adv"], low)
        if low <= info["stop_loss"]:
            return events, ("STOP", info["stop_loss"])
        if high >= info["tp3"]:
            return
