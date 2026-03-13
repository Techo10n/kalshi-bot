---
name: postmortem-agent
description: >
  Analyzes settled trades, classifies failure modes, and updates the system's learning memory.
  Trigger this skill when the user says things like "run postmortem", "analyze losses",
  "what went wrong", "update the model", "review settled trades", "check today's PnL",
  "analyze my trades", or "what did we lose on". Reads execution_log.json and Kalshi
  settlement API. Writes to historical_results.json (feeds back to prediction-agent retraining).
---

# Postmortem Agent

Reviews all settled trades, computes PnL, classifies failures using Claude, and updates
the system's persistent memory. The main feedback loop that makes the bot smarter over time.

## When to Use

Run daily or after any batch of trades settles:
- After a trading session to review outcomes
- When you want to know what went wrong and why
- To check if the XGBoost model needs retraining
- To identify systemic failure patterns

## Pipeline Overview

```
Step 1: detect_losses.py   → data/historical_results.json (appended)
Step 2: analyze_failure.py → (intermediate: postmortem records per loss)
Step 3: update_memory.py   → data/system_memory.json
Step 4: retrain_trigger.py → prints retrain recommendation if 50+ new samples
Step 5: run_postmortem.py  → data/postmortem_log.json + session report
```

Run with:

```bash
python skills/postmortem-agent/scripts/run_postmortem.py
```

## What It Outputs

1. **`data/historical_results.json`** — Cumulative training dataset for XGBoost.
   Each row = one settled trade with features, outcome, and PnL.

2. **`data/system_memory.json`** — Persistent lessons learned:
   - Failure mode counts and examples
   - Per-category performance stats
   - Blacklisted patterns
   - Adjustment history

3. **`data/postmortem_log.json`** — Current session's analysis.

4. **Console report** with session summary and retrain recommendation.

## Failure Mode Taxonomy

| Code | Meaning |
|---|---|
| `SENTIMENT_WRONG` | Narrative didn't match actual outcome |
| `MODEL_OVERCONFIDENT` | High confidence but edge was false |
| `TIMING_BAD` | Right direction, wrong timeframe |
| `LIQUIDITY_IMPACT` | Spread moved against us at entry/exit |
| `BLACK_SWAN` | Genuinely unpredictable event |
| `DATA_MISSING` | Key information wasn't in our sources |

Any failure mode occurring 3+ times is flagged as `SYSTEMIC_ISSUE` requiring review.

## Retrain Trigger

If `historical_results.json` has 50+ new settled outcomes since last XGBoost training,
the agent prints a clear recommendation to retrain. It does NOT auto-retrain.

## Notes

- Requires `ANTHROPIC_API_KEY` for failure analysis (Step 2). If absent, failure
  classification is skipped and records are saved with `failure_mode = "UNANALYZED"`.
- If no settled trades are found, exits cleanly with a "nothing to postmortem" message.
- See `references/learning-schema.md` for the full historical_results.json schema.
