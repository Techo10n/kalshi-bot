"""
Step 2: Apply quarter-Kelly criterion to size each position.

Reads:  data/predictions.json, data/portfolio_state.json
Writes: data/sized_positions.json
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
OUTPUT = DATA_DIR / "sized_positions.json"

KELLY_MULTIPLIER = 0.25
MAX_SINGLE_TRADE_PCT = 0.05
MIN_BET_DOLLARS = 10.0
MIN_CONFIDENCE = 0.65
MIN_EDGE = 0.05


def kelly_fraction(edge: float, confidence: float, yes_price: float, signal: str) -> float:
    """
    Fractional Kelly for binary bet.
    For YES: kelly = (edge * confidence) / (1 - yes_price)
    For NO:  kelly = (edge * confidence) / yes_price
    """
    if signal == "BUY_YES":
        denom = 1.0 - yes_price
    elif signal == "BUY_NO":
        denom = yes_price
    else:
        return 0.0

    if denom <= 0:
        return 0.0
    return (abs(edge) * confidence) / denom


def size_positions(predictions: list, portfolio_state: dict) -> list:
    available = portfolio_state.get("available_balance", 0.0)
    portfolio_value = portfolio_state.get("portfolio_value", available)
    blocked = portfolio_state.get("blocked", False)

    if blocked:
        reason = portfolio_state.get("block_reason", "exposure limit exceeded")
        logger.warning(f"Execution blocked: {reason}. No positions sized.")
        return []

    sized = []
    for pred in predictions:
        ticker = pred["ticker"]
        confidence = pred.get("confidence", 0.0)
        edge = pred.get("edge", 0.0)
        signal = pred.get("signal", "PASS")
        yes_price = pred.get("yes_price", 0.5)

        # Re-check hard rules
        if signal == "PASS":
            logger.info(f"  {ticker}: PASS signal — skipping")
            continue
        if confidence < MIN_CONFIDENCE:
            logger.info(f"  {ticker}: confidence {confidence:.3f} < {MIN_CONFIDENCE} — skipping")
            continue
        if abs(edge) < MIN_EDGE:
            logger.info(f"  {ticker}: edge {edge:.3f} < {MIN_EDGE} — skipping")
            continue

        kf = kelly_fraction(edge, confidence, yes_price, signal)
        kf_quarter = kf * KELLY_MULTIPLIER

        # Cap at 5% of portfolio
        max_bet = portfolio_value * MAX_SINGLE_TRADE_PCT
        bet_size = min(kf_quarter * available, max_bet)

        # Round to nearest $1
        bet_size = round(bet_size)

        if bet_size < MIN_BET_DOLLARS:
            logger.info(
                f"  {ticker}: bet_size ${bet_size:.0f} < ${MIN_BET_DOLLARS:.0f} minimum — skipping"
            )
            continue

        # Calculate contracts
        if signal == "BUY_YES":
            price_frac = yes_price
        else:
            price_frac = 1.0 - yes_price

        contracts = max(1, int(bet_size / price_frac))
        actual_cost = contracts * price_frac

        sized.append({
            "ticker": ticker,
            "title": pred.get("title", ""),
            "signal": signal,
            "bet_size": round(actual_cost, 2),
            "contracts": contracts,
            "yes_price": yes_price,
            "yes_price_cents": int(yes_price * 100),
            "kelly_fraction": round(kf, 4),
            "kelly_quarter": round(kf_quarter, 4),
            "final_probability": pred.get("final_probability", yes_price),
            "edge": edge,
            "confidence": confidence,
        })

        logger.info(
            f"  {ticker}: signal={signal} bet=${actual_cost:.2f} "
            f"contracts={contracts} kelly={kf:.4f} edge={edge:+.3f}"
        )

    return sized


def main(dry_run: bool = False):
    def load_json(path):
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text())
        return []

    predictions = load_json(DATA_DIR / "predictions.json")
    portfolio_state = load_json(DATA_DIR / "portfolio_state.json")

    if not predictions:
        logger.warning("No predictions found — nothing to size")
        result = []
    elif not portfolio_state:
        logger.error("portfolio_state.json not found — run check_risk.py first")
        result = []
    else:
        result = size_positions(predictions, portfolio_state)

    total_capital = sum(p["bet_size"] for p in result)
    logger.info(f"Sized {len(result)} positions, total capital: ${total_capital:.2f}")

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2))
    logger.info(f"Saved sized positions → {OUTPUT}")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = main(dry_run=args.dry_run)
    print(f"\nSized {len(result)} positions")
    for p in result:
        print(f"  {p['ticker']:<40} {p['signal']:<8} ${p['bet_size']:.2f} ({p['contracts']} contracts)")
