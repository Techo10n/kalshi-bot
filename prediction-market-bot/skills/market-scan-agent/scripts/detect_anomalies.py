"""
detect_anomalies.py — Step 3 of the market scan pipeline.

Reads data/filtered_markets.json and scores each market on 5 anomaly flags.
Each flag adds 1 point to the anomaly_score (max 5).

Flags:
  PRICE_SPIKE  — yes_bid moved >10¢ from previous_yes_bid
  WIDE_SPREAD  — spread > 6¢
  VOL_SURGE    — 24h volume > 3× the median for its category
  NEAR_50      — yes_bid between $0.35 and $0.65 (most uncertain)
  IMMINENT     — closes within 24 hours

Saves scored markets to data/flagged_markets.json.
"""

import json
import statistics
from pathlib import Path

INPUT_FILE = Path(__file__).parents[3] / "data" / "filtered_markets.json"
OUTPUT_FILE = Path(__file__).parents[3] / "data" / "flagged_markets.json"

PRICE_SPIKE_THRESHOLD = 0.10   # $0.10 move from previous bid
WIDE_SPREAD_THRESHOLD = 0.06   # $0.06 spread
VOL_SURGE_MULTIPLIER = 3.0     # 3× category median
NEAR_50_LOW = 0.35
NEAR_50_HIGH = 0.65
IMMINENT_HOURS = 24.0


def compute_category_medians(markets: list[dict]) -> dict[str, float]:
    """Compute median 24h dollar volume per category."""
    by_category: dict[str, list[float]] = {}
    for m in markets:
        cat = m.get("category") or "Unknown"
        vol = float(m.get("volume_24h_fp") or 0)
        by_category.setdefault(cat, []).append(vol)

    return {
        cat: statistics.median(vols) if vols else 0.0
        for cat, vols in by_category.items()
    }


def score_market(market: dict, category_medians: dict[str, float]) -> dict:
    flags: list[str] = []

    yes_bid = float(market.get("yes_bid") or 0)
    yes_ask = float(market.get("yes_ask") or 0)
    prev_bid = float(market.get("previous_yes_bid") or yes_bid)
    spread = market.get("_spread", yes_ask - yes_bid)
    vol_24h = market.get("_vol_24h_fp", float(market.get("volume_24h_fp") or 0))
    hours_to_close = market.get("_hours_to_close", 999.0)
    category = market.get("category") or "Unknown"

    # PRICE_SPIKE
    if abs(yes_bid - prev_bid) > PRICE_SPIKE_THRESHOLD:
        flags.append("PRICE_SPIKE")

    # WIDE_SPREAD
    if spread > WIDE_SPREAD_THRESHOLD:
        flags.append("WIDE_SPREAD")

    # VOL_SURGE
    cat_median = category_medians.get(category, 0.0)
    if cat_median > 0 and vol_24h > VOL_SURGE_MULTIPLIER * cat_median:
        flags.append("VOL_SURGE")

    # NEAR_50
    if NEAR_50_LOW <= yes_bid <= NEAR_50_HIGH:
        flags.append("NEAR_50")

    # IMMINENT
    if 0 < hours_to_close <= IMMINENT_HOURS:
        flags.append("IMMINENT")

    market["anomaly_flags"] = flags
    market["anomaly_score"] = len(flags)
    return market


def main():
    print("Detecting anomalies in filtered markets...")

    with open(INPUT_FILE) as f:
        markets = json.load(f)

    print(f"  Input: {len(markets)} markets")

    category_medians = compute_category_medians(markets)
    print(f"  Category medians computed for {len(category_medians)} categories:")
    for cat, median in sorted(category_medians.items(), key=lambda x: -x[1])[:5]:
        print(f"    {cat}: ${median:,.0f}")

    scored = [score_market(m, category_medians) for m in markets]

    # Summary
    flag_counts: dict[str, int] = {}
    for m in scored:
        for flag in m["anomaly_flags"]:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    print(f"\n  Anomaly flag summary:")
    for flag, count in sorted(flag_counts.items(), key=lambda x: -x[1]):
        print(f"    {flag}: {count} markets")

    score_dist = {}
    for m in scored:
        s = m["anomaly_score"]
        score_dist[s] = score_dist.get(s, 0) + 1
    print(f"\n  Score distribution:")
    for s in sorted(score_dist):
        print(f"    Score {s}: {score_dist[s]} markets")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(scored, f, indent=2)

    print(f"\n  Saved {len(scored)} scored markets to {OUTPUT_FILE}")
    return scored


if __name__ == "__main__":
    main()
