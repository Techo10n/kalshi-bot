"""
Research Agent — full pipeline orchestrator.

Runs all 5 steps in sequence:
  1. scrape_twitter.py   (skipped if no TWITTER_BEARER_TOKEN)
  2. scrape_reddit.py    (skipped if no Reddit creds)
  3. scrape_rss.py       (always runs)
  4. sentiment_analysis.py
  5. compare_narrative.py

Output: data/research_results.json
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# Add scripts dir to path so we can import sibling modules
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"


def check_prerequisites():
    scan_file = DATA_DIR / "scan_results.json"
    if not scan_file.exists():
        logger.error(
            "data/scan_results.json not found.\n"
            "Run market-scan-agent first:\n"
            "  python skills/market-scan-agent/scripts/run_scan.py"
        )
        sys.exit(1)


def run_step(name: str, fn, skip: bool = False):
    if skip:
        logger.info(f"--- [{name}] SKIPPED ---")
        return None

    logger.info(f"--- [{name}] START ---")
    t0 = time.time()
    try:
        result = fn()
        elapsed = time.time() - t0
        logger.info(f"--- [{name}] DONE ({elapsed:.1f}s) ---")
        return result
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"--- [{name}] FAILED ({elapsed:.1f}s): {e} ---")
        return None


def main():
    parser = argparse.ArgumentParser(description="Research Agent pipeline")
    parser.add_argument("--no-twitter", action="store_true", help="Skip Twitter scraping")
    parser.add_argument("--no-reddit", action="store_true", help="Skip Reddit scraping")
    args = parser.parse_args()

    pipeline_start = time.time()
    check_prerequisites()

    has_twitter = bool(os.environ.get("TWITTER_BEARER_TOKEN")) and not args.no_twitter
    has_reddit = (
        bool(os.environ.get("REDDIT_CLIENT_ID"))
        and bool(os.environ.get("REDDIT_CLIENT_SECRET"))
        and not args.no_reddit
    )

    if not has_twitter:
        reason = "--no-twitter flag" if args.no_twitter else "no TWITTER_BEARER_TOKEN"
        logger.warning(f"Twitter: SKIPPED ({reason})")
    if not has_reddit:
        reason = "--no-reddit flag" if args.no_reddit else "missing REDDIT_CLIENT_ID/SECRET"
        logger.warning(f"Reddit: SKIPPED ({reason})")

    print("\n=== RESEARCH AGENT PIPELINE ===")

    # Step 1: Twitter
    import scrape_twitter
    run_step("Step 1: Twitter", scrape_twitter.main, skip=not has_twitter)

    # Step 2: Reddit
    import scrape_reddit
    run_step("Step 2: Reddit", scrape_reddit.main, skip=not has_reddit)

    # Step 3: RSS (always runs)
    import scrape_rss
    run_step("Step 3: RSS", scrape_rss.main)

    # Step 4: Sentiment
    import sentiment_analysis
    scores = run_step("Step 4: Sentiment Analysis", sentiment_analysis.main)

    # Step 5: Narrative Comparison
    import compare_narrative
    results = run_step("Step 5: Narrative Comparison", compare_narrative.main)

    total_elapsed = time.time() - pipeline_start

    print(f"\n=== RESEARCH COMPLETE ({total_elapsed:.1f}s) ===")
    if results:
        flagged = [r for r in results if r.get("narrative_flags")]
        print(f"Markets researched:   {len(results)}")
        print(f"Narrative flags:      {len(flagged)}")
        if flagged:
            print("\nTop flagged markets:")
            for r in sorted(flagged, key=lambda x: x["narrative_edge"], reverse=True)[:5]:
                print(
                    f"  {r['ticker']:<40} edge={r['narrative_edge']:.2f}  "
                    f"flags={r['narrative_flags']}"
                )
        print(f"\nOutput: data/research_results.json")
    else:
        print("No results generated — check logs above for errors.")


if __name__ == "__main__":
    main()
