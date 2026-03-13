"""
Step 4: Check if XGBoost retraining is recommended.

Reads:  data/historical_results.json
        data/system_memory.json

Prints a clear recommendation if:
- 50+ new samples since last training
- Any failure_mode has count >= 5

Does NOT auto-retrain. Human decision required.
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
RETRAIN_SAMPLE_THRESHOLD = 50
FAILURE_ALERT_THRESHOLD = 5


def check_retrain(memory: dict = None) -> dict:
    # Load historical results
    hist_path = DATA_DIR / "historical_results.json"
    if not hist_path.exists():
        return {"retrain_needed": False, "reason": "No historical data yet", "new_samples": 0}

    historical = json.loads(hist_path.read_text())
    total_samples = len(historical)

    # Load memory for last retrain count
    if memory is None:
        mem_path = DATA_DIR / "system_memory.json"
        memory = json.loads(mem_path.read_text()) if mem_path.exists() else {}

    last_retrain_count = memory.get("last_retrain_sample_count", 0)
    new_samples = total_samples - last_retrain_count

    retrain_needed = new_samples >= RETRAIN_SAMPLE_THRESHOLD
    failure_alerts = []

    # Check for high-count failure modes
    for pattern in memory.get("failure_patterns", []):
        if pattern.get("count", 0) >= FAILURE_ALERT_THRESHOLD:
            failure_alerts.append(pattern)

    return {
        "retrain_needed": retrain_needed,
        "total_samples": total_samples,
        "new_samples": new_samples,
        "threshold": RETRAIN_SAMPLE_THRESHOLD,
        "failure_alerts": failure_alerts,
    }


def print_retrain_report(result: dict):
    if result["retrain_needed"]:
        print(
            f"\n*** RETRAIN RECOMMENDED: {result['new_samples']} new samples available "
            f"(threshold: {result['threshold']}) ***"
        )
        print("Run: python skills/prediction-agent/scripts/train_xgboost.py")
        print(f"Total training samples will be: {result['total_samples']}")
    else:
        new = result.get("new_samples", 0)
        threshold = result.get("threshold", RETRAIN_SAMPLE_THRESHOLD)
        print(f"\nRetrain: NOT needed ({new}/{threshold} new samples)")

    if result.get("failure_alerts"):
        print(f"\n*** FAILURE MODE ALERTS ({len(result['failure_alerts'])}) ***")
        for alert in result["failure_alerts"]:
            print(f"\n  {alert['failure_mode']} occurred {alert['count']} times:")
            for fix in alert.get("suggested_fixes", [])[:2]:
                print(f"    → {fix}")
            print(f"  Examples: {', '.join(alert.get('example_tickers', [])[:3])}")


def main(memory: dict = None):
    result = check_retrain(memory)
    print_retrain_report(result)
    return result


if __name__ == "__main__":
    main()
