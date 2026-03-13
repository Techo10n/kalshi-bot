"""
Step 3: Update data/system_memory.json with lessons from today's postmortem.

Reads:  data/system_memory.json (existing, if any)
        postmortem results (passed in from analyze_failure.py)
        newly settled records (for category performance)
Writes: data/system_memory.json

Flags failure modes with count >= 3 as SYSTEMIC_ISSUE.
"""

import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
MEMORY_PATH = DATA_DIR / "system_memory.json"

SYSTEMIC_THRESHOLD = 3

DEFAULT_MEMORY = {
    "failure_patterns": [],
    "category_performance": {},
    "model_adjustments": [],
    "blacklisted_patterns": [],
    "last_retrain_sample_count": 0,
    "total_trades": 0,
    "total_wins": 0,
    "total_losses": 0,
    "total_pnl": 0.0,
    "last_updated": None,
}


def load_memory() -> dict:
    if MEMORY_PATH.exists():
        try:
            return json.loads(MEMORY_PATH.read_text())
        except (json.JSONDecodeError, KeyError):
            logger.warning("Corrupt system_memory.json — resetting")
    return dict(DEFAULT_MEMORY)


def save_memory(memory: dict):
    DATA_DIR.mkdir(exist_ok=True)
    memory["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    MEMORY_PATH.write_text(json.dumps(memory, indent=2))


def update_failure_patterns(memory: dict, postmortem_results: list):
    """Update failure mode counts and examples."""
    patterns_index = {p["failure_mode"]: p for p in memory["failure_patterns"]}

    for result in postmortem_results:
        mode = result.get("failure_mode", "UNANALYZED")
        ticker = result.get("ticker", "")
        fix = result.get("suggested_fix", "")

        if mode not in patterns_index:
            patterns_index[mode] = {
                "failure_mode": mode,
                "count": 0,
                "example_tickers": [],
                "suggested_fixes": [],
                "is_systemic": False,
            }

        pattern = patterns_index[mode]
        pattern["count"] += 1
        if ticker and ticker not in pattern["example_tickers"]:
            pattern["example_tickers"].append(ticker)
        if fix and fix not in pattern["suggested_fixes"]:
            pattern["suggested_fixes"].append(fix)
        pattern["is_systemic"] = pattern["count"] >= SYSTEMIC_THRESHOLD

    memory["failure_patterns"] = list(patterns_index.values())


def update_category_performance(memory: dict, settled_records: list):
    """Update per-category win/loss/pnl stats."""
    perf = memory["category_performance"]

    for record in settled_records:
        category = record.get("category", "unknown") or "unknown"
        if category not in perf:
            perf[category] = {"wins": 0, "losses": 0, "avg_pnl": 0.0, "avg_confidence": 0.0, "_pnl_sum": 0.0, "_conf_sum": 0.0, "_count": 0}

        p = perf[category]
        classification = record.get("classification", "SCRATCH")
        if classification == "WIN":
            p["wins"] += 1
        elif classification == "LOSS":
            p["losses"] += 1

        pnl = record.get("pnl", 0.0)
        conf = record.get("confidence") or 0.0
        p["_pnl_sum"] += pnl
        p["_conf_sum"] += conf
        p["_count"] += 1
        p["avg_pnl"] = round(p["_pnl_sum"] / p["_count"], 4)
        p["avg_confidence"] = round(p["_conf_sum"] / p["_count"], 4)

    # Clean internal accumulators from JSON output
    for cat in perf:
        for key in list(perf[cat].keys()):
            if key.startswith("_"):
                del perf[cat][key]


def update_totals(memory: dict, settled_records: list):
    memory["total_trades"] += len(settled_records)
    memory["total_wins"] += sum(1 for r in settled_records if r.get("classification") == "WIN")
    memory["total_losses"] += sum(1 for r in settled_records if r.get("classification") == "LOSS")
    memory["total_pnl"] = round(
        memory["total_pnl"] + sum(r.get("pnl", 0) for r in settled_records), 4
    )


def update_memory(settled_records: list, postmortem_results: list) -> dict:
    memory = load_memory()

    update_failure_patterns(memory, postmortem_results)
    update_category_performance(memory, settled_records)
    update_totals(memory, settled_records)

    save_memory(memory)

    # Check for systemic issues
    systemic = [p for p in memory["failure_patterns"] if p.get("is_systemic")]
    if systemic:
        logger.warning(f"SYSTEMIC ISSUES DETECTED ({len(systemic)}):")
        for p in systemic:
            logger.warning(f"  {p['failure_mode']}: {p['count']} occurrences")

    return memory


def print_memory_digest(memory: dict, new_lessons: int):
    print(f"\n--- LESSONS LEARNED ---")
    print(f"Total wins/losses: {memory['total_wins']}/{memory['total_losses']}")
    print(f"Total PnL: ${memory['total_pnl']:.2f}")

    systemic = [p for p in memory["failure_patterns"] if p.get("is_systemic")]
    if systemic:
        print(f"\n*** SYSTEMIC ISSUES ({len(systemic)}) — REQUIRES REVIEW ***")
        for p in systemic:
            print(f"  {p['failure_mode']}: {p['count']} times")
            for fix in p["suggested_fixes"][:2]:
                print(f"    → {fix}")

    if memory["category_performance"]:
        print("\nCategory performance:")
        for cat, stats in sorted(memory["category_performance"].items()):
            total = stats["wins"] + stats["losses"]
            if total > 0:
                win_rate = stats["wins"] / total
                print(f"  {cat:<20} {stats['wins']}W/{stats['losses']}L ({win_rate:.0%}) avg_pnl=${stats['avg_pnl']:.2f}")


def main(settled_records: list = None, postmortem_results: list = None):
    if settled_records is None:
        settled_records = []
    if postmortem_results is None:
        postmortem_results = []

    memory = update_memory(settled_records, postmortem_results)
    new_lessons = len(postmortem_results)
    logger.info(f"System memory updated: {new_lessons} new lessons, {len([p for p in memory['failure_patterns'] if p.get('is_systemic')])} systemic issues")
    return memory


if __name__ == "__main__":
    memory = main()
    print(json.dumps(memory, indent=2))
