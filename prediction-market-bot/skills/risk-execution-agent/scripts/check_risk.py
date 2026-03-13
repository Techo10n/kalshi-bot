"""
Step 1: Fetch portfolio state from Kalshi and check exposure limits.

Reads:  Kalshi API (GET /portfolio/balance, GET /portfolio/positions)
Writes: data/portfolio_state.json

Requires: KALSHI_ACCESS_KEY, KALSHI_PRIVATE_KEY_PATH env vars.
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
OUTPUT = DATA_DIR / "portfolio_state.json"

BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"
MAX_EXPOSURE_RATIO = 0.20


def load_private_key(key_path: str):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        raise ImportError("cryptography not installed. Run: pip install cryptography")

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


def kalshi_get(path: str, access_key: str, private_key, dry_run: bool = False) -> dict:
    if dry_run:
        logger.info(f"[DRY RUN] GET {path}")
        return {}

    headers = sign_request(access_key, private_key, "GET", path)
    url = BASE_URL + path
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 200:
        return resp.json()
    logger.error(f"Kalshi GET {path} → {resp.status_code}: {resp.text[:200]}")
    return {}


def check_risk(dry_run: bool = False) -> dict:
    access_key = os.environ.get("KALSHI_ACCESS_KEY")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")

    if not access_key or not key_path:
        if dry_run:
            logger.info("[DRY RUN] No Kalshi creds — using mock portfolio state")
            state = {
                "available_balance": 1000.0,
                "portfolio_value": 1000.0,
                "total_exposure": 0.0,
                "exposure_ratio": 0.0,
                "blocked": False,
                "block_reason": None,
                "dry_run": True,
            }
            OUTPUT.write_text(json.dumps(state, indent=2))
            return state
        logger.error("Missing KALSHI_ACCESS_KEY or KALSHI_PRIVATE_KEY_PATH")
        raise EnvironmentError("Kalshi credentials required")

    private_key = load_private_key(key_path)

    balance_data = kalshi_get("/portfolio/balance", access_key, private_key, dry_run)
    positions_data = kalshi_get("/portfolio/positions", access_key, private_key, dry_run)

    # Convert from cents to dollars
    available_balance = balance_data.get("balance", 0) / 100.0
    portfolio_value = balance_data.get("portfolio_value", available_balance * 100) / 100.0

    positions = positions_data.get("market_positions", [])
    total_exposure = sum(
        abs(p.get("market_exposure", 0)) for p in positions
    ) / 100.0

    exposure_ratio = total_exposure / max(portfolio_value, 0.01)
    blocked = exposure_ratio > MAX_EXPOSURE_RATIO
    block_reason = (
        f"Exposure ratio {exposure_ratio:.1%} > {MAX_EXPOSURE_RATIO:.0%} limit"
        if blocked else None
    )

    if blocked:
        logger.warning(f"TRADE BLOCKED: {block_reason}")

    state = {
        "available_balance": round(available_balance, 2),
        "portfolio_value": round(portfolio_value, 2),
        "total_exposure": round(total_exposure, 2),
        "exposure_ratio": round(exposure_ratio, 4),
        "open_positions": len(positions),
        "blocked": blocked,
        "block_reason": block_reason,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dry_run": dry_run,
    }

    logger.info(
        f"Portfolio: balance=${available_balance:.2f} "
        f"exposure=${total_exposure:.2f} ({exposure_ratio:.1%}) "
        f"blocked={blocked}"
    )

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(state, indent=2))
    return state


def main(dry_run: bool = False):
    return check_risk(dry_run=dry_run)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = main(dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
