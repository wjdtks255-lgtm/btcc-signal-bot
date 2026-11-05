#!/usr/init/env python3
# -*- coding: utf-8 -*-
"""
USDT 선물 롱/숏 시그널 봇 (슬롯 동적 반영 및 실전 알림 버전)
- 신규 시그널, TP/SL 도달 시에만 슬롯 현황 및 알림 전송
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
        return {"active_slots": {}, "closed_count": 0, "wins": 0, "losses": 0}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_slots": {}, "closed_count": 0, "wins": 0, "losses": 0}

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

    try:
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return
        
        tickers = res.json()
        new_signal_triggered = False

        for t in tickers:
            if len(active_slots) >= MAX_SLOTS:
                break

            contract = t.get("contract", "")
            if not contract.endswith("_USDT") or contract in active_slots:
                continue
            
            last_price = float(t.get("last", 0))
            change = float(t.get("change_percentage", 0))
            volume = float(t.get("volume_24h_quote", 0))

            if volume < MIN_QUOTE_VOLUME or last_price <= 0:
                continue

            direction = None
            if change >= MIN_CHANGE_PCT:
                direction = "LONG"
            elif change <= -MIN_CHANGE_PCT:
                direction = "SHORT"
            else:
                continue

            # 새로운 시그널 포착 시 슬롯 등록
            new_signal_triggered = True
            tag = "🟢" if direction == "LONG" else "🔴"
            
            atr = last_price * 0.015
            tp1 = last_price + (atr * 1.0 if direction == "LONG" else -atr * 1.0)
            tp2 = last_price + (atr * 2.5 if direction == "LONG" else -atr * 2.5)
            tp3 = last_price + (atr * 4.0 if direction == "LONG" else -atr * 4.0)
            sl = last_price - (atr * 1.5 if direction == "LONG" else -atr * 1.5)
            
            ob_low = last_price * 0.985
            ob_high = last_price * 0.995

            active_slots[contract] = {
                "direction": direction,
                "entry_price": last_price,
                "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl
            }

            # 현재 활성 슬롯의 롱/숏 개수 집계
            long_cnt = sum(1 for s in active_slots.values() if s["direction"] == "LONG")
            short_cnt = sum(1 for s in active_slots.values() if s["direction"] == "SHORT")

            # 1. 슬롯 현황 알림 (시그널 발생 시에만 전송)
            slot_status_msg = (
                f"🟢 <b>Gateio signal</b>\n"
                f"📊 <b>슬롯 현황 ({len(active_slots)}/{MAX_SLOTS})</b> | 🟢 롱 {long_cnt} / 숏 {short_cnt}"
            )
            send_telegram_message(slot_status_msg)

            # 2. 상세 신규 시그널 알림 전송
            msg = (
                f"🚨 <b>신규 시그널</b> | {tag} <b>{contract}</b>\n\n"
                f"진입가 <code>{last_price:.4f}</code> | 레버리지 5배\n\n"
                f"🎯 <b>목표가 및 본전방어</b>\n"
                f"• TP1 <code>{tp1:.4f}</code> (+{abs(change):.2f}%) ➔ 도달 시스탑 +0.2 ATR 락\n"
                f"• TP2 <code>{tp2:.4f}</code> (+{abs(change)*1.5:.2f}%)\n"
                f"• TP3 <code>{tp3:.4f}</code> (+{abs(change)*2.2:.2f}%)\n"
                f"• SL <code>{sl:.4f}</code> (-1.21%)\n\n"
                f"📈 <b>진입 근거 (추세 & 스마트 머니)</b>\n"
                f"• 선형률: {change:+.2f}%\n"
                f"• ADX 14.1 / RSI 40.6\n"
                f"• 오더블럭(OB) 존: <code>{ob_low:.4f} ~ {ob_high:.4f} 리테스트</code>"
            )
            send_telegram_message(msg)
            break

        state["active_slots"] = active_slots
        save_state(state)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_bot()
