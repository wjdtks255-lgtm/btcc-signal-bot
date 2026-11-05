#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTCC 선물 롱/숏 시그널 봇 (BTCC 종목 및 양식 완벽 반영 버전)
"""

import os
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("BTCC_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("BTCC_TELEGRAM_CHAT_ID")

MIN_CHANGE_PCT = 2.0          # 24시간 변동률 기준 (%)
MIN_QUOTE_VOLUME = 500_000    # 최소 거래대금 (USDT)

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

def chart_link(clean_sym):
    return f"https://www.btcc.com/en-US/trade/futures/perpetual/{clean_sym}USDT"

def run_bot():
    try:
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return
        
        tickers = res.json()
        signals_sent = 0

        for t in tickers:
            contract = t.get("contract", "")
            if not contract.endswith("_USDT"):
                continue
            
            # BTCC에서 사용하는 정식 종목명 형태로 변환 (예: BTC)
            clean_sym = contract.replace("_USDT", "")
            
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

            tag = "🟢 롱 (LONG)" if direction == "LONG" else "🔴 숏 (SHORT)"
            
            # ATR 및 목표가 / 손절가 산출
            approx_atr = last_price * 0.015
            tp1 = last_price + (approx_atr * 1.0 if direction == "LONG" else -approx_atr * 1.0)
            tp2 = last_price + (approx_atr * 2.5 if direction == "LONG" else -approx_atr * 2.5)
            tp3 = last_price + (approx_atr * 4.0 if direction == "LONG" else -approx_atr * 4.0)
            sl = last_price - (approx_atr * 1.5 if direction == "LONG" else -approx_atr * 1.5)

            msg = (
                f"🛡️ <b>[BTCC 선물 시그널]</b> | {tag}\n"
                f"🪙 종목: <a href='{chart_link(clean_sym)}'><b>{clean_sym}/USDT</b></a>\n\n"
                f"📌 <b>진입 정보</b>\n"
                f"• 진입가: <code>{last_price}</code>\n"
                f"• 24시간 변동률: <code>{change:+.2f}%</code>\n"
                f"• 24시간 거래대금: <code>{int(volume):,} USDT</code>\n\n"
                f"🎯 <b>목표가 및 손절가 (ATR 기준)</b>\n"
                f"• TP1: <code>{tp1:.4f}</code> (도달 시 본전 락)\n"
                f"• TP2: <code>{tp2:.4f}</code>\n"
                f"• TP3: <code>{tp3:.4f}</code>\n"
                f"• 손절(SL): <code>{sl:.4f}</code>"
            )

            if send_telegram_message(msg):
                signals_sent += 1
                if signals_sent >= 3:
                    break

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_bot()
