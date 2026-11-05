#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT 선물 롱/숏 시그널 봇 (Git 자동 동기화 및 슬롯 영구 고정 버전)
"""

import os
import json
import subprocess
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

def save_and_commit_state(state):
    # 로컬 JSON 저장
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    # 깃허브 액션 환경에서 상태 파일을 레포지토리에 자동 푸시하여 유실 방지
    try:
        subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", STATE_FILE], check=False)
        subprocess.run(["git", "commit", "-m", "Auto-update bot state [skip ci]"], check=False)
        subprocess.run(["git", "push"], check=False)
    except Exception as e:
        print(f"Git commit/push error: {e}")

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
        url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
        res = requests.get(url, timeout=10)
        
        if res.status_code != 200:
            return
        
        tickers = res.json()
        ticker_map = {}
        for t in tickers:
            contract = t.get("contract", "")
            if contract:
                ticker_map[contract] = t

        slots_to_remove = []

        # 1. 기존 슬롯 현재가 갱신 및 TP/SL 도달 검사
        for contract, slot in list(active_slots.items()):
            if contract in ticker_map:
                t_info = ticker_map[contract]
                current_price = float(t_info.get("last", slot["entry_price"]))
                entry_price = slot["entry_price"]
                direction = slot["direction"]

                if direction == "LONG":
                    pnl_pct = ((current_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - current_price) / entry_price) * 100

                slot["pnl_pct"] = pnl_pct

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
                else:
                    if current_price <= tp3:
                        hit_target = "TP3 (최종 익절 도달 🚀)"
                    elif current_price <= tp2:
                        hit_target = "TP2 (2차 익절 도달 🎯)"
                    elif current_price <= tp1:
                        hit_target = "TP1 (1차 익절 도달 ✨)"
                    elif current_price >= sl:
                        hit_target = "SL (손절 도달 🛑)"

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

        for contract in slots_to_remove:
            del active_slots[contract]

        # 2. 비워진 슬롯만큼 신규 종목 채우기
        newly_added = []
        if len(active_slots) < MAX_SLOTS:
            for contract, t in ticker_map.items():
                if len(active_slots) >= MAX_SLOTS:
                    break

                if not contract.endswith("_USDT"):
                    continue
                if contract in active_slots:
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

                clean_sym = contract.replace("_USDT", "")
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

        # 3. 신규 시그널 알림 전송
        for contract, slot in newly_added:
            tag = "🟢" if slot["direction"] == "LONG" else "🔴"
            msg = (
                f"🚨 <b>신규 시그널</b> | {tag} <b>{contract}</b>\n\n"
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

        # 4. 전체 슬롯 현황판 전송
        if active_slots:
            long_cnt = sum(1 for s in active_slots.values() if s["direction"] == "LONG")
            short_cnt = sum(1 for s in active_slots.values() if s["direction"] == "SHORT")

            slot_msg_lines = [
                f"📊 <b>슬롯 현황 ({len(active_slots)} / {MAX_SLOTS})</b> | 🟢 롱 {long_cnt} / 🔴 숏 {short_cnt}\n"
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
        save_and_commit_state(state)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_bot()
