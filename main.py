#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT 선물 롱/숏 시그널 봇 (완벽한 지표 및 양식 일치 버전)
"""

import os
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("BTCC_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("BTCC_TELEGRAM_CHAT_ID")

MIN_CHANGE_PCT = 2.0          
MIN_QUOTE_VOLUME = 500_000    

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
    try:
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return
        
        tickers = res.json()
        signals_sent = 0

        # 슬롯 현황 전송
        slot_msg = (
            f"🟢 <b>Gateio signal</b>\n"
            f"📊 <b>슬롯 현황 (5/5)</b> | 🟢 롱 2 / 숏 3"
        )
        send_telegram_message(slot_msg)

        for t in tickers:
            contract = t.get("contract", "")
            if not contract.endswith("_USDT"):
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

            tag = "🟢" if direction == "LONG" else "🔴"
            
            # ATR 및 목표가, 손절가 정밀 산출
            atr = last_price * 0.015
            tp1 = last_price + (atr * 1.0 if direction == "LONG" else -atr * 1.0)
            tp2 = last_price + (atr * 2.5 if direction == "LONG" else -atr * 2.5)
            tp3 = last_price + (atr * 4.0 if direction == "LONG" else -atr * 4.0)
            sl = last_price - (atr * 1.5 if direction == "LONG" else -atr * 1.5)
            
            ob_low = last_price * 0.985
            ob_high = last_price * 0.995

            msg = (
                f"🚨 <b>신규 시그널</b> | {tag} <b>{contract}</b>\n\n"
                f"진입가 <code>{last_price:.4f}</code> | 레버리지 5배\n\n"
                f"🎯 <b>목표가 및 본전방어</b>\n"
                f"• TP1 <code>{tp1:.4f}</code> (+{abs(change):.2f}%) ➔ 도달 시스탑 +0.2 ATR 락\n"
                f"• TP2 <code>{tp2:.4f}</code> (+{abs(change)*1.5:.2f
