# Model Notes — Prediction Agent

## Why the 60/40 XGBoost / LLM Blend

The 60/40 split reflects the relative reliability of each signal source:

**XGBoost (60%):**
- Trained on actual resolved market outcomes with real feature vectors
- Statistically grounded — trained on data, not opinions
- Once we have 500+ samples, this should dominate
- Weakness: cold start (no data = falls back to market price as prior)

**LLM (40%):**
- Claude has broad world knowledge and can reason about event probability
- Useful for incorporating context that isn't captured in features (e.g. breaking news)
- Weakness: overconfident on political markets (see failure modes below)
- Weakness: doesn't know the current date / market close time without being told

The blend gives us the model's statistical grounding with LLM's contextual reasoning.
As `historical_results.json` grows, consider shifting to 70/30 or 80/20.

---

## historical_results.json Schema

This file is the training dataset for XGBoost. Every settled trade should append a row here.
The postmortem-agent writes to this file automatically after settlement.

```json
[
  {
    "ticker": "SERIES-DATE-THRESHOLD",
    "signal": "BUY_YES",
    "entry_price": 0.42,
    "final_probability": 0.69,
    "confidence": 0.73,
    "sentiment_scores": {
      "bullish_score": 0.71,
      "bearish_score": 0.18,
      "sentiment_volume": 143
    },
    "feature_vector": {
      "yes_price": 0.42,
      "spread_cents": 3.0,
      "volume_24h": 12400.0,
      "open_interest": 45000.0,
      "hours_to_close": 18.5,
      "anomaly_score": 3,
      "is_near_50": true,
      "bullish_score": 0.71,
      "bearish_score": 0.18,
      "sentiment_volume": 143,
      "narrative_edge": 0.29,
      "has_narrative_flag": true,
      "price_momentum": 0.05,
      "liquidity_ratio": 0.275,
      "time_pressure": 0.054
    },
    "outcome": 1,
    "pnl": 12.50,
    "settled_at": "2025-03-14T18:00:00Z"
  }
]
```

**Key fields:**
- `outcome`: 1 = YES resolved, 0 = NO resolved (binary classification target)
- `feature_vector`: must contain all features used in `build_features.py`
- `pnl`: realized profit/loss in dollars after fees
- `settled_at`: ISO 8601 UTC timestamp

---

## Feature Importance Interpretation

Once trained, inspect feature importance with:

```python
import xgboost as xgb
model = xgb.XGBClassifier()
model.load_model("data/xgboost_model.json")
import pandas as pd
importances = pd.Series(model.feature_importances_, index=feature_names)
print(importances.sort_values(ascending=False))
```

**Expected top features (hypothesis before training):**
1. `yes_price` — market consensus is strong signal
2. `time_pressure` — imminent markets have less time for correction
3. `narrative_edge` — sentiment divergence is actionable
4. `bullish_score` — directional signal from text
5. `liquidity_ratio` — high-liquidity markets are better priced

If `yes_price` dominates with importance > 0.8, the model is essentially just echoing
the market and needs more varied training data.

---

## Confidence Threshold Rationale (0.65)

The confidence formula is:
```
confidence = (abs(final_probability - yes_price) * 2) * sentiment_alignment
```

For confidence = 0.65:
- With neutral alignment (1.0): requires |edge| ≥ 0.325 (32.5 cent difference)
- With bullish alignment (1.2): requires |edge| ≥ 0.271
- With bearish alignment (0.8): requires |edge| ≥ 0.406

This is intentionally strict. We only bet when the model disagrees with the market
significantly AND sentiment confirms the direction.

**Calibration history:**
- If win rate above threshold < 55%: raise threshold to 0.70
- If win rate above threshold > 65%: can lower to 0.60 to capture more opportunities

---

## Known Failure Modes

### 1. LLM Overconfidence on Political Markets
**Pattern:** Claude assigns high probability to political outcomes based on news
narrative that doesn't translate to binary resolution (e.g. "will X win?" vs "will X
get more than 50%?").

**Fix:** Add a `category_discount` for political markets in the LLM prompt.
Force Claude to consider base rates before narrative.

### 2. XGBoost Underfit with <500 Samples
**Pattern:** With fewer than 500 training samples, XGBoost has insufficient data to
generalize. Feature importance will be noisy and predictions will regress toward 0.5.

**Fix:** Use market price as primary prior until 500 samples are collected.
The system does this automatically when `historical_results.json` is empty.

### 3. Sentiment Lag
**Pattern:** Sentiment is scraped at scan time (T+0). For fast-moving markets
(resolving within 6 hours), the news cycle has already moved on and our sentiment
is stale.

**Fix:** Apply a `recency_discount` in `evaluate_confidence.py` for markets with
`hours_to_close < 6` — reduce confidence by 20%.

### 4. Keyword Extraction Failures
**Pattern:** Markets with very specific titles (e.g. esoteric financial instruments,
obscure sports events) produce keywords that return zero Twitter/Reddit results.
Sentiment volume = 0, so `bullish_score = bearish_score = 0`, giving flat predictions.

**Fix:** For `sentiment_volume < 10`, set `sentiment_alignment = 0.9` (slight penalty
for data sparsity) in `evaluate_confidence.py`.

### 5. RSS-Only Signal Inflation
**Pattern:** When Twitter and Reddit creds are missing, only RSS is available.
RSS tends to be neutral/factual, so `bullish_score` and `bearish_score` both stay low.
The model may under-flag opportunities that Twitter would have caught.

**Fix:** Log `data_sources_used` in predictions.json. Down-weight confidence by 10%
when only RSS is available.

---

## Recommended Training Cadence

| Condition | Action |
|---|---|
| < 50 settled outcomes | Use market price as XGBoost prior. Focus on collecting data. |
| 50–200 settled outcomes | Train XGBoost but treat predictions as experimental. |
| 200–500 settled outcomes | XGBoost is becoming reliable. Monitor feature importance. |
| 500+ settled outcomes | Full production mode. Retrain weekly or on 50 new samples. |

The postmortem-agent checks this threshold and prints a retrain recommendation automatically.
