"""
Step 2: Train XGBoost binary classifier on historical settled outcomes.

Reads:  data/features.json, data/historical_results.json (optional)
Writes: data/xgboost_model.json (if training data exists)
        Returns: dict of {ticker -> xgb_probability}

If historical_results.json does not exist or has 0 rows, uses market price
as the naive prior (xgb_probability = yes_price) and prints a clear warning.
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
MODEL_PATH = DATA_DIR / "xgboost_model.json"

FEATURE_NAMES = [
    "yes_price", "spread_cents", "volume_24h", "open_interest",
    "hours_to_close", "anomaly_score", "is_near_50",
    "bullish_score", "bearish_score", "sentiment_volume",
    "narrative_edge", "has_narrative_flag",
    "price_momentum", "liquidity_ratio", "time_pressure",
]

XGB_PARAMS = {
    "max_depth": 4,
    "n_estimators": 100,
    "learning_rate": 0.1,
    "use_label_encoder": False,
    "eval_metric": "logloss",
    "random_state": 42,
}


def load_json(path):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    return None


def features_to_row(feature_dict: dict) -> list:
    return [feature_dict.get(f, 0.0) for f in FEATURE_NAMES]


def train_model(historical: list) -> object:
    try:
        import xgboost as xgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import log_loss
    except ImportError as e:
        logger.error(f"Missing dependency: {e}. Run: pip install xgboost scikit-learn")
        raise

    X = []
    y = []
    for record in historical:
        fv = record.get("feature_vector", {})
        outcome = record.get("outcome")
        if outcome is None or not fv:
            continue
        X.append(features_to_row(fv))
        y.append(int(outcome))

    if len(X) < 10:
        logger.warning(f"Only {len(X)} training samples — model may underfit. Need 500+.")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = xgb.XGBClassifier(**XGB_PARAMS)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    y_pred_proba = model.predict_proba(X_test)[:, 1]
    loss = log_loss(y_test, y_pred_proba)
    logger.info(f"XGBoost trained: {len(X_train)} train / {len(X_test)} test, log_loss={loss:.4f}")

    return model


def predict_with_model(model, features_data: list) -> dict:
    try:
        import numpy as np
    except ImportError:
        logger.error("numpy required: pip install numpy")
        raise

    X = [features_to_row(row["features"]) for row in features_data]
    probas = model.predict_proba(X)[:, 1]
    return {row["ticker"]: float(p) for row, p in zip(features_data, probas)}


def predict_naive_prior(features_data: list) -> dict:
    """Fallback: use market price as XGBoost prior."""
    return {row["ticker"]: row["features"]["yes_price"] for row in features_data}


def main():
    features_data = load_json(DATA_DIR / "features.json")
    if not features_data:
        logger.error("features.json not found — run build_features.py first")
        raise FileNotFoundError("features.json required")

    historical = load_json(DATA_DIR / "historical_results.json")

    if not historical or len(historical) == 0:
        print(
            "\n[WARNING] No historical data — using market price as XGBoost prior.\n"
            "Collect settled market outcomes to train the model.\n"
            "See references/model-notes.md for historical_results.json schema.\n"
        )
        probs = predict_naive_prior(features_data)
        logger.info(f"Naive prior applied to {len(probs)} markets")
        return probs

    logger.info(f"Training XGBoost on {len(historical)} historical outcomes...")
    try:
        model = train_model(historical)
        model.save_model(str(MODEL_PATH))
        logger.info(f"Model saved → {MODEL_PATH}")
        probs = predict_with_model(model, features_data)
    except Exception as e:
        logger.warning(f"XGBoost training failed: {e}. Falling back to naive prior.")
        probs = predict_naive_prior(features_data)

    return probs


if __name__ == "__main__":
    result = main()
    print(f"\nXGBoost probabilities for {len(result)} markets:")
    for ticker, p in sorted(result.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {ticker:<40} {p:.3f}")
