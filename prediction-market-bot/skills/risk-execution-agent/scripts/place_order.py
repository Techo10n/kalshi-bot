"""
Step 3: Place limit orders on Kalshi for each sized position.

Reads:  data/sized_positions.json
Writes: data/execution_log.json (appends)

Requires: KALSHI_ACCESS_KEY, KALSHI_PRIVATE_KEY_PATH
"""

import base64
import json
import logging
import os
import random
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
EXEC_LOG = DATA_DIR / "execution_log.json"

BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"


def load_private_key(key_path: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    with open(key_path, "rb") as f:
        return serialization.load_pem_private_key(
            f.read(), password=None, backend=default_backend()
        )


def sign_request(access_key: str, private_key, method: str, path: str) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    timestamp_ms = str(int(time.time() * 1000))
    msg_string = timestamp_ms + method.upper() + path
    signature = private_key.sign(
        msg_string.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": access_key,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "Content-Type": "application/json",
    }


def place_order_api(access_key: str, private_key, payload: dict, max_retries: int = 3) -> dict:
    path = "/portfolio/orders"
    for attempt in range(max_retries):
        headers = sign_request(access_key, private_key, "POST", path)
        try:
            resp = requests.post(BASE_URL + path, headers=headers, json=payload, timeout=15)
        except requests.RequestException as e:
            logger.warning(f"Request error (attempt {attempt+1}): {e}")
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in (200, 201):
            return resp.json()
        elif resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2)) + random.uniform(0, 1)
            logger.warning(f"Rate limited — waiting {wait:.1f}s")
            time.sleep(wait)
        elif resp.status_code == 400:
            logger.error(f"Bad request for {payload.get('ticker')}: {resp.text[:300]}")
            return {"error": f"400: {resp.text[:200]}"}
        elif resp.status_code == 403:
            logger.error("Auth failed (403) — check KALSHI_ACCESS_KEY and private key")
            return {"error": "403 auth failed"}
        else:
            logger.warning(f"Order error {resp.status_code}: {resp.text[:200]}")
            return {"error": f"{resp.status_code}: {resp.text[:100]}"}
    return {"error": "max retries exceeded"}


def load_exec_log() -> list:
    if EXEC_LOG.exists():
        return json.loads(EXEC_LOG.read_text())
    return []


def save_exec_log(log: list):
    DATA_DIR.mkdir(exist_ok=True)
    EXEC_LOG.write_text(json.dumps(log, indent=2))


def place_orders(positions: list, dry_run: bool = False) -> list:
    access_key = os.environ.get("KALSHI_ACCESS_KEY")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")

    if not dry_run and (not access_key or not key_path):
        logger.error("Missing KALSHI_ACCESS_KEY or KALSHI_PRIVATE_KEY_PATH")
        raise EnvironmentError("Kalshi credentials required for live trading")

    private_key = None
    if not dry_run and key_path:
        private_key = load_private_key(key_path)

    exec_log = load_exec_log()
    new_entries = []

    for pos in positions:
        ticker = pos["ticker"]
        signal = pos["signal"]
        contracts = pos["contracts"]
        yes_price_cents = pos["yes_price_cents"]
        bet_size = pos["bet_size"]
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        client_order_id = f"bot-{ticker}-{int(time.time())}"

        side = "yes" if signal == "BUY_YES" else "no"
        payload = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "type": "limit",
            "count": contracts,
            "yes_price": yes_price_cents,
            "client_order_id": client_order_id,
        }

        if dry_run:
            logger.info(
                f"[DRY RUN] Would place: ticker={ticker} side={side} "
                f"contracts={contracts} price={yes_price_cents}¢ bet=${bet_size:.2f}"
            )
            entry = {
                "ticker": ticker,
                "order_id": f"dry-run-{ticker}",
                "client_order_id": client_order_id,
                "side": side,
                "contracts": contracts,
                "price_cents": yes_price_cents,
                "bet_size": bet_size,
                "timestamp": timestamp,
                "status": "dry_run",
            }
        else:
            logger.info(f"  Placing order: {ticker} {side} x{contracts} @ {yes_price_cents}¢")
            result = place_order_api(access_key, private_key, payload)

            if "error" in result:
                entry = {
                    "ticker": ticker,
                    "client_order_id": client_order_id,
                    "side": side,
                    "contracts": contracts,
                    "price_cents": yes_price_cents,
                    "bet_size": bet_size,
                    "timestamp": timestamp,
                    "status": "failed",
                    "error": result["error"],
                }
                logger.error(f"    Order failed: {result['error']}")
            else:
                order = result.get("order", result)
                entry = {
                    "ticker": ticker,
                    "order_id": order.get("order_id", ""),
                    "client_order_id": client_order_id,
                    "side": side,
                    "contracts": contracts,
                    "price_cents": yes_price_cents,
                    "bet_size": bet_size,
                    "timestamp": timestamp,
                    "status": "placed",
                }
                logger.info(f"    Order placed: id={entry['order_id']}")

        new_entries.append(entry)

    exec_log.extend(new_entries)
    save_exec_log(exec_log)
    logger.info(f"Execution log updated → {EXEC_LOG}")
    return new_entries


def main(dry_run: bool = False):
    p = DATA_DIR / "sized_positions.json"
    if not p.exists():
        logger.error("sized_positions.json not found — run size_position.py first")
        return []
    positions = json.loads(p.read_text())
    if not positions:
        logger.info("No positions to place")
        return []
    return place_orders(positions, dry_run=dry_run)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    entries = main(dry_run=args.dry_run)
    print(f"\nPlaced/logged {len(entries)} order entries")
