"""
main.py -- listens for new Pump.fun launches in real time (PumpPortal
WebSocket, confirmed free/real per official docs and multiple independent
examples), logs every launch, periodically re-checks 24h+-old launches for
liquidity to build real per-deployer death-rate history, and alerts on
Telegram when a launch comes from a known serial-rugger deployer.

This is WATCH-ONLY / DATA-COLLECTION for now -- it places no trades. The
goal is the same as the weather bot's first phase: build a real, verified
dataset before trusting any filter enough to act on it.
"""

import asyncio
import json
import os
import time
import requests
import websockets

import journal
import dexscreener_client as dex

PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
LIQUIDITY_CHECK_INTERVAL_MINUTES = 30
MIN_HISTORY_FOR_CLASSIFICATION = 3

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_telegram(text: str, timeout: int = 10) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] Not configured -- printing instead:")
        print(text)
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=timeout,
        )
        return resp.ok
    except requests.RequestException as e:
        print(f"[telegram] Send failed: {e}")
        return False


def handle_new_token_message(msg: dict):
    """
    Called for every subscribeNewToken event. Real PumpPortal payload
    includes at minimum: mint, name, symbol, and the creator/deployer
    address (field name confirmed as part of the creation event schema
    per official examples -- verify exact key name against a live message
    on first real run, since PumpPortal's docs show the general shape but
    this sandbox can't confirm the literal field name against a live feed).
    """
    mint = msg.get("mint")
    deployer = msg.get("traderPublicKey") or msg.get("creator")
    name = msg.get("name", "")
    symbol = msg.get("symbol", "")
    initial_liq = msg.get("vSolInBondingCurve")  # rough proxy at launch time

    if not mint or not deployer:
        print(f"  [listener] Skipping malformed message (missing mint/deployer): {msg}")
        return

    classification = journal.classify_deployer(deployer)
    stats = journal.deployer_stats(deployer)

    print(f"  New launch: {symbol} ({mint[:8]}...) by {deployer[:8]}... "
          f"[deployer classification: {classification}, history: {stats['checked']} checked, "
          f"death_rate: {stats['death_rate']}]")

    journal.log_launch(mint, deployer, name, symbol, initial_liq)

    if classification == "serial_rugger":
        alert = (
            f"\u26a0\ufe0f SERIAL RUGGER LAUNCH DETECTED\n"
            f"Token: {name} ({symbol})\n"
            f"Mint: {mint}\n"
            f"Deployer: {deployer}\n"
            f"Deployer history: {stats['checked']} launches, "
            f"{stats['death_rate']:.0%} death rate\n"
            f"-- watch-only, no trade placed. Logged for tracking."
        )
        send_telegram(alert)


async def listen_for_launches():
    """Long-running WebSocket listener. Reconnects with backoff on failure --
    important per PumpPortal's own docs: don't hammer new connections, reuse
    one persistent connection."""
    backoff = 1
    while True:
        try:
            async with websockets.connect(PUMPPORTAL_WS) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print("[listener] Connected and subscribed to new token launches.")
                backoff = 1  # reset on successful connect
                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                        handle_new_token_message(msg)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"[listener] Connection error: {e} -- reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def run_liquidity_check_cycle():
    """Call periodically (separate from the WebSocket loop) to re-check
    24h+-old launches and update deployer death-rate history."""
    pending = journal.get_pending_checks(min_age_hours=24.0)
    print(f"[liquidity-check] {len(pending)} launches ready for re-check.")
    for row in pending:
        liq = dex.get_current_liquidity_usd(row["mint"])
        if liq is None:
            continue  # fetch failed, try again next cycle
        journal.mark_checked(row["mint"], liq)
        print(f"  {row['symbol']} ({row['mint'][:8]}...): "
              f"${liq:.0f} liquidity -> {'DEAD' if liq < 1000 else 'ALIVE'}")


async def periodic_liquidity_checker():
    while True:
        try:
            run_liquidity_check_cycle()
        except Exception as e:
            print(f"[liquidity-check] Unexpected error: {e}")
        await asyncio.sleep(LIQUIDITY_CHECK_INTERVAL_MINUTES * 60)


async def main():
    print("=" * 50)
    print("Deployer Reputation Tracker -- starting")
    print(f"Journal path: {os.path.abspath(journal.JOURNAL_PATH)}")
    print("=" * 50)
    await asyncio.gather(
        listen_for_launches(),
        periodic_liquidity_checker(),
    )


if __name__ == "__main__":
    asyncio.run(main())
