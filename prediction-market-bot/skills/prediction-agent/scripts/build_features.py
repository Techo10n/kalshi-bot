"""
Step 1: Build feature vectors for each market.

Reads:  data/scan_results.json, data/research_results.json
Writes: data/features.json
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
OUTPUT = DATA_DIR / "features.json"


def load_json(path):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    logger.warning(f"{path} not found")
    return []


def build_features(scan: list, research: list) -> list:
    # Index research by ticker for O(1) lookup
    research_index = {r["ticker"]: r for r in research}

    feature_rows = []
    for market in scan:
        ticker = market["ticker"]
        r = research_index.get(ticker, {})

        yes_price = float(market.get("yes_bid", 0.5))
        yes_ask = float(market.get("yes_ask", yes_price + 0.02))
        spread_cents = float(market.get("spread_cents", (yes_ask - yes_price) * 100))
        volume_24h = float(market.get("volume_24h", 0))
        open_interest = float(market.get("open_interest", 1))
        hours_to_close = float(market.get("hours_to_close", 720))
        anomaly_score = int(market.get("anomaly_score", 0))
        is_near_50 = bool(0.35 <= yes_price <= 0.65)

        bullish_score = float(r.get("bullish_score", 0.0))
        bearish_score = float(r.get("bearish_score", 0.0))
        sentiment_volume = int(r.get("sentiment_volume", 0))
        narrative_edge = float(r.get("narrative_edge", 0.0))
        has_narrative_flag = bool(r.get("narrative_flags"))

        # Derived features
        # price_momentum: difference from previous bid (use 0 if not available)
        previous_yes_bid = float(market.get("previous_yes_bid", yes_price))
        price_momentum = yes_price - previous_yes_bid

        # liquidity_ratio: volume relative to open interest
        liquidity_ratio = volume_24h / max(open_interest, 1.0)

        # time_pressure: higher when market closes soon (1/hours avoids div-by-zero)
        time_pressure = 1.0 / max(hours_to_close, 0.1)

        features = {
            # Market features
            "yes_price": yes_price,
            "spread_cents": spread_cents,
            "volume_24h": volume_24h,
            "open_interest": open_interest,
            "hours_to_close": hours_to_close,
            "anomaly_score": anomaly_score,
            "is_near_50": int(is_near_50),
            # Sentiment features
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
            "sentiment_volume": sentiment_volume,
            "narrative_edge": narrative_edge,
            "has_narrative_flag": int(has_narrative_flag),
            # Derived features
            "price_momentum": price_momentum,
            "liquidity_ratio": liquidity_ratio,
            "time_pressure": time_pressure,
        }

        feature_rows.append({
            "ticker": ticker,
            "title": market.get("title", ""),
            "yes_price": yes_price,
            "features": features,
        })

    logger.info(f"Built feature vectors for {len(feature_rows)} markets")
    return feature_rows


def main():
    scan = load_json(DATA_DIR / "scan_results.json")
    research = load_json(DATA_DIR / "research_results.json")

    if not scan:
        logger.error("scan_results.json is empty — run market-scan-agent first")
        raise FileNotFoundError("scan_results.json required")

    if not research:
        logger.warning("research_results.json is empty — sentiment features will be 0")

    rows = build_features(scan, research)

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(rows, indent=2))
    logger.info(f"Saved features → {OUTPUT}")
    return rows


if __name__ == "__main__":
    main()
