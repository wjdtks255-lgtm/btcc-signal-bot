#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT 선물 롱/숏 시그널 봇 (HTTP 공개 API 기반 - IP 제한 우회 버전)
- 텔레그램 알림 전용
"""

import os
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("BTCC_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("BTCC_TELEGRAM_CHAT_ID")

MIN_CHANGE_PCT = 2.0  # 변동률 조건 (테스트를 위해 잠시 2%로 완화)

def utcnow():
    return datetime.now(timezone.utc)

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
    print("Fetching public market data...")
    try:
        # 바이낸스 퍼블릭 24hr 틱 데이터를 HTTP로 직접 조회 (IP 제한 없음)
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            print(f"API failed with status {res.status_code}")
            return
        
        tickers = res.json()
        signals_sent = 0

        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            
            last_price = float(t.get("lastPrice", 0))
            change = float(t.get("priceChangePercent", 0))
            volume = float(t.get("quoteVolume", 0))

            if volume < 1_000_000:  # 최소 거래대금 100만 USDT 이상
                continue

            direction = None
            if change >= MIN_CHANGE_PCT:
                direction = "LONG"
            elif change <= -MIN_CHANGE_PCT:
                direction = "SHORT"
            else:
                continue

            tag = "🟢 롱" if direction == "LONG" else "🔴 숏"
            chart_url = f"https://www.binance.com/en/futures/{symbol}"

            msg = (
                f"🛡️ <b>[실시간 선물 급등락 시그널]</b> | {tag}\n"
                f"<a href='{chart_url}'>{symbol}</a>\n\n"
                f"현재가: <code>{last_price}</code>\n"
                f"24시간 변동률: <code>{change:+.2f}%</code>\n"
                f"24시간 거래대금: <code>{int(volume):,} USDT</code>"
            )

            # 테스트 및 과도한 알림 방지를 위해 상위 3개만 발송
            if send_telegram_message(msg):
                signals_sent += 1
                if signals_sent >= 3:
                    break

        print(f"Bot executed successfully. Sent {signals_sent} signals.")

    except Exception as e:
        print(f"Error in run_bot: {e}")

if __name__ == "__main__":
    run_bot()
