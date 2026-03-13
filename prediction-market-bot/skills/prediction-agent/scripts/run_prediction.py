"""
Prediction Agent — full pipeline orchestrator.

Steps:
  1. build_features.py
  2. train_xgboost.py
  3. calibrate_with_llm.py
  4. evaluate_confidence.py
  5. Output data/predictions.json (sorted by confidence desc)

Only markets with confidence >= 0.65 are output.
"""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
OUTPUT = DATA_DIR / "predictions.json"
CONFIDENCE_THRESHOLD = 0.65


def check_prerequisites():
    for fname in ["scan_results.json", "research_results.json"]:
        if not (DATA_DIR / fname).exists():
            logger.error(
                f"{fname} not found.\n"
                "Run market-scan-agent, then research-agent first."
            )
            sys.exit(1)


def main():
    pipeline_start = time.time()
    check_prerequisites()

    print("\n=== PREDICTION AGENT PIPELINE ===")

    # Step 1: Build features
    t = time.time()
    logger.info("--- [Step 1: Build Features] START ---")
    import build_features
    features_data = build_features.main()
    logger.info(f"--- [Step 1: Build Features] DONE ({time.time()-t:.1f}s) ---")

    # Step 2: XGBoost predictions
    t = time.time()
    logger.info("--- [Step 2: XGBoost] START ---")
    import train_xgboost
    xgb_probs = train_xgboost.main()
    # Save for calibrate_with_llm.py standalone use
    (DATA_DIR / "xgb_probs.json").write_text(json.dumps(xgb_probs, indent=2))
    logger.info(f"--- [Step 2: XGBoost] DONE ({time.time()-t:.1f}s) ---")

    # Step 3: LLM calibration
    t = time.time()
    logger.info("--- [Step 3: LLM Calibration] START ---")
    import calibrate_with_llm
    research_list = json.loads((DATA_DIR / "research_results.json").read_text())
    calibrated = calibrate_with_llm.calibrate(features_data, xgb_probs, research_list)
    logger.info(f"--- [Step 3: LLM Calibration] DONE ({time.time()-t:.1f}s) ---")

    # Step 4: Confidence evaluation + filtering
    t = time.time()
    logger.info("--- [Step 4: Confidence Filter] START ---")
    import evaluate_confidence
    passing, filtered_out = evaluate_confidence.evaluate_confidence(
        features_data, calibrated, research_list
    )
    logger.info(
        f"--- [Step 4: Confidence Filter] DONE ({time.time()-t:.1f}s) | "
        f"{len(passing)} pass / {len(filtered_out)} filtered ---"
    )

    # Build final predictions — enrich with xgb_probability separately
    predictions = []
    for record in passing:
        ticker = record["ticker"]
        xgb_raw = xgb_probs.get(ticker, record["yes_price"])
        cal = calibrated.get(ticker, {})
        predictions.append({
            "ticker": ticker,
            "title": record["title"],
            "yes_price": record["yes_price"],
            "xgb_probability": round(xgb_raw, 4),
            "llm_probability": round(cal.get("llm_probability", xgb_raw), 4),
            "final_probability": record["final_probability"],
            "confidence": record["confidence"],
            "signal": record["signal"],
            "reasoning": record["reasoning"],
            "edge": record["edge"],
        })

    # Sort by confidence descending
    predictions.sort(key=lambda x: x["confidence"], reverse=True)

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(predictions, indent=2))

    elapsed = time.time() - pipeline_start
    print(f"\n=== PREDICTION COMPLETE ({elapsed:.1f}s) ===")
    print(f"Markets evaluated:    {len(features_data)}")
    print(f"Passed threshold:     {len(predictions)} (confidence >= {CONFIDENCE_THRESHOLD})")
    print(f"Filtered out:         {len(filtered_out)}")

    if predictions:
        print("\nTop predictions:")
        for p in predictions[:5]:
            print(
                f"  {p['ticker']:<40} signal={p['signal']:<8} "
                f"conf={p['confidence']:.2f} edge={p['edge']:+.3f}"
            )
    else:
        print("\nNo predictions passed the confidence threshold.")
        print("This is expected when using market price as prior (no historical data).")
        print("The model needs historical settled outcomes to generate strong signals.")

    print(f"\nOutput: data/predictions.json")
    return predictions


if __name__ == "__main__":
    main()
