"""
run_scan.py — Orchestrates the full market scan pipeline.

Runs all 4 steps in sequence:
  1. fetch_markets.py    → data/raw_markets.json
  2. filter_markets.py   → data/filtered_markets.json
  3. detect_anomalies.py → data/flagged_markets.json
  4. rank_markets.py     → data/scan_results.json
"""

import sys
import time
from pathlib import Path

# Ensure scripts directory is on path for direct imports
scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

import fetch_markets
import filter_markets
import detect_anomalies
import rank_markets


DIVIDER = "─" * 60


def run_step(step_num: int, name: str, fn) -> tuple[any, float]:
    print(f"\n{DIVIDER}")
    print(f"  STEP {step_num}: {name}")
    print(DIVIDER)
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    print(f"  Done in {elapsed:.2f}s")
    return result, elapsed


def main():
    print(f"\n{'═' * 60}")
    print("  KALSHI MARKET SCANNER")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"{'═' * 60}")

    total_start = time.perf_counter()

    _, t1 = run_step(1, "Fetch open markets", fetch_markets.main)
    _, t2 = run_step(2, "Filter by liquidity & timing", filter_markets.main)
    _, t3 = run_step(3, "Detect anomalies", detect_anomalies.main)
    results, t4 = run_step(4, "Rank by composite score", rank_markets.main)

    total = time.perf_counter() - total_start

    print(f"\n{DIVIDER}")
    print(f"  SCAN COMPLETE")
    print(f"{DIVIDER}")
    print(f"  Step 1 fetch:    {t1:>6.2f}s")
    print(f"  Step 2 filter:   {t2:>6.2f}s")
    print(f"  Step 3 anomaly:  {t3:>6.2f}s")
    print(f"  Step 4 rank:     {t4:>6.2f}s")
    print(f"  {'─'*28}")
    print(f"  Total:           {total:>6.2f}s")
    print(f"\n  Results: data/scan_results.json ({len(results) if results else 0} markets)")
    print(f"{'═' * 60}\n")

    return results


if __name__ == "__main__":
    main()
