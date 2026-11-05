#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTCC USDT 선물 롱/숏 시그널 봇 (실전 최적화 완성본)
- 완성된 봉(Closed Candle) 기준 추세 및 필터 검증
- 텔레그램 알림 전용 (자동 주문 없음)
"""

import csv
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
import ccxt

# ──────────────────────────── 설정 ────────────────────────────
TELEGRAM_TOKEN = os.getenv("BTCC_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("BTCC_TELEGRAM_CHAT_ID")

STATE_FILE = "btcc_state.json"
TRADES_FILE = "btcc_trades.csv"

MAX_SLOTS = 5                  # 최대 슬롯 5개

# 진입 조건 
MIN_CHANGE_PCT = 2.5          # 24시간 변동률 기준(%)
MIN_QUOTE_VOLUME = 500_000    # 24시간 거래대금 최소값(USDT)
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
BODY_RATIO_MIN = 0.30         # 캔들 몸통 비율 강화

# 목표가/손절가 (ATR 배수)
ATR_SL = 1.5
ATR_TP_MULTS = (1.0, 2.5, 4.0)   
MAX_SL_PCT = 5.5              

# 비용/리스크 가정 및 수익형 본전 방어 오프셋
FEE_ROUNDTRIP = 0.001         
PROFIT_LOCK_ATR = 0.2         
MAX_MARGIN_LOSS = 0.5         

# 보유 시간 및 쿨다운 규칙
ROTATE_HOURS = 18             
ROTATE_MIN_PROFIT_PCT = 0.5   
TIME_STOP_HOURS = 24          
COOLDOWN_HOURS = 6            

DRAW_BAND_R = 0.05
MAX_CANDIDATES = 50           


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
    if p >= 1000: return f"{p:.2f}"
    if p >= 1: return f"{p:.4f}"
    if p >= 0.01: return f"{p:.5f}"
    return f"{p:.8f}"

def dir_tag(direction):
    return "🟢 롱" if direction == "LONG" else "🔴 숏"

def chart_link(symbol):
    clean_sym = symbol.replace("/USDT:USDT", "").replace("/USDT", "")
    return f"https://www.btcc.com/en-US/trade/futures/perpetual/{clean_sym}USDT"

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
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
        return response.status_code == 200
    except Exception:
        return False


# ──────────────────────────── 상태 관리 및 CCXT ────────────────────────────
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"active_slots": {}, "closed_symbols": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_slots": {}, "closed_symbols": {}}

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

def get_exchange():
    # 💡 에러 원인 해결: 대문자 BTCC 사용
    return ccxt.BTCC({
        'enableRateLimit': True, 
        'options': {'defaultType': 'swap'}
    })

def fetch_market_data():
    try:
        return get_exchange().fetch_tickers()
    except Exception as e:
        print(f"Failed to fetch BTCC tickers: {e}")
        return {}

def fetch_ohlcv_data(symbol, timeframe='1h', limit=100):
    try:
        raw = get_exchange().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        out = []
        for c in raw:
            out.append({"t": int(c[0] / 1000), "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4]), "v": float(c[5])})
        out.sort(key=lambda x: x["t"])
        return out
    except Exception as e:
        print(f"Failed to fetch OHLCV for {symbol}: {e}")
        return []


# ──────────────────────────── 지표 및 필터 ────────────────────────────
def wilder_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0)); losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def calc_atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1: return None
    trs = [max(c["h"] - c["l"], abs(c["h"] - candles[i]["c"]), abs(c["l"] - candles[i]["c"])) for i, c in enumerate(candles[1:], 1)]
    return sum(trs[-period:]) / period

def calc_ema(closes, period=20):
    if len(closes) < period: return sum(closes) / len(closes) if closes else None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calc_adx(candles, period=ADX_PERIOD):
    if len(candles) < period * 2: return 25.0
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, down = h - candles[i - 1]["h"], candles[i - 1]["l"] - l
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    
    atr_v = sum(tr_list[:period]) / period
    p_di = sum(plus_dm[:period]) / period
    m_di = sum(minus_dm[:period]) / period
    dxs = []
    for i in range(period, len(tr_list)):
        atr_v = (atr_v * (period - 1) + tr_list[i]) / period
        p_di = (p_di * (period - 1) + plus_dm[i]) / period
        m_di = (m_di * (period - 1) + minus_dm[i]) / period
        di_sum = p_di + m_di
        dxs.append((abs(p_di - m_di) / di_sum * 100) if di_sum > 0 else 0)
    return sum(dxs[-period:]) / len(dxs[-period:]) if dxs else 25.0

def analyze_signal(symbol, direction, now_ts):
    candles = fetch_ohlcv_data(symbol, timeframe='1h', limit=80)
    candles = [c for c in candles if c["t"] + 3600 <= now_ts]
    if len(candles) < 50: return None

    closed_candle = candles[-2]
    closes = [c["c"] for c in candles[:-1]]
    
    ema20, ema50 = calc_ema(closes, 20), calc_ema(closes, 50)
    adx, atr = calc_adx(candles[:-1], ADX_PERIOD), calc_atr(candles[:-1], ATR_PERIOD)
    rsi = wilder_rsi(closes, RSI_PERIOD)
    
    if not ema20 or not ema50 or not atr or atr <= 0 or rsi is None: return None

    total_range = closed_candle["h"] - closed_candle["l"]
    if total_range <= 0: return None
    body_ratio = abs(closed_candle["c"] - closed_candle["o"]) / total_range
    if body_ratio < BODY_RATIO_MIN: return None

    if direction == "LONG":
        if not (ema20 > ema50 and closed_candle["c"] > ema50) or adx < 15 or rsi < 45 or closed_candle["c"] <= closed_candle["o"]:
            return None
    else:
        if not (ema20 < ema50 and closed_candle["c"] < ema50) or adx < 15 or rsi > 55 or closed_candle["c"] >= closed_candle["o"]:
            return None

    return {"rsi": rsi, "atr": atr, "adx": adx}


# ──────────────────────────── 메인 실행 ────────────────────────────
def run_bot():
    state = load_state()
    now = utcnow()
    now_ts = int(now.timestamp())
    tickers = fetch_market_data()
    if not tickers: return

    active = state["active_slots"]
    candidates = []

    for symbol, t in tickers.items():
        if not symbol.endswith("/USDT:USDT") and not symbol.endswith("/USDT"): continue
        try:
            last_price = float(t.get('last', 0))
            change = float(t.get('percentage', 0))
            volume = float(t.get('quoteVolume', 0))

            direction = "LONG" if change >= MIN_CHANGE_PCT else ("SHORT" if change <= -MIN_CHANGE_PCT else None)
            if not direction or symbol in active or last_price <= 0 or volume < MIN_QUOTE_VOLUME: continue
            candidates.append((volume, symbol, last_price, change, direction))
        except Exception:
            continue

    candidates.sort(reverse=True)
    for volume, symbol, last_price, change, direction in candidates[:MAX_CANDIDATES]:
        if len(active) >= MAX_SLOTS: break
        try:
            analysis = analyze_signal(symbol, direction, now_ts)
            if not analysis: continue

            atr = analysis["atr"]
            risk = atr * ATR_SL
            sign = 1 if direction == "LONG" else -1
            tp1, tp2, tp3 = (last_price + sign * atr * m for m in ATR_TP_MULTS)
            sl = last_price - sign * risk

            if (risk / last_price) * 100 > MAX_SL_PCT: continue

            active[symbol] = {
                "direction": direction, "entry_price": last_price, "entry_time": now.isoformat(),
                "leverage": 7, "risk": risk, "atr": atr, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "stop_loss": sl, "tp1_reached": False, "tp2_reached": False
            }

            msg = (
                f"🛡️ <b>[BTCC 최적화 시그널]</b> | {dir_tag(direction)}\n"
                f"<a href='{chart_link(symbol)}'>{symbol}</a>\n\n"
                f"진입가: <code>{fmt_price(last_price)}</code> | 변동률: <code>{change:+.2f}%</code>\n"
                f"TP1: <code>{fmt_price(tp1)}</code> | TP2: <code>{fmt_price(tp2)}</code> | TP3: <code>{fmt_price(tp3)}</code>\n"
                f"손절(SL): <code>{fmt_price(sl)}</code>"
            )
            send_telegram_message(msg)
        except Exception as e:
            print(f"Error evaluating {symbol}: {e}")
            continue

    save_state(state)

if __name__ == "__main__":
    run_bot()
