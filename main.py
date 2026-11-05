#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT 선물 롱/숏 시그널 봇 (클라우드 IP 차단 우회 버전)
- 텔레그램 알림 전용
"""

import os
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("BTCC_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("BTCC_TELEGRAM_CHAT_ID")

MIN_CHANGE_PCT = 2.0  # 24시간 변동률 기준 (%)

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
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def run_bot():
    print("Fetching market data...")
    try:
        # IP 제한 없는 글로벌 공개 선물 API 호출
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            print(f"API failed with status {res.status_code}")
            return
        
        tickers = res.json()
        signals_sent = 0

        for t in tickers:
            contract = t.get("contract", "")
            if not contract.endswith("_USDT"):
                continue
            
            last_price = float(t.get("last", 0))
            change = float(t.get("change_percentage", 0))
            volume = float(t.get("volume_24h_quote", 0))

            if volume < 500_000:  # 최소 거래대금 50만 USDT 이상
                continue

            direction = None
            if change >= MIN_CHANGE_PCT:
                direction = "LONG"
            elif change <= -MIN_CHANGE_PCT:
                direction = "SHORT"
            else:
                continue

            tag = "🟢 롱" if direction == "LONG" else "🔴 숏"
            chart_url = f"https://www.gate.io/futures/USDT/{contract}"

            msg = (
                f"🛡️ <b>[선물 급등락 시그널]</b> | {tag}\n"
                f"<a href='{chart_url}'>{contract}</a>\n\n"
                f"현재가: <code>{last_price}</code>\n"
                f"24시간 변동률: <code>{change:+.2f}%</code>\n"
                f"24시간 거래대금: <code>{int(volume):,} USDT</code>"
            )

            if send_telegram_message(msg):
                signals_sent += 1
                if signals_sent >= 3:  # 테스트 및 과도한 알림 방지용 제한
                    break

        print(f"Bot executed successfully. Sent {signals_sent} signals.")

    except Exception as e:
        print(f"Error in run_bot: {e}")

if __name__ == "__main__":
    run_bot()
