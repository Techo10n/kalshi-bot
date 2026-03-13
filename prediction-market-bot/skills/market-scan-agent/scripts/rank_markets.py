"""
rank_markets.py — Step 4 of the market scan pipeline.

Reads data/flagged_markets.json and computes a composite score for each market.
Outputs the top 20 markets to data/scan_results.json.

Composite score formula:
  score = (vol_24h / 1000)^0.30 * 0.30
        + (1 / spread_cents)^0.30 * 0.30
        + anomaly_score             * 0.25
        + near_50_bonus             * 0.15

  near_50_bonus = 1 if "NEAR_50" in anomaly_flags, else 0
"""

import json
from pathlib import Path

INPUT_FILE = Path(__file__).parents[3] / "data" / "flagged_markets.json"
OUTPUT_FILE = Path(__file__).parents[3] / "data" / "scan_results.json"

TOP_N = 20

W_VOLUME = 0.30
W_SPREAD = 0.30
W_ANOMALY = 0.25
W_NEAR50 = 0.15


def composite_score(market: dict) -> float:
    vol_24h = market.get("_vol_24h_fp", float(market.get("volume_24h_fp") or 0))
    spread = market.get("_spread", 0.0)
    anomaly_score = market.get("anomaly_score", 0)
    near_50_bonus = 1.0 if "NEAR_50" in market.get("anomaly_flags", []) else 0.0

    vol_component = (vol_24h / 1000) ** W_VOLUME if vol_24h > 0 else 0.0

    spread_cents = spread * 100
    spread_component = (1.0 / spread_cents) ** W_SPREAD if spread_cents > 0 else 0.0

    score = (
        vol_component * W_VOLUME
        + spread_component * W_SPREAD
        + anomaly_score * W_ANOMALY
        + near_50_bonus * W_NEAR50
    )
    return round(score, 4)


def build_result(market: dict) -> dict:
    yes_bid = float(market.get("yes_bid_dollars") or 0)
    yes_ask = float(market.get("yes_ask_dollars") or 0)
    spread = market.get("_spread", yes_ask - yes_bid)

    return {
        "ticker": market.get("ticker", ""),
        "title": market.get("title", ""),
        "category": market.get("category", ""),
        "yes_bid": round(yes_bid, 4),
        "yes_ask": round(yes_ask, 4),
        "spread_cents": round(spread * 100, 2),
        "volume_24h": round(market.get("_vol_24h_fp", 0), 2),
        "open_interest": round(market.get("_oi_fp", 0), 2),
        "hours_to_close": market.get("_hours_to_close", 0),
        "anomaly_score": market.get("anomaly_score", 0),
        "anomaly_flags": market.get("anomaly_flags", []),
        "composite_score": market.get("_composite_score", 0),
    }


def main():
    print("Ranking markets by composite score...")

    with open(INPUT_FILE) as f:
        markets = json.load(f)

    print(f"  Input: {len(markets)} markets")

    # Compute composite scores
    for m in markets:
        m["_composite_score"] = composite_score(m)

    # Sort and take top N
    ranked = sorted(markets, key=lambda m: m["_composite_score"], reverse=True)
    top = ranked[:TOP_N]

    # Build clean output
    results = [build_result(m) for m in top]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n  Top {len(results)} markets:")
    print(f"  {'#':<4} {'Score':<8} {'Flags':<6} {'Vol24h':<10} {'Spread':<8} Ticker")
    print(f"  {'-'*4} {'-'*8} {'-'*6} {'-'*10} {'-'*8} {'-'*30}")
    for i, r in enumerate(results, 1):
        print(
            f"  {i:<4} {r['composite_score']:<8.4f} "
            f"{r['anomaly_score']:<6} "
            f"${r['volume_24h']:<9,.0f} "
            f"{r['spread_cents']:<8.1f}¢ "
            f"{r['ticker']}"
        )

    print(f"\n  Saved top {len(results)} markets to {OUTPUT_FILE}")
    return results


if __name__ == "__main__":
    main()
