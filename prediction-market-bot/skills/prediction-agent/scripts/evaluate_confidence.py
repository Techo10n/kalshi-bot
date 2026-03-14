"""
Step 4: Compute final confidence scores and filter markets below threshold.

confidence = (abs(final_probability - yes_price) * 2) * sentiment_alignment
  where sentiment_alignment:
    1.2 if model direction matches narrative direction
    0.8 if they disagree
    1.0 if neutral

Only markets with confidence >= 0.65 pass through.
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.50
ALIGNMENT_AGREE = 1.2
ALIGNMENT_DISAGREE = 0.8
ALIGNMENT_NEUTRAL = 1.0
WEATHER_CONFIDENCE_BONUS = 0.08  # extra confidence for weather markets with GraphCast data


def sentiment_alignment(
    final_probability: float,
    yes_price: float,
    bullish_score: float,
    bearish_score: float,
) -> float:
    """
    Check if model direction and sentiment direction agree.
    Model is bullish if final_probability > yes_price (predicts YES more likely).
    Sentiment is bullish if bullish_score > bearish_score.
    """
    model_bullish = final_probability > yes_price
    sent_bullish = bullish_score > bearish_score
    sent_neutral = abs(bullish_score - bearish_score) < 0.1

    if sent_neutral or (bullish_score == 0 and bearish_score == 0):
        return ALIGNMENT_NEUTRAL
    if model_bullish == sent_bullish:
        return ALIGNMENT_AGREE
    return ALIGNMENT_DISAGREE


def compute_confidence(
    final_probability: float,
    yes_price: float,
    bullish_score: float,
    bearish_score: float,
    is_weather_market: bool = False,
    has_weather_forecast: bool = False,
) -> float:
    edge = abs(final_probability - yes_price)
    alignment = sentiment_alignment(final_probability, yes_price, bullish_score, bearish_score)
    confidence = (edge * 2) * alignment
    # Weather markets with GraphCast forecast data get a confidence bonus — the forecast
    # is a high-quality, objective signal that reduces uncertainty.
    if is_weather_market and has_weather_forecast:
        confidence += WEATHER_CONFIDENCE_BONUS
    return min(confidence, 1.0)


def evaluate_confidence(
    features_data: list,
    calibrated: dict,
    research_list: list,
) -> tuple:
    """
    Returns (passing, filtered) — two lists of market dicts.
    passing: confidence >= threshold
    filtered: confidence < threshold
    """
    research_index = {r["ticker"]: r for r in research_list}
    passing = []
    filtered_out = []

    for row in features_data:
        ticker = row["ticker"]
        yes_price = row["features"]["yes_price"]
        cal = calibrated.get(ticker, {})
        final_probability = cal.get("final_probability", yes_price)
        research = research_index.get(ticker, {})
        bullish_score = research.get("bullish_score", 0.0)
        bearish_score = research.get("bearish_score", 0.0)
        is_weather_market = research.get("is_weather_market", False)
        has_weather_forecast = bool(research.get("weather_forecast_summary"))

        conf = compute_confidence(
            final_probability, yes_price, bullish_score, bearish_score,
            is_weather_market=is_weather_market,
            has_weather_forecast=has_weather_forecast,
        )

        record = {
            "ticker": ticker,
            "title": row.get("title", ""),
            "yes_price": yes_price,
            "xgb_probability": cal.get("final_probability", yes_price),  # before blend name fix
            "llm_probability": cal.get("llm_probability", yes_price),
            "final_probability": final_probability,
            "confidence": round(conf, 4),
            "signal": cal.get("llm_signal", "PASS"),
            "reasoning": cal.get("reasoning", ""),
            "edge": round(final_probability - yes_price, 4),
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
            "is_weather_market": is_weather_market,
        }

        if conf >= CONFIDENCE_THRESHOLD:
            passing.append(record)
        else:
            filtered_out.append(record)

    return passing, filtered_out


def main():
    def load_json(path):
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text())
        return []

    DATA_DIR = Path(__file__).parents[3] / "data"

    features_data = load_json(DATA_DIR / "features.json")
    research_list = load_json(DATA_DIR / "research_results.json")

    # This is normally called from run_prediction.py with calibrated data
    # Standalone: load from temp if available
    cal_path = DATA_DIR / "calibrated.json"
    if cal_path.exists():
        calibrated = json.loads(cal_path.read_text())
    else:
        calibrated = {}

    passing, filtered = evaluate_confidence(features_data, calibrated, research_list)
    logger.info(
        f"Confidence filter: {len(passing)} pass, {len(filtered)} filtered "
        f"(threshold={CONFIDENCE_THRESHOLD})"
    )
    return passing, filtered


DATA_DIR = Path(__file__).parents[3] / "data"

if __name__ == "__main__":
    main()
