---
name: prediction-agent
description: >
  Generates probability predictions and trade signals for Kalshi markets using an XGBoost
  model blended with Claude LLM calibration. Trigger this skill when the user says things
  like "run predictions", "predict markets", "what's the true probability", "calibrate odds",
  "should I bet on", "generate signals", "model the markets", or "what does the model think".
  Reads from data/research_results.json and data/scan_results.json.
  Only outputs predictions with confidence >= 0.65.
---

# Prediction Agent

Combines market features and sentiment data into calibrated probability estimates using a
two-stage approach: XGBoost baseline + Claude LLM calibration. Only surfaces predictions
where `confidence >= 0.65`.

## When to Use

Run after market-scan-agent and research-agent have completed. Invoke when:
- You want a model-driven probability for whether a market resolves YES
- You want to generate BUY_YES / BUY_NO / PASS signals
- You need calibrated confidence scores before executing trades
- You have 50+ settled outcomes and want to retrain the XGBoost model

## Prerequisites

1. `data/scan_results.json` — from market-scan-agent
2. `data/research_results.json` — from research-agent
3. `ANTHROPIC_API_KEY` env var — for LLM calibration step

## Pipeline Overview

```
Step 1: build_features.py    → data/features.json
Step 2: train_xgboost.py     → data/xgboost_model.json  (or uses market price as prior)
Step 3: calibrate_with_llm.py → (intermediate, folds into run_prediction.py)
Step 4: evaluate_confidence.py → (intermediate, folds into run_prediction.py)
Step 5: run_prediction.py    → data/predictions.json
```

Run the full pipeline:

```bash
python skills/prediction-agent/scripts/run_prediction.py
```

## Confidence Threshold

**Only markets with `confidence >= 0.65` appear in `data/predictions.json`.**
Markets below this threshold are logged but not output. This prevents the execution agent
from acting on weak signals.

## Step-by-Step Breakdown

### Step 1 — build_features.py
Builds a feature vector per market:

**Market features:** `yes_price`, `spread_cents`, `volume_24h`, `open_interest`,
`hours_to_close`, `anomaly_score`, `is_near_50`

**Sentiment features:** `bullish_score`, `bearish_score`, `sentiment_volume`,
`narrative_edge`, `has_narrative_flag`

**Derived features:** `price_momentum`, `liquidity_ratio`, `time_pressure`

### Step 2 — train_xgboost.py
- If `data/historical_results.json` exists (≥1 settled outcome): trains a binary classifier
- If no historical data: uses `xgb_probability = yes_price` (market price as naive prior)
- Prints a clear warning when using fallback mode

### Step 3 — calibrate_with_llm.py
- Calls `claude-sonnet-4-20250514` with market context
- Prompts for: `{"llm_probability": float, "reasoning": string, "confidence": float, "signal": "BUY_YES"|"BUY_NO"|"PASS"}`
- Final probability = `0.6 × xgb_probability + 0.4 × llm_probability`

### Step 4 — evaluate_confidence.py
```
confidence = (abs(final_probability - yes_price) * 2) * sentiment_alignment
```
Where `sentiment_alignment` = 1.2 (model+narrative agree), 0.8 (disagree), 1.0 (neutral).
Capped at 1.0. Markets below 0.65 are filtered out.

## Output Format

`data/predictions.json`:

```json
[
  {
    "ticker": "SERIES-DATE-THRESHOLD",
    "title": "Market title",
    "yes_price": 0.42,
    "xgb_probability": 0.67,
    "llm_probability": 0.71,
    "final_probability": 0.692,
    "confidence": 0.71,
    "signal": "BUY_YES",
    "reasoning": "Claude's reasoning string",
    "edge": 0.272
  }
]
```

Sorted by `confidence` descending.

## Notes

- `ANTHROPIC_API_KEY` is required for LLM calibration. Without it, `calibrate_with_llm.py`
  will skip LLM and use `final_probability = xgb_probability`.
- XGBoost model trains on `data/historical_results.json` — collect settled outcomes to improve.
- See `references/model-notes.md` for historical_results schema and failure mode analysis.
