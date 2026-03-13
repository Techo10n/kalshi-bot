# Learning Schema — Postmortem Agent

## historical_results.json

The bridge between the postmortem-agent and the prediction-agent's XGBoost model.
Every settled trade appends a row here. This is the training dataset.

### Full Schema

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
      "is_near_50": 1,
      "bullish_score": 0.71,
      "bearish_score": 0.18,
      "sentiment_volume": 143,
      "narrative_edge": 0.29,
      "has_narrative_flag": 1,
      "price_momentum": 0.05,
      "liquidity_ratio": 0.275,
      "time_pressure": 0.054
    },
    "outcome": 1,
    "pnl": 12.50,
    "settled_at": "2025-03-14T18:00:00Z",
    "failure_mode": null,
    "failure_explanation": null
  }
]
```

### Key Fields

| Field | Type | Description |
|---|---|---|
| `outcome` | int | **1** = YES won, **0** = NO won (binary classification target) |
| `feature_vector` | dict | Must match all features in `build_features.py` exactly |
| `pnl` | float | Realized profit/loss in dollars (after fees) |
| `failure_mode` | string\|null | Set by analyze_failure.py for losses |
| `settled_at` | ISO 8601 | When the market settled |

### How PnL is Calculated

```python
# For YES bet:
pnl = (1.0 - entry_price) * contracts * outcome - entry_price * contracts * (1 - outcome)
     - fees_paid

# For NO bet:
pnl = (1.0 - (1.0 - entry_price)) * contracts * (1 - outcome) - ...

# Simplified: use filled_price and settled_price from Kalshi
pnl = (settled_price - entry_price) * contracts - fees_paid
```

---

## system_memory.json

Persistent lessons learned. Updated after every postmortem run.

```json
{
  "failure_patterns": [
    {
      "failure_mode": "SENTIMENT_WRONG",
      "count": 3,
      "example_tickers": ["TICKER-A", "TICKER-B"],
      "suggested_fixes": [
        "Reduce LLM weight for markets where sentiment_volume < 20",
        "Add sentiment lag discount for markets closing within 6 hours"
      ],
      "is_systemic": false
    }
  ],
  "category_performance": {
    "sports": {"wins": 12, "losses": 4, "avg_pnl": 3.20, "avg_confidence": 0.71},
    "politics": {"wins": 2, "losses": 8, "avg_pnl": -2.10, "avg_confidence": 0.68}
  },
  "model_adjustments": [
    {
      "date": "2025-03-14",
      "adjustment": "Raised confidence threshold from 0.65 to 0.70 for political markets",
      "reason": "Win rate on political markets was 20% — far below baseline"
    }
  ],
  "blacklisted_patterns": [
    "esoteric sports (obscure golf tournaments) — sentiment data too sparse",
    "same-day resolution markets — our data is too stale"
  ],
  "last_retrain_sample_count": 0,
  "total_trades": 20,
  "total_wins": 14,
  "total_losses": 6,
  "total_pnl": 45.20,
  "last_updated": "2025-03-14T18:00:00Z"
}
```

---

## Failure Mode → Suggested Fix Mapping

| Failure Mode | Immediate Fix | Long-Term Fix |
|---|---|---|
| `SENTIMENT_WRONG` | Down-weight sentiment for low-volume markets | Collect more training data per category |
| `MODEL_OVERCONFIDENT` | Raise confidence threshold to 0.70 | Calibrate model on held-out validation set |
| `TIMING_BAD` | Add time-remaining discount to confidence | Add time-series features to XGBoost |
| `LIQUIDITY_IMPACT` | Add minimum volume filter (e.g. >$5k/24h) | Track slippage at fill time |
| `BLACK_SWAN` | Accept as irreducible noise | Monitor position sizing |
| `DATA_MISSING` | Expand RSS/Twitter keyword matching | Add more data sources |

---

## Feedback Loop Diagram

```
postmortem-agent
     │
     ├─ detect_losses.py
     │       │
     │       └──► historical_results.json  ◄─── Training data accumulates
     │                    │
     │                    ▼
     ├─ retrain_trigger.py checks: 50+ new samples?
     │                    │
     │                    └──► YES: "Run train_xgboost.py"
     │                          │
     │                          ▼
     │              prediction-agent/train_xgboost.py
     │                          │
     │                          └──► Better XGBoost predictions
     │
     └─ analyze_failure.py + update_memory.py
               │
               └──► system_memory.json ──► Human reviews SYSTEMIC_ISSUE alerts
```

---

## When to Manually Intervene vs Let the System Self-Correct

**Let the system self-correct:**
- 1-2 losses of the same type (random noise)
- Win rate is 50-55% (near breakeven, needs more data)
- XGBoost needs retraining (just run train_xgboost.py)

**Manually intervene:**
- Any `failure_mode` with count >= 5 (SYSTEMIC_ISSUE)
- Win rate below 40% on any category for 2+ weeks
- Model is outputting `BUY_YES` on every market (feature leak or bug)
- `SENTIMENT_WRONG` count > 3 in same category (the source is noisy for that topic)
- Total PnL is negative after 50+ trades

**How to intervene:**
1. Review `system_memory.json` failure patterns
2. Adjust thresholds in the relevant script (e.g. raise `MIN_CONFIDENCE`)
3. Add the pattern to `blacklisted_patterns` if it's consistently bad
4. Run `train_xgboost.py` with updated `historical_results.json`
5. Document the adjustment in `model_adjustments`
