"""
Step 1: Compare filled orders against Kalshi settlements. Compute PnL.

Reads:  data/execution_log.json, Kalshi GET /portfolio/settlements
Writes: data/historical_results.json (appends new settled outcomes)

Returns list of newly settled trade records.
"""

import base64
import json
import logging
import os
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
EXEC_LOG = DATA_DIR / "execution_log.json"
HISTORICAL = DATA_DIR / "historical_results.json"

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


def fetch_settlements(access_key: str, private_key, limit: int = 100) -> list:
    path = f"/portfolio/settlements?limit={limit}"
    headers = sign_request(access_key, private_key, "GET", path)
    try:
        resp = requests.get(BASE_URL + path, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("settlements", [])
        logger.warning(f"Settlements API → {resp.status_code}: {resp.text[:200]}")
    except requests.RequestException as e:
        logger.warning(f"Request error fetching settlements: {e}")
    return []


def load_historical() -> list:
    if HISTORICAL.exists():
        return json.loads(HISTORICAL.read_text())
    return []


def save_historical(records: list):
    DATA_DIR.mkdir(exist_ok=True)
    HISTORICAL.write_text(json.dumps(records, indent=2))


def classify_trade(pnl: float) -> str:
    if pnl > 1.0:
        return "WIN"
    if pnl < -1.0:
        return "LOSS"
    return "SCRATCH"


def detect_losses(dry_run: bool = False) -> list:
    if not EXEC_LOG.exists():
        logger.info("execution_log.json not found — no trades to review")
        return []

    exec_log = json.loads(EXEC_LOG.read_text())
    filled_orders = [e for e in exec_log if e.get("status") in ("filled", "partial")]

    if not filled_orders:
        logger.info("No filled orders found")
        return []

    historical = load_historical()
    already_settled = {r["ticker"] for r in historical}

    # Filter to orders not yet in historical
    new_orders = [o for o in filled_orders if o.get("ticker") not in already_settled]
    if not new_orders:
        logger.info("All filled orders already in historical_results.json")
        return []

    if dry_run:
        logger.info(f"[DRY RUN] Would check {len(new_orders)} filled orders for settlement")
        return []

    access_key = os.environ.get("KALSHI_ACCESS_KEY")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not access_key or not key_path:
        logger.error("Missing Kalshi credentials — cannot fetch settlements")
        return []

    private_key = load_private_key(key_path)
    settlements = fetch_settlements(access_key, private_key)
    settlement_index = {s["ticker"]: s for s in settlements}

    new_records = []
    for order in new_orders:
        ticker = order.get("ticker")
        settlement = settlement_index.get(ticker)

        if not settlement:
            logger.info(f"  {ticker}: not yet settled")
            continue

        # Determine outcome (1=YES resolved, 0=NO resolved)
        result = settlement.get("result", "no").lower()
        outcome = 1 if result == "yes" else 0

        # Compute PnL
        entry_price_cents = order.get("price_cents", 50)
        entry_price = entry_price_cents / 100.0
        filled_count = order.get("filled_count", order.get("contracts", 0))
        fees = order.get("fees_paid", 0.0)
        side = order.get("side", "yes")

        if side == "yes":
            pnl = (outcome - entry_price) * filled_count - fees
        else:
            # NO bet: wins when outcome = 0
            pnl = ((1 - outcome) - (1.0 - entry_price)) * filled_count - fees

        classification = classify_trade(pnl)
        logger.info(
            f"  {ticker}: outcome={result.upper()} pnl=${pnl:.2f} → {classification}"
        )

        record = {
            "ticker": ticker,
            "signal": "BUY_YES" if side == "yes" else "BUY_NO",
            "entry_price": entry_price,
            "final_probability": None,  # filled by prediction-agent records if available
            "confidence": None,
            "sentiment_scores": {},
            "feature_vector": {},
            "outcome": outcome,
            "pnl": round(pnl, 4),
            "classification": classification,
            "settled_at": settlement.get("settled_time", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            "failure_mode": None,
            "failure_explanation": None,
        }
        new_records.append(record)

    if new_records:
        historical.extend(new_records)
        save_historical(historical)
        logger.info(f"Appended {len(new_records)} records to historical_results.json")
    else:
        logger.info("No new settled trades found")

    return new_records


def main(dry_run: bool = False):
    return detect_losses(dry_run=dry_run)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    records = main(dry_run=args.dry_run)
    wins = sum(1 for r in records if r["classification"] == "WIN")
    losses = sum(1 for r in records if r["classification"] == "LOSS")
    print(f"\nSettled today: {len(records)} | Wins: {wins} | Losses: {losses}")
