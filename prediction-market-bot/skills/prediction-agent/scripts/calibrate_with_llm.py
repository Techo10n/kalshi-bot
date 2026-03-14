"""
Step 3: Calibrate XGBoost predictions with Claude LLM.

For each market, sends structured context to Claude and asks for a probability,
reasoning, confidence, and signal.

Final probability = 0.6 * xgb_probability + 0.4 * llm_probability

Requires: ANTHROPIC_API_KEY env var. Skips LLM step if absent, uses xgb only.
"""

import json
import logging
import os
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"

XGB_WEIGHT = 0.6
LLM_WEIGHT = 0.4

SYSTEM_PROMPT = """You are a prediction market analyst. Your job is to estimate the
probability that a binary market resolves YES, given market data and sentiment.

You must respond with ONLY a valid JSON object — no prose, no markdown, no extra text.
Format:
{"llm_probability": <float 0-1>, "reasoning": "<1-2 sentences>", "confidence": <float 0-1>, "signal": "<BUY_YES|BUY_NO|PASS>"}

Rules:
- signal is BUY_YES if llm_probability > market_yes_price + 0.07
- signal is BUY_NO if llm_probability < market_yes_price - 0.07
- signal is PASS otherwise
- confidence is how certain you are in your probability estimate (0=uncertain, 1=certain)
- Be conservative. Political markets are hard. If unsure, lean toward market price."""

USER_TEMPLATE = """Market: {title}
Current yes_price: {yes_price}
XGBoost model probability: {xgb_probability}
Bullish sentiment score: {bullish_score} (0-1, higher=more bullish)
Bearish sentiment score: {bearish_score} (0-1, higher=more bearish)
Hours until market closes: {hours_to_close}
Narrative flags: {narrative_flags}
{live_price_line}{weather_forecast_line}
Top 3 news/social snippets:
{snippets}

Based on all available information, estimate the probability this market resolves YES."""


def parse_llm_response(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks."""
    # Strip markdown code blocks if present
    text = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()

    try:
        data = json.loads(text)
        # Validate required fields
        prob = float(data.get("llm_probability", 0.5))
        prob = max(0.01, min(0.99, prob))
        conf = float(data.get("confidence", 0.5))
        conf = max(0.0, min(1.0, conf))
        signal = str(data.get("signal", "PASS")).upper()
        if signal not in ("BUY_YES", "BUY_NO", "PASS"):
            signal = "PASS"
        return {
            "llm_probability": prob,
            "reasoning": str(data.get("reasoning", ""))[:500],
            "confidence": conf,
            "signal": signal,
        }
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Failed to parse LLM response: {e} — response: {text[:200]}")
        return None


def call_claude(client, market: dict, xgb_prob: float, research: dict) -> dict:
    snippets = "\n".join(
        f"  {i+1}. {s}" for i, s in enumerate(research.get("raw_sample", [])[:3])
    ) or "  (no snippets available)"

    live_price_summary = research.get("live_price_summary")
    live_price_line = (
        f"LIVE ASSET PRICE: {live_price_summary}\n"
        if live_price_summary else ""
    )

    weather_forecast_summary = research.get("weather_forecast_summary")
    weather_forecast_line = (
        f"WEATHER FORECAST (GraphCast/Open-Meteo):\n{weather_forecast_summary}\n"
        if weather_forecast_summary else ""
    )

    user_msg = USER_TEMPLATE.format(
        title=market.get("title", ""),
        yes_price=market.get("yes_price", 0.5),
        xgb_probability=round(xgb_prob, 4),
        bullish_score=round(research.get("bullish_score", 0.0), 3),
        bearish_score=round(research.get("bearish_score", 0.0), 3),
        hours_to_close=round(market.get("hours_to_close", 720), 1),
        narrative_flags=research.get("narrative_flags", []),
        live_price_line=live_price_line,
        weather_forecast_line=weather_forecast_line,
        snippets=snippets,
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = response.content[0].text.strip()
        return parse_llm_response(text)
    except Exception as e:
        logger.warning(f"Claude API error: {e}")
        return None


def calibrate(features_data: list, xgb_probs: dict, research_list: list) -> dict:
    """
    Returns dict: {ticker -> {"llm_probability": float, "final_probability": float,
                               "reasoning": str, "llm_signal": str, "llm_confidence": float}}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping LLM calibration, using XGBoost only")
        return {
            row["ticker"]: {
                "llm_probability": xgb_probs.get(row["ticker"], row["features"]["yes_price"]),
                "final_probability": xgb_probs.get(row["ticker"], row["features"]["yes_price"]),
                "reasoning": "LLM skipped (no API key)",
                "llm_signal": "PASS",
                "llm_confidence": 0.5,
            }
            for row in features_data
        }

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        logger.error("anthropic not installed. Run: pip install anthropic")
        raise

    research_index = {r["ticker"]: r for r in research_list}
    results = {}

    for row in features_data:
        ticker = row["ticker"]
        xgb_prob = xgb_probs.get(ticker, row["features"]["yes_price"])
        research = research_index.get(ticker, {})

        logger.info(f"  LLM calibrating {ticker} (xgb={xgb_prob:.3f})...")
        llm_result = call_claude(client, row, xgb_prob, research)

        if llm_result:
            llm_prob = llm_result["llm_probability"]
            final_prob = round(XGB_WEIGHT * xgb_prob + LLM_WEIGHT * llm_prob, 4)
            results[ticker] = {
                "llm_probability": llm_prob,
                "final_probability": final_prob,
                "reasoning": llm_result["reasoning"],
                "llm_signal": llm_result["signal"],
                "llm_confidence": llm_result["confidence"],
            }
            logger.info(
                f"    xgb={xgb_prob:.3f} llm={llm_prob:.3f} final={final_prob:.3f} "
                f"signal={llm_result['signal']}"
            )
        else:
            # Parse failed — use XGBoost only
            results[ticker] = {
                "llm_probability": xgb_prob,
                "final_probability": xgb_prob,
                "reasoning": "LLM parse failed — using XGBoost only",
                "llm_signal": "PASS",
                "llm_confidence": 0.5,
            }

    return results


def main():
    def load_json(path):
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text())
        return []

    features_data = load_json(DATA_DIR / "features.json")
    if not features_data:
        raise FileNotFoundError("features.json not found — run build_features.py first")

    # Load xgb_probs from a temp file if run_prediction saved them
    xgb_probs_path = DATA_DIR / "xgb_probs.json"
    if xgb_probs_path.exists():
        xgb_probs = json.loads(xgb_probs_path.read_text())
    else:
        xgb_probs = {row["ticker"]: row["features"]["yes_price"] for row in features_data}
        logger.warning("xgb_probs.json not found — using market price as prior")

    research_list = load_json(DATA_DIR / "research_results.json")
    return calibrate(features_data, xgb_probs, research_list)


if __name__ == "__main__":
    results = main()
    print(f"\nCalibrated {len(results)} markets")
