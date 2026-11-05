#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT 선물 롱/숏 시그널 봇 (슬롯 초기화 및 신규 재탐색 버전)
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

# 💡 True로 설정하고 실행하면 기존 슬롯이 모두 초기화되고 새로운 종목으로 다시 채워집니다.
FORCE_RESET = True 

def load_state():
    if FORCE_RESET or not os.path.exists(STATE_FILE):
        return {
            "active_slots": {},
            "stats": {"wins": 0, "draws": 0, "losses": 1, "pf": 0.00, "avg_r": -1.03, "last_r": -1.0}
        }
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "active_slots": {},
            "stats": {"wins": 0, "draws": 0, "losses": 1, "pf": 0.00, "avg_r": -1.03, "last_r": -1.0}
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
    stats = state.get("stats", {"wins": 0, "draws": 0, "losses": 1, "pf": 0.00, "avg_r": -1.03, "last_r": -1.0})

    try:
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return
        
        tickers = res.json()
        newly_added = []

        # 빈 슬롯이 있으면 새로운 종목으로 채우기
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

            asset_type = "주식" if contract.replace("_USDT", "") in ["TSLA", "INTC", "NVDA", "AAPL"] else "코인"
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
                "pnl_pct": abs(change) * 0.3,
                "
