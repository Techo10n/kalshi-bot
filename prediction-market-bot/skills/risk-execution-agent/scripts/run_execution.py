"""
Risk Execution Agent — full pipeline orchestrator.

Steps:
  1. check_risk.py       — fetch portfolio state, check exposure
  2. size_position.py    — Kelly criterion sizing
  3. place_order.py      — place limit orders
  4. monitor_position.py — poll fills, cancel after 10 minutes

Use --dry-run to preview orders without hitting the API.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"


def check_prerequisites():
    pred_file = DATA_DIR / "predictions.json"
    if not pred_file.exists():
        logger.error(
            "data/predictions.json not found.\n"
            "Run prediction-agent first:\n"
            "  python skills/prediction-agent/scripts/run_prediction.py"
        )
        sys.exit(1)

    preds = json.loads(pred_file.read_text())
    if not preds:
        logger.warning("predictions.json is empty — no signals to execute")
        sys.exit(0)
    return preds


def main():
    parser = argparse.ArgumentParser(description="Risk Execution Agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview orders without placing them (STRONGLY RECOMMENDED before live use)",
    )
    args = parser.parse_args()

    if not args.dry_run:
        print("\n*** LIVE EXECUTION MODE — real orders will be placed ***")
        print("Use --dry-run to preview first. Continuing in 3 seconds...")
        time.sleep(3)

    pipeline_start = time.time()
    predictions = check_prerequisites()

    print(f"\n=== RISK EXECUTION AGENT {'[DRY RUN]' if args.dry_run else '[LIVE]'} ===")
    print(f"Input predictions: {len(predictions)}")

    # Step 1: Check risk
    t = time.time()
    logger.info("--- [Step 1: Check Risk] START ---")
    import check_risk
    portfolio_state = check_risk.main(dry_run=args.dry_run)
    logger.info(f"--- [Step 1: Check Risk] DONE ({time.time()-t:.1f}s) ---")

    if portfolio_state.get("blocked"):
        print(f"\nEXECUTION BLOCKED: {portfolio_state.get('block_reason')}")
        print("Resolve exposure before placing new orders.")
        sys.exit(0)

    # Step 2: Size positions
    t = time.time()
    logger.info("--- [Step 2: Size Positions] START ---")
    import size_position
    sized = size_position.main(dry_run=args.dry_run)
    logger.info(f"--- [Step 2: Size Positions] DONE ({time.time()-t:.1f}s) ---")

    if not sized:
        print("\nNo positions to place after risk checks.")
        print(
            f"Portfolio: ${portfolio_state.get('available_balance', 0):.2f} available, "
            f"{portfolio_state.get('exposure_ratio', 0):.1%} exposed"
        )
        sys.exit(0)

    # Step 3: Place orders
    t = time.time()
    logger.info("--- [Step 3: Place Orders] START ---")
    import place_order
    placed = place_order.main(dry_run=args.dry_run)
    logger.info(f"--- [Step 3: Place Orders] DONE ({time.time()-t:.1f}s) ---")

    # Step 4: Monitor fills (skip in dry-run)
    monitored_log = []
    if not args.dry_run:
        t = time.time()
        logger.info("--- [Step 4: Monitor Positions] START ---")
        import monitor_position
        monitored_log = monitor_position.main(dry_run=False)
        logger.info(f"--- [Step 4: Monitor Positions] DONE ({time.time()-t:.1f}s) ---")

    # Summary
    elapsed = time.time() - pipeline_start
    total_capital = sum(p.get("bet_size", 0) for p in sized)
    skipped = len(predictions) - len(sized)

    print(f"\n=== EXECUTION COMPLETE ({elapsed:.1f}s) ===")
    print(f"Placed {len(placed)} trades")
    print(f"Skipped {skipped} (risk/size filters)")
    print(f"Total deployed: ${total_capital:.2f}")

    if args.dry_run:
        print("\n[DRY RUN] No real orders were placed.")
        print("Remove --dry-run to execute live.")
        print("\nOrders that WOULD be placed:")
        for p in sized:
            print(
                f"  {p['ticker']:<40} {p['signal']:<8} "
                f"${p['bet_size']:.2f} ({p['contracts']} contracts @ {p['yes_price_cents']}¢)"
            )
    else:
        if monitored_log:
            statuses = {}
            for e in monitored_log:
                s = e.get("status", "unknown")
                statuses[s] = statuses.get(s, 0) + 1
            print(f"Fill statuses: {statuses}")
        print("\nOutput: data/execution_log.json")


if __name__ == "__main__":
    main()
