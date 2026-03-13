"""
Step 4: Poll order fill status, cancel unfilled orders after 10 minutes.

Reads:  data/execution_log.json
Writes: data/execution_log.json (updates status fields)

Requires: KALSHI_ACCESS_KEY, KALSHI_PRIVATE_KEY_PATH
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

BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"
POLL_INTERVAL_SECONDS = 60
MAX_WAIT_SECONDS = 600  # 10 minutes


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


def get_order_status(order_id: str, access_key: str, private_key) -> dict:
    path = f"/portfolio/orders/{order_id}"
    headers = sign_request(access_key, private_key, "GET", path)
    try:
        resp = requests.get(BASE_URL + path, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("order", {})
        logger.warning(f"GET order {order_id} → {resp.status_code}")
    except requests.RequestException as e:
        logger.warning(f"Request error checking order {order_id}: {e}")
    return {}


def cancel_order(order_id: str, access_key: str, private_key) -> bool:
    path = f"/portfolio/orders/{order_id}"
    headers = sign_request(access_key, private_key, "DELETE", path)
    try:
        resp = requests.delete(BASE_URL + path, headers=headers, timeout=15)
        if resp.status_code in (200, 204):
            logger.info(f"  Cancelled order {order_id}")
            return True
        logger.warning(f"Cancel {order_id} → {resp.status_code}: {resp.text[:100]}")
    except requests.RequestException as e:
        logger.warning(f"Cancel request error for {order_id}: {e}")
    return False


def monitor_positions(dry_run: bool = False) -> list:
    if not EXEC_LOG.exists():
        logger.warning("execution_log.json not found — nothing to monitor")
        return []

    log = json.loads(EXEC_LOG.read_text())

    # Find orders that were placed (status="placed") and need monitoring
    pending = [
        entry for entry in log
        if entry.get("status") == "placed" and entry.get("order_id")
    ]

    if not pending:
        logger.info("No pending orders to monitor")
        return log

    if dry_run:
        logger.info(f"[DRY RUN] Would monitor {len(pending)} orders")
        return log

    access_key = os.environ.get("KALSHI_ACCESS_KEY")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not access_key or not key_path:
        logger.error("Missing Kalshi credentials — cannot monitor orders")
        return log

    private_key = load_private_key(key_path)
    logger.info(f"Monitoring {len(pending)} open orders (max {MAX_WAIT_SECONDS//60} minutes)...")

    # Build index for fast lookup
    log_index = {entry.get("order_id"): i for i, entry in enumerate(log) if entry.get("order_id")}
    start_time = time.time()

    still_pending = list(pending)

    while still_pending and (time.time() - start_time) < MAX_WAIT_SECONDS:
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed = time.time() - start_time
        logger.info(f"  Poll ({elapsed:.0f}s elapsed): checking {len(still_pending)} orders...")

        resolved = []
        for entry in still_pending:
            order_id = entry["order_id"]
            order = get_order_status(order_id, access_key, private_key)
            status = order.get("status", "unknown")
            idx = log_index.get(order_id)
            if idx is None:
                continue

            if status == "filled":
                log[idx].update({
                    "status": "filled",
                    "filled_price": order.get("yes_price"),
                    "filled_count": order.get("filled_count", entry.get("contracts")),
                    "fees_paid": order.get("fees", 0) / 100.0,
                    "resolved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
                logger.info(f"    {order_id}: FILLED @ {order.get('yes_price')}¢")
                resolved.append(entry)
            elif status == "partially_filled":
                log[idx].update({
                    "status": "partial",
                    "filled_count": order.get("filled_count", 0),
                })
                logger.info(f"    {order_id}: PARTIAL ({order.get('filled_count')} contracts)")
            elif status in ("cancelled", "expired"):
                log[idx]["status"] = status
                logger.info(f"    {order_id}: {status.upper()}")
                resolved.append(entry)

        still_pending = [e for e in still_pending if e not in resolved]

    # Cancel any remaining unfilled orders
    if still_pending:
        logger.info(f"Timeout reached. Cancelling {len(still_pending)} unfilled orders...")
        for entry in still_pending:
            order_id = entry["order_id"]
            cancelled = cancel_order(order_id, access_key, private_key)
            idx = log_index.get(order_id)
            if idx is not None:
                log[idx]["status"] = "cancelled" if cancelled else "cancel_failed"
                log[idx]["resolved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    DATA_DIR.mkdir(exist_ok=True)
    EXEC_LOG.write_text(json.dumps(log, indent=2))
    logger.info(f"Execution log updated → {EXEC_LOG}")
    return log


def main(dry_run: bool = False):
    return monitor_positions(dry_run=dry_run)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    log = main(dry_run=args.dry_run)
    statuses = {}
    for e in log:
        s = e.get("status", "unknown")
        statuses[s] = statuses.get(s, 0) + 1
    print(f"\nExecution log summary: {statuses}")
