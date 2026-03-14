"""
Master Orchestrator — runs the full Kalshi trading bot pipeline.

Pipeline:
  Step 1: market-scan-agent  (fetch → filter → anomalies → rank)
  Step 2: research-agent     (twitter + reddit + rss → sentiment → narrative)
  Step 3: prediction-agent   (features → xgboost → llm → confidence filter)
  Step 4: risk-execution-agent (risk check → sizing → place → monitor)

postmortem-agent runs separately on a daily schedule.

Usage:
  python skills/run_bot.py                  # full pipeline, live execution
  python skills/run_bot.py --dry-run        # full pipeline, no real orders
  python skills/run_bot.py --scan-only      # steps 1-2 only
  python skills/run_bot.py --predict-only   # steps 1-3 only
  python skills/run_bot.py --no-twitter     # skip Twitter scraping
  python skills/run_bot.py --top-n 10       # research only top 10 markets

Target total runtime: under 3 minutes (excluding model download on first run).
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Load .env / .env.local credentials before any agent code runs
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).parents[1] / ".env.local"
    if not _env_file.exists():
        _env_file = Path(__file__).parents[1] / ".env"
    load_dotenv(_env_file, override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on env already being set

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
SCRIPTS = {
    "scan": ROOT / "market-scan-agent" / "scripts",
    "research": ROOT / "research-agent" / "scripts",
    "prediction": ROOT / "prediction-agent" / "scripts",
    "execution": ROOT / "risk-execution-agent" / "scripts",
}
DATA_DIR = ROOT.parent / "data"


def add_to_path(*dirs):
    for d in dirs:
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))


def step(name: str, fn, dry_run: bool = False):
    """Run a pipeline step with timing. Returns (result, elapsed_seconds)."""
    logger.info(f"\n{'='*50}")
    logger.info(f"  {name}")
    logger.info(f"{'='*50}")
    t0 = time.time()
    try:
        result = fn()
        elapsed = time.time() - t0
        logger.info(f"✓ {name} completed in {elapsed:.1f}s")
        return result, elapsed
    except SystemExit as e:
        # Allow sys.exit(0) from sub-pipelines
        if e.code == 0:
            return None, time.time() - t0
        raise
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"✗ {name} FAILED after {elapsed:.1f}s: {e}")
        raise


def trim_scan_results(top_n: int):
    """Trim scan_results.json to top N markets."""
    path = DATA_DIR / "scan_results.json"
    if not path.exists():
        return
    markets = json.loads(path.read_text())
    if len(markets) > top_n:
        markets = markets[:top_n]
        path.write_text(json.dumps(markets, indent=2))
        logger.info(f"Trimmed scan_results.json to top {top_n} markets")


def run_scan():
    add_to_path(SCRIPTS["scan"])
    import run_scan as rs
    return rs.main()


def run_research(no_twitter: bool = False, no_reddit: bool = False):
    add_to_path(SCRIPTS["research"])
    import run_research as rr
    # Patch argv for argparse inside run_research
    orig_argv = sys.argv
    sys.argv = ["run_research.py"]
    if no_twitter:
        sys.argv.append("--no-twitter")
    if no_reddit:
        sys.argv.append("--no-reddit")
    try:
        rr.main()
    finally:
        sys.argv = orig_argv


def run_prediction():
    add_to_path(SCRIPTS["prediction"])
    import run_prediction as rp
    return rp.main()


def run_execution(dry_run: bool = False):
    add_to_path(SCRIPTS["execution"])
    import run_execution as re_mod
    orig_argv = sys.argv
    sys.argv = ["run_execution.py"]
    if dry_run:
        sys.argv.append("--dry-run")
    try:
        re_mod.main()
    finally:
        sys.argv = orig_argv


def print_summary(timings: dict, predictions: list, dry_run: bool, scan_only: bool, predict_only: bool):
    total = sum(timings.values())
    print(f"\n{'='*55}")
    print(f"  KALSHI BOT RUN COMPLETE")
    print(f"{'='*55}")
    print(f"  Mode:  {'DRY RUN' if dry_run else 'SCAN ONLY' if scan_only else 'PREDICT ONLY' if predict_only else 'LIVE'}")
    print(f"  Total runtime: {total:.1f}s")
    print(f"\n  Step timings:")
    for name, t in timings.items():
        print(f"    {name:<30} {t:>6.1f}s")

    if predictions is not None:
        print(f"\n  Predictions passing threshold: {len(predictions)}")
        if predictions:
            print(f"\n  Top signals:")
            for p in predictions[:5]:
                print(
                    f"    {p['ticker']:<40} {p['signal']:<8} "
                    f"conf={p['confidence']:.2f} edge={p['edge']:+.3f}"
                )

    if total > 180:
        print(f"\n  [NOTE] Runtime {total:.0f}s exceeded 3-minute target.")
        print("  Tips: use --no-twitter to skip slow Twitter scraping")
        print("        use --top-n to reduce markets researched")
    print(f"{'='*55}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Kalshi trading bot — full pipeline orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--scan-only", action="store_true", help="Run steps 1-2, no trading")
    parser.add_argument("--predict-only", action="store_true", help="Run steps 1-3, no trading")
    parser.add_argument("--dry-run", action="store_true", help="Full pipeline, no real orders")
    parser.add_argument("--no-twitter", action="store_true", help="Skip Twitter scraping")
    parser.add_argument("--top-n", type=int, default=20, metavar="INT", help="Research top N markets (default: 20)")
    args = parser.parse_args()

    if not args.dry_run and not args.scan_only and not args.predict_only:
        print("\n*** LIVE EXECUTION MODE — real orders will be placed ***")
        print("Use --dry-run to preview first. Continuing in 3 seconds...")
        time.sleep(3)

    bot_start = time.time()
    timings = {}
    predictions = None

    print(f"\n{'='*55}")
    print(f"  KALSHI BOT STARTING")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'SCAN ONLY' if args.scan_only else 'PREDICT ONLY' if args.predict_only else 'LIVE'}")
    print(f"  Top-N: {args.top_n} | Twitter: {'OFF' if args.no_twitter else 'ON'}")
    print(f"{'='*55}")

    # Step 1: Market Scan
    _, t = step("Step 1/4: Market Scan Agent", run_scan)
    timings["market-scan"] = t

    # Optionally trim to top-N
    if args.top_n < 20:
        trim_scan_results(args.top_n)

    # Step 2: Research
    _, t = step(
        "Step 2/4: Research Agent",
        lambda: run_research(no_twitter=args.no_twitter),
    )
    timings["research"] = t

    if args.scan_only:
        print_summary(timings, None, args.dry_run, args.scan_only, args.predict_only)
        return

    # Step 3: Prediction
    predictions, t = step("Step 3/4: Prediction Agent", run_prediction)
    timings["prediction"] = t

    if args.predict_only:
        print_summary(timings, predictions, args.dry_run, args.scan_only, args.predict_only)
        return

    # Step 4: Execution
    if not predictions:
        print("\nNo predictions passed confidence threshold — skipping execution.")
        timings["execution"] = 0.0
    else:
        _, t = step(
            "Step 4/4: Risk Execution Agent",
            lambda: run_execution(dry_run=args.dry_run),
        )
        timings["execution"] = t

    print_summary(timings, predictions, args.dry_run, args.scan_only, args.predict_only)


if __name__ == "__main__":
    main()
