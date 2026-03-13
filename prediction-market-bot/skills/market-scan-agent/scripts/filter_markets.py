"""
filter_markets.py — Step 2 of the market scan pipeline.

Reads data/raw_markets.json and applies liquidity/timing filters.
Saves qualifying markets to data/filtered_markets.json.

Filters applied (ALL must pass):
  - 24h volume (dollar) >= $500
  - open interest (dollar) >= $1000
  - spread <= $0.08
  - time to close: 1–720 hours from now
"""

import json
from datetime import datetime, timezone
from pathlib import Path

INPUT_FILE = Path(__file__).parents[3] / "data" / "raw_markets.json"
OUTPUT_FILE = Path(__file__).parents[3] / "data" / "filtered_markets.json"

MIN_VOLUME_24H = 500.0      # $500 minimum 24h dollar volume
MIN_OPEN_INTEREST = 1000.0  # $1000 minimum open interest
MAX_SPREAD = 0.08           # $0.08 maximum bid-ask spread
MIN_HOURS_TO_CLOSE = 1.0
MAX_HOURS_TO_CLOSE = 720.0


def parse_close_time(close_time_str: str) -> datetime | None:
    if not close_time_str:
        return None
    try:
        return datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def hours_until(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    delta = dt - now
    return delta.total_seconds() / 3600.0


def passes_filters(market: dict) -> tuple[bool, str]:
    """Returns (passes, reason_if_rejected)."""

    # 24h dollar volume
    vol_24h = float(market.get("volume_24h_fp") or 0)
    if vol_24h < MIN_VOLUME_24H:
        return False, f"low_volume_24h={vol_24h:.0f}"

    # Open interest
    oi = float(market.get("open_interest_fp") or 0)
    if oi < MIN_OPEN_INTEREST:
        return False, f"low_open_interest={oi:.0f}"

    # Spread (field names use _dollars suffix in Kalshi v2 API)
    yes_bid = float(market.get("yes_bid_dollars") or 0)
    yes_ask = float(market.get("yes_ask_dollars") or 0)
    if yes_bid <= 0 or yes_ask <= 0:
        return False, "missing_bid_ask"
    spread = yes_ask - yes_bid
    if spread > MAX_SPREAD:
        return False, f"wide_spread={spread:.4f}"

    # Time to close
    close_time = parse_close_time(market.get("close_time", ""))
    if close_time is None:
        return False, "missing_close_time"
    hours = hours_until(close_time)
    if hours < MIN_HOURS_TO_CLOSE:
        return False, f"closing_too_soon={hours:.1f}h"
    if hours > MAX_HOURS_TO_CLOSE:
        return False, f"closing_too_far={hours:.1f}h"

    return True, ""


def main():
    print("Filtering markets by liquidity and timing criteria...")

    with open(INPUT_FILE) as f:
        markets = json.load(f)

    print(f"  Input: {len(markets)} markets")

    passed = []
    rejection_reasons: dict[str, int] = {}

    for market in markets:
        ok, reason = passes_filters(market)
        if ok:
            # Attach computed fields for downstream steps
            yes_bid = float(market.get("yes_bid_dollars") or 0)
            yes_ask = float(market.get("yes_ask_dollars") or 0)
            close_time = parse_close_time(market.get("close_time", ""))
            market["_spread"] = round(yes_ask - yes_bid, 4)
            market["_hours_to_close"] = round(hours_until(close_time), 2)
            market["_vol_24h_fp"] = float(market.get("volume_24h_fp") or 0)
            market["_oi_fp"] = float(market.get("open_interest_fp") or 0)
            passed.append(market)
        else:
            rejection_reasons[reason.split("=")[0]] = rejection_reasons.get(reason.split("=")[0], 0) + 1

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(passed, f, indent=2)

    print(f"  Passed: {len(passed)} markets")
    print(f"  Rejected: {len(markets) - len(passed)} markets")
    if rejection_reasons:
        for reason, count in sorted(rejection_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")
    print(f"  Saved to {OUTPUT_FILE}")
    return passed


if __name__ == "__main__":
    main()
