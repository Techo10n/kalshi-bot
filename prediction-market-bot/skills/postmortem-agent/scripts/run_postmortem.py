"""
Postmortem Agent — full pipeline orchestrator.

Steps:
  1. detect_losses.py    — settle trades, compute PnL
  2. analyze_failure.py  — classify failures with Claude
  3. update_memory.py    — update system_memory.json
  4. retrain_trigger.py  — check if retraining is needed

Output: data/postmortem_log.json + session report printed to console.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
POSTMORTEM_LOG = DATA_DIR / "postmortem_log.json"


def main():
    pipeline_start = time.time()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"\n=== POSTMORTEM REPORT [{today}] ===")

    # Step 1: Detect settled trades
    import detect_losses
    settled_records = detect_losses.main()

    if not settled_records:
        print("\nNo settled trades found. Nothing to postmortem.")
        print("(Run after trades have settled on Kalshi)")
        return

    wins = [r for r in settled_records if r.get("classification") == "WIN"]
    losses = [r for r in settled_records if r.get("classification") == "LOSS"]
    scratches = [r for r in settled_records if r.get("classification") == "SCRATCH"]
    today_pnl = sum(r.get("pnl", 0) for r in settled_records)

    # Step 2: Analyze failures
    import analyze_failure
    postmortem_results = analyze_failure.main(settled_records)

    # Step 3: Update memory
    import update_memory
    memory = update_memory.main(settled_records, postmortem_results)

    # Step 4: Check retrain trigger
    import retrain_trigger
    retrain_result = retrain_trigger.main(memory)

    # Save postmortem log
    postmortem_session = {
        "date": today,
        "settled_count": len(settled_records),
        "wins": len(wins),
        "losses": len(losses),
        "scratches": len(scratches),
        "pnl": round(today_pnl, 4),
        "new_lessons": len(postmortem_results),
        "systemic_issues": len([p for p in memory.get("failure_patterns", []) if p.get("is_systemic")]),
        "retrain_needed": retrain_result.get("retrain_needed", False),
        "postmortem_details": postmortem_results,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }

    # Load existing postmortem log and append
    existing_log = []
    if POSTMORTEM_LOG.exists():
        try:
            existing_log = json.loads(POSTMORTEM_LOG.read_text())
        except json.JSONDecodeError:
            existing_log = []

    existing_log.append(postmortem_session)
    DATA_DIR.mkdir(exist_ok=True)
    POSTMORTEM_LOG.write_text(json.dumps(existing_log, indent=2))

    # Print session summary
    elapsed = time.time() - pipeline_start
    print(f"\nSettled today: {len(settled_records)} trades")
    print(f"Wins: {len(wins)} | Losses: {len(losses)} | Scratch: {len(scratches)}")
    print(f"PnL today: ${today_pnl:.2f}")
    print(f"New lessons: {len(postmortem_results)}")

    systemic = [p for p in memory.get("failure_patterns", []) if p.get("is_systemic")]
    print(f"Systemic issues: {len(systemic)} (see system_memory.json)")

    update_memory.print_memory_digest(memory, len(postmortem_results))

    print(f"\nRetrain needed: {'YES' if retrain_result['retrain_needed'] else 'NO'}")
    if retrain_result["retrain_needed"]:
        print("  → Run: python skills/prediction-agent/scripts/train_xgboost.py")

    print(f"\nOutput: data/postmortem_log.json")
    print(f"Runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
