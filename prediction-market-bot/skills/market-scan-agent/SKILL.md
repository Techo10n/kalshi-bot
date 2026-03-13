---
name: market-scan-agent
description: >
  Scans Kalshi prediction markets to find trading opportunities. Trigger this skill
  when the user says things like "scan markets", "find opportunities", "run the scanner",
  "check Kalshi", "what markets look interesting", "find anomalies", "show me top markets",
  or "which markets should I look at". Runs a 4-step pipeline: fetch → filter →
  detect anomalies → rank. Outputs the top 20 ranked markets to data/scan_results.json.
---

# Market Scan Agent

Scans all open Kalshi markets and surfaces the best trading opportunities using a
4-step pipeline. No authentication required — all data comes from the public REST API.

## When to Use

Invoke this skill whenever the user wants to:
- Discover which Kalshi markets are worth trading
- Find markets with unusual price or volume activity
- Get a ranked list of opportunities before placing orders
- Run a fresh scan before the analysis or execution agents act

## Pipeline Overview

```
Step 1: fetch_markets.py      → data/raw_markets.json
Step 2: filter_markets.py     → data/filtered_markets.json
Step 3: detect_anomalies.py   → data/flagged_markets.json
Step 4: rank_markets.py       → data/scan_results.json
```

Run the full pipeline with:

```bash
python skills/market-scan-agent/scripts/run_scan.py
```

Or run individual steps:

```bash
python skills/market-scan-agent/scripts/fetch_markets.py
python skills/market-scan-agent/scripts/filter_markets.py
python skills/market-scan-agent/scripts/detect_anomalies.py
python skills/market-scan-agent/scripts/rank_markets.py
```

## Step-by-Step Breakdown

### Step 1 — fetch_markets.py
- Calls `GET /markets?status=open&limit=1000` on the Kalshi public API
- Follows cursor pagination until all pages are exhausted
- Saves the full list to `data/raw_markets.json`

### Step 2 — filter_markets.py
Reads `data/raw_markets.json` and keeps only markets passing ALL of:
- `volume_24h_fp` ≥ 500 (≥ $500 in 24h volume)
- `open_interest_fp` ≥ 1000 (≥ $1000 open interest)
- Spread (`yes_ask_dollars - yes_bid_dollars`) ≤ $0.08
- Time to close between 1 and 720 hours from now

Saves passing markets to `data/filtered_markets.json`.

### Step 3 — detect_anomalies.py
Reads `data/filtered_markets.json` and scores each market 0–5 on five flags:

| Flag | Condition | Score |
|------|-----------|-------|
| PRICE_SPIKE | yes_bid moved >10¢ from previous_yes_bid | 1 |
| WIDE_SPREAD | spread > 6¢ | 1 |
| VOL_SURGE | 24h volume > 3× category median | 1 |
| NEAR_50 | yes_bid between $0.35–$0.65 | 1 |
| IMMINENT | closes within 24 hours | 1 |

Saves scored markets to `data/flagged_markets.json`.

### Step 4 — rank_markets.py
Reads `data/flagged_markets.json` and computes a composite score:

```
score = (vol_24h / 1000)^0.30 * 0.30
      + (1 / spread_cents)^0.30 * 0.30
      + anomaly_score          * 0.25
      + near_50_bonus          * 0.15
```

Outputs the top 20 markets (sorted by score descending) to `data/scan_results.json`.

## Output Format

`data/scan_results.json` is a list of up to 20 objects:

```json
[
  {
    "ticker": "SERIES-DATE-THRESHOLD",
    "title": "Market title",
    "yes_bid": 0.52,
    "yes_ask": 0.55,
    "spread_cents": 3.0,
    "volume_24h": 12400.0,
    "open_interest": 45000.0,
    "anomaly_score": 3,
    "anomaly_flags": ["NEAR_50", "VOL_SURGE", "PRICE_SPIKE"],
    "composite_score": 4.27,
    "hours_to_close": 18.5
  }
]
```

## Notes

- Dollar fields from Kalshi are strings — always use `float()` before math.
- The scanner is read-only and safe to run at any time.
- Results reflect market state at scan time; re-run for fresh data.
- See `references/api-patterns.md` for full API field documentation.
