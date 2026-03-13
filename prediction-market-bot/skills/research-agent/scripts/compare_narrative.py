"""
Step 5: Compare sentiment direction against market price and flag narrative divergence.

Reads:  data/scan_results.json, data/sentiment_scores.json,
        data/raw_twitter.json, data/raw_reddit.json, data/raw_rss.json
Writes: data/research_results.json
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
OUTPUT = DATA_DIR / "research_results.json"

BULLISH_THRESHOLD = 0.6
BEARISH_THRESHOLD = 0.6
UNDERPRICED_MAX = 0.45
OVERPRICED_MIN = 0.55


def load_json(path):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    logger.warning(f"{path} not found — treating as empty")
    return {}


def get_raw_samples(ticker: str, twitter_data: dict, reddit_data: dict, rss_data: dict, n: int = 3) -> list:
    """Collect up to n most-relevant text snippets for the market."""
    candidates = []

    tw = twitter_data.get(ticker, {})
    for tweet in tw.get("tweets", [])[:10]:
        text = tweet.get("text", "").strip()
        if text:
            candidates.append(text)

    rd = reddit_data.get(ticker, {})
    for post in rd.get("posts", [])[:5]:
        title = post.get("title", "").strip()
        if title:
            candidates.append(title)

    rs = rss_data.get(ticker, {})
    for article in rs.get("articles", [])[:5]:
        headline = article.get("title", "").strip()
        if headline:
            candidates.append(headline)

    return candidates[:n]


def implied_sentiment_probability(bullish_score: float, bearish_score: float) -> float:
    """
    Convert bullish/bearish scores into an implied probability.
    Uses a simple linear mapping where equal scores → 0.5.
    """
    total = bullish_score + bearish_score
    if total == 0:
        return 0.5
    return bullish_score / total


def compare_narrative(markets: list, sentiment: dict, twitter_data: dict, reddit_data: dict, rss_data: dict) -> list:
    results = []

    for market in markets:
        ticker = market["ticker"]
        yes_price = market.get("yes_bid", market.get("yes_price", 0.5))

        scores = sentiment.get(ticker, {})
        bullish_score = scores.get("bullish_score", 0.0)
        bearish_score = scores.get("bearish_score", 0.0)
        sentiment_volume = scores.get("sentiment_volume", 0)

        flags = []
        if bullish_score > BULLISH_THRESHOLD and yes_price < UNDERPRICED_MAX:
            flags.append("NARRATIVE_BULLISH_UNDERPRICED")
        if bearish_score > BEARISH_THRESHOLD and yes_price > OVERPRICED_MIN:
            flags.append("NARRATIVE_BEARISH_OVERPRICED")

        implied_prob = implied_sentiment_probability(bullish_score, bearish_score)
        narrative_edge = round(abs(implied_prob - yes_price), 4)

        raw_sample = get_raw_samples(ticker, twitter_data, reddit_data, rss_data)

        result = {
            "ticker": ticker,
            "title": market.get("title", ""),
            "yes_price": yes_price,
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
            "sentiment_volume": sentiment_volume,
            "implied_sentiment_probability": round(implied_prob, 4),
            "narrative_flags": flags,
            "narrative_edge": narrative_edge,
            "raw_sample": raw_sample,
        }
        results.append(result)

        flag_str = ", ".join(flags) if flags else "none"
        logger.info(
            f"  {ticker}: yes={yes_price:.2f} bull={bullish_score:.2f} "
            f"bear={bearish_score:.2f} edge={narrative_edge:.2f} flags=[{flag_str}]"
        )

    # Sort by narrative_edge descending so highest-edge markets come first
    results.sort(key=lambda x: x["narrative_edge"], reverse=True)
    return results


def main():
    scan = load_json(DATA_DIR / "scan_results.json")
    if not scan:
        logger.error("scan_results.json is empty or missing")
        raise FileNotFoundError("scan_results.json required")

    sentiment = load_json(DATA_DIR / "sentiment_scores.json")
    twitter_data = load_json(DATA_DIR / "raw_twitter.json")
    reddit_data = load_json(DATA_DIR / "raw_reddit.json")
    rss_data = load_json(DATA_DIR / "raw_rss.json")

    logger.info(f"Comparing narrative vs price for {len(scan)} markets...")
    results = compare_narrative(scan, sentiment, twitter_data, reddit_data, rss_data)

    flagged = [r for r in results if r["narrative_flags"]]
    logger.info(f"Markets with narrative flags: {len(flagged)}/{len(results)}")

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved research results → {OUTPUT}")
    return results


if __name__ == "__main__":
    main()
