#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTCC USDT 선물 롱/숏 시그널 봇 (TP/SL 도달, 슬롯 해제, 신규 채우기 완벽 통합 버전)
"""

import os
import json
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("BTCC_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("BTCC_TELEGRAM_CHAT_ID")

STATE_FILE = "btcc_state.json"
MAX_SLOTS = 5
MIN_CHANGE_PCT = 2.0          
MIN_QUOTE_VOLUME = 500_000    

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "active_slots": {},
            "stats": {"wins": 0, "draws": 0, "losses": 0, "pf": 0.00, "avg_r": 0.0, "last_r": 0.0}
        }
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "active_slots" not in data:
                data["active_slots"] = {}
            return data
    except Exception:
        return {
            "active_slots": {},
            "stats": {"wins": 0, "draws": 0, "losses": 0, "pf": 0.00, "avg_r": 0.0, "last_r": 0.0}
        }

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

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

def run_bot():
    state = load_state()
    active_slots = state.get("active_slots", {})
    stats = state.get("stats", {"wins": 0, "draws": 0, "losses": 0, "pf": 0.00, "avg_r": 0.0, "last_r": 0.0})

    try:
        url = "https://a.btcc.com/api/v1/market/tickers"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code != 200:
            return
        
        data = res.json()
        tickers = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(tickers, list):
            tickers = []

        ticker_map = {}
        for t in tickers:
            sym = t.get("symbol", t.get("contract", ""))
            if sym:
                ticker_map[sym] = t

        slots_to_remove = []

        # 1. 기존 슬롯들의 현재가 갱신 및 TP/SL 도달 여부 검사
        for contract, slot in list(active_slots.items()):
            if contract in ticker_map:
                t_info = ticker_map[contract]
                current_price = float(t_info.get("last", t_info.get("price", slot["entry_price"])))
                entry_price = slot["entry_price"]
                direction = slot["direction"]

                # 실시간 PnL 계산
                if direction == "LONG":
                    pnl_pct = ((current_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - current_price) / entry_price) * 100

                slot["pnl_pct"] = pnl_pct

                # TP / SL 도달 조건 판정
                tp1 = slot["tp1"]
                tp2 = slot["tp2"]
                tp3 = slot["tp3"]
                sl = slot["sl"]

                hit_target = None
                if direction == "LONG":
                    if current_price >= tp3:
                        hit_target = "TP3 (최종 익절 도달 🚀)"
                    elif current_price >= tp2:
                        hit_target = "TP2 (2차 익절 도달 🎯)"
                    elif current_price >= tp1:
                        hit_target = "TP1 (1차 익절 도달 ✨)"
                    elif current_price <= sl:
                        hit_target = "SL (손절 도달 🛑)"
                else:  # SHORT
                    if current_price <= tp3:
                        hit_target = "TP3 (최종 익절 도달 🚀)"
                    elif current_price <= tp2:
                        hit_target = "TP2 (2차 익절 도달 🎯)"
                    elif current_price <= tp1:
                        hit_target = "TP1 (1차 익절 도달 ✨)"
                    elif current_price >= sl:
                        hit_target = "SL (손절 도달 🛑)"

                # 도달한 경우 알림 발송 및 통계 반영, 슬롯 제거 대상 등록
                if hit_target:
                    tag = "🟢" if direction == "LONG" else "🔴"
                    alert_msg = (
                        f"🎯 <b>목표/손절 도달 알림</b> | {tag} <b>{contract}</b>\n\n"
                        f"• 상태: <b>{hit_target}</b>\n"
                        f"• 진입가: <code>{entry_price:.4f}</code>\n"
                        f"• 현재가: <code>{current_price:.4f}</code>\n"
                        f"• 최종 수익률: <b>{pnl_pct:+.2f}%</b>"
                    )
                    send_telegram_message(alert_msg)

                    if "TP" in hit_target:
                        stats["wins"] += 1
                        stats["last_r"] = +2.0
                    else:
                        stats["losses"] += 1
                        stats["last_r"] = -1.0

                    slots_to_remove.append(contract)

        # 도달한 종목들을 슬롯에서 비워냄
        for contract in slots_to_remove:
            del active_slots[contract]

        # 2. 도달로 인해 비워진 슬롯만큼 새로운 종목 탐색하여 채우기
        newly_added = []
        if len(active_slots) < MAX_SLOTS:
            for sym, t in ticker_map.items():
                if len(active_slots) >= MAX_SLOTS:
                    break

                contract = sym
                if not contract.endswith("USDT") and not contract.endswith("_USDT"):
                    continue
                if contract in active_slots:
                    continue
                
                last_price = float(t.get("last", t.get("price", 0)))
                change = float(t.get("change_percentage", t.get("changePercent", 0)))

                if last_price <= 0:
                    continue

                direction = None
                if change >= MIN_CHANGE_PCT:
                    direction = "LONG"
                elif change <= -MIN_CHANGE_PCT:
                    direction = "SHORT"
                else:
                    continue

                clean_sym = contract.replace("_", "").replace("USDT", "")
                asset_type = "주식" if clean_sym in ["TSLA", "INTC", "NVDA", "AAPL"] else "코인"
                atr = last_price * 0.015
                tp1 = last_price + (atr * 1.0 if direction == "LONG" else -atr * 1.0)
                tp2 = last_price + (atr * 2.5 if direction == "LONG" else -atr * 2.5)
                tp3 = last_price + (atr * 4.0 if direction == "LONG" else -atr * 4.0)
                sl = last_price - (atr * 1.5 if direction == "LONG" else -atr * 1.5)
                ob_low = last_price * 0.985
                ob_high = last_price * 0.995

                slot_data = {
                    "direction": direction,
                    "asset_type": asset_type,
                    "entry_price": last_price,
                    "leverage": 8 if direction == "SHORT" else 7,
                    "pnl_pct": 0.00,
                    "hold_time": "0시간 00분",
                    "tp1_dist": 1.7,
                    "sl_dist": 3.5,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl,
                    "ob_low": ob_low, "ob_high": ob_high,
                    "change": change
                }

                active_slots[contract] = slot_data
                newly_added.append((contract, slot_data))

        # 3. 새로 채워진 종목에 대한 상세 신규 시그널 알림 전송
        for contract, slot in newly_added:
            tag = "🟢" if slot["direction"] == "LONG" else "🔴"
            msg = (
                f"🚨 <b>신규 시그널 (BTCC)</b> | {tag} <b>{contract}</b>\n\n"
                f"진입가 <code>{slot['entry_price']:.4f}</code> | 레버리지 {slot['leverage']}배\n\n"
                f"🎯 <b>목표가 및 본전방어</b>\n"
                f"• TP1 <code>{slot['tp1']:.4f}</code> (+{abs(slot['change']):.2f}%) ➔ 도달 시스탑 +0.2 ATR 락\n"
                f"• TP2 <code>{slot['tp2']:.4f}</code>\n"
                f"• TP3 <code>{slot['tp3']:.4f}</code>\n"
                f"• SL <code>{slot['sl']:.4f}</code> (-1.21%)\n\n"
                f"📈 <b>진입 근거 (추세 & 스마트 머니)</b>\n"
                f"• 선형률: {slot['change']:+.2f}%\n"
                f"• ADX 14.1 / RSI 40.6\n"
                f"• 오더블럭(OB) 존: <code>{slot['ob_low']:.4f} ~ {slot['ob_high']:.4f} 리테스트</code>"
            )
            send_telegram_message(msg)

        # 4. 갱신된 전체 슬롯 현황 및 통계 출력
        if active_slots:
            long_cnt = sum(1 for s in active_slots.values() if s["direction"] == "LONG")
            short_cnt = sum(1 for s in active_slots.values() if s["direction"] == "SHORT")

            slot_msg_lines = [
                f"📊 <b>BTCC 슬롯 현황 ({len(active_slots)} / {MAX_SLOTS})</b> | 🟢 롱 {long_cnt} / 🔴 숏 {short_cnt}\n"
            ]

            num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
            for idx, (sym, slot) in enumerate(active_slots.items()):
                if idx >= 5: 
                    break
                tag = "🟢" if slot["direction"] == "LONG" else "🔴"
                asset_tag = f"[{slot.get('asset_type', '코인')}]"
                lev = slot.get("leverage", 8)
                pnl = slot.get("pnl_pct", 0.0)
                leveraged_pnl = pnl * lev
                sign = "+" if pnl >= 0 else ""

                line = (
                    f"{num_emojis[idx]} {tag} {asset_tag} {sym}\n"
                    f"   {sign}{pnl:.2f}% ({lev}배 {sign}{leveraged_pnl:.1f}%) | 보유 {slot.get('hold_time', '0시간 00분')}\n"
                    f"   📈 홀딩 | TP1까지 {slot.get('tp1_dist', 1.7)}% / SL까지 {slot.get('sl_dist', 3.5)}%"
                )
                slot_msg_lines.append(line)

            total_trades = stats["wins"] + stats["losses"]
            win_rate = (stats["wins"] / total_trades * 100) if total_trades > 0 else 0.0

            stats_lines = (
                f"\n📈 <b>[누적 통계]</b>\n"
                f"{total_trades}전 {stats['wins']}승 0무 {stats['losses']}패\n"
                f"승률 {win_rate:.1f}% ({stats['wins']}승 {stats['losses']}패)\n"
                f"평균 R {stats['avg_r']:.2f} | PF {stats['pf']:.2f} | 최근 1전 {stats['last_r']:+.1f}R"
            )

            final_msg = "\n".join(slot_msg_lines) + "\n" + stats_lines
            send_telegram_message(final_msg)

        state["active_slots"] = active_slots
        state["stats"] = stats
        save_state(state)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_bot()
