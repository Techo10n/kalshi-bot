---
name: risk-execution-agent
description: >
  Executes trades on Kalshi with risk management and position sizing. Trigger this skill
  when the user says things like "place bets", "execute trades", "run execution",
  "size positions", "place orders on Kalshi", "deploy capital", "trade the predictions",
  or "execute the signals". Reads from data/predictions.json. Requires Kalshi RSA auth.
  Always run with --dry-run first to preview orders before live execution.
---

# Risk Execution Agent

Places limit orders on Kalshi for markets that passed the prediction-agent's confidence
threshold. Enforces hard risk rules before touching the API.

## When to Use

Run after prediction-agent has produced `data/predictions.json`. Invoke when:
- You want to act on model predictions with real capital
- You want to preview what orders WOULD be placed (--dry-run)
- You need to check current portfolio exposure before trading

**Always test with `--dry-run` before live execution.**

## Prerequisites

1. `data/predictions.json` — from prediction-agent
2. `KALSHI_PRIVATE_KEY_PATH` — path to RSA private key file
3. `KALSHI_ACCESS_KEY` — your Kalshi API access key (from Kalshi settings)

## Pipeline Overview

```
Step 1: check_risk.py        → data/portfolio_state.json
Step 2: size_position.py     → data/sized_positions.json
Step 3: place_order.py       → data/execution_log.json
Step 4: monitor_position.py  → updates data/execution_log.json
```

Run with:

```bash
python skills/risk-execution-agent/scripts/run_execution.py --dry-run  # safe preview
python skills/risk-execution-agent/scripts/run_execution.py            # live execution
```

## Hard Risk Rules (enforced without exception)

| Rule | Value |
|---|---|
| Max single trade | 5% of bankroll |
| Min confidence | 0.65 (re-checked even if predictions.json passed it) |
| Min edge | 0.05 (5 cents) |
| Max total exposure | 20% of bankroll |
| Minimum bet size | $10 |
| Kelly multiplier | 0.25 (quarter Kelly) |

If any rule is violated, the trade is skipped and logged.

## Step-by-Step Breakdown

### Step 1 — check_risk.py
- Fetches portfolio balance and open positions from Kalshi API (RSA auth)
- Computes `exposure_ratio = total_exposure / portfolio_value`
- Blocks ALL new trades if `exposure_ratio > 0.20`
- Saves to `data/portfolio_state.json`

### Step 2 — size_position.py
- Applies quarter-Kelly criterion: `bet_size = kelly_fraction * 0.25 * available_balance`
- Caps at 5% of portfolio
- Rounds to nearest $1, minimum $10

### Step 3 — place_order.py
- Places limit orders via `POST /portfolio/orders`
- Uses `client_order_id = f"bot-{ticker}-{timestamp}"` for tracking
- Handles 400/403/429 errors with exponential backoff on rate limits
- Logs every attempt to `data/execution_log.json`

### Step 4 — monitor_position.py
- Polls `GET /portfolio/orders` every 60s for up to 10 minutes
- Cancels unfilled orders after 10 minutes
- Updates log with: "filled" / "partial" / "cancelled"

## Authentication

Kalshi uses RSA-256 request signing. Required env vars:
- `KALSHI_ACCESS_KEY` — your access key from Kalshi API settings
- `KALSHI_PRIVATE_KEY_PATH` — path to your RSA private key file (.pem)

See `references/kalshi-order-api.md` for the full auth implementation.

## Output Format

`data/execution_log.json`:

```json
[
  {
    "ticker": "SERIES-DATE-THRESHOLD",
    "order_id": "abc123",
    "client_order_id": "bot-TICKER-1703001600",
    "side": "yes",
    "contracts": 24,
    "price_cents": 42,
    "bet_size": 10.08,
    "timestamp": "2025-03-14T18:00:00Z",
    "status": "filled",
    "filled_price": 42,
    "filled_count": 24,
    "fees_paid": 0.10
  }
]
```
