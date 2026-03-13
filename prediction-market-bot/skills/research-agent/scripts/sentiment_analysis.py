"""
Step 4: Score all collected text with cardiffnlp/twitter-roberta-base-sentiment-latest.

Reads:  data/raw_twitter.json, data/raw_reddit.json, data/raw_rss.json
Writes: data/sentiment_scores.json

Requires: pip install transformers torch
Model downloads ~500MB on first run, cached to ~/.cache/huggingface.
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
OUTPUT = DATA_DIR / "sentiment_scores.json"

MODEL_NAME = "cardiffnlp/twitter-roberta-base-sentiment-latest"
MAX_TEXT_LEN = 512  # model token limit (approximate chars)


def load_text_corpus(ticker: str, twitter_data: dict, reddit_data: dict, rss_data: dict) -> list:
    """Collect all text snippets for a given ticker across all sources."""
    corpus = []

    # Twitter
    tw = twitter_data.get(ticker, {})
    for tweet in tw.get("tweets", []):
        text = tweet.get("text", "").strip()
        if text:
            corpus.append({"text": text[:MAX_TEXT_LEN], "source": "twitter"})

    # Reddit
    rd = reddit_data.get(ticker, {})
    for post in rd.get("posts", []):
        title = post.get("title", "").strip()
        if title:
            corpus.append({"text": title[:MAX_TEXT_LEN], "source": "reddit_title"})
        for comment in post.get("comments", []):
            body = comment.get("body", "").strip()
            if body:
                corpus.append({"text": body[:MAX_TEXT_LEN], "source": "reddit_comment"})

    # RSS
    rs = rss_data.get(ticker, {})
    for article in rs.get("articles", []):
        headline = article.get("title", "").strip()
        if headline:
            corpus.append({"text": headline[:MAX_TEXT_LEN], "source": "rss"})

    return corpus


def run_sentiment(texts: list, pipe) -> list:
    """Run sentiment pipeline on a list of text strings. Returns scores list."""
    if not texts:
        return []
    try:
        results = pipe(texts, truncation=True, max_length=128)
        return results
    except Exception as e:
        logger.warning(f"Sentiment pipeline error: {e}")
        return [{"label": "neutral", "score": 0.5}] * len(texts)


def aggregate_sentiment(scored_items: list) -> dict:
    """
    scored_items: list of {"label": "positive"|"negative"|"neutral", "score": float}
    Returns: bullish_score, bearish_score, neutral_score, sentiment_volume
    """
    if not scored_items:
        return {"bullish_score": 0.0, "bearish_score": 0.0, "neutral_score": 0.0, "sentiment_volume": 0}

    positives = [x["score"] for x in scored_items if x["label"].lower() == "positive"]
    negatives = [x["score"] for x in scored_items if x["label"].lower() == "negative"]
    neutrals = [x["score"] for x in scored_items if x["label"].lower() == "neutral"]

    bullish = sum(positives) / len(positives) if positives else 0.0
    bearish = sum(negatives) / len(negatives) if negatives else 0.0
    neutral = sum(neutrals) / len(neutrals) if neutrals else 0.0

    return {
        "bullish_score": round(bullish, 4),
        "bearish_score": round(bearish, 4),
        "neutral_score": round(neutral, 4),
        "sentiment_volume": len(scored_items),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "neutral_count": len(neutrals),
    }


def main():
    # Load source data (files may be absent if source was skipped)
    def load_json(path):
        if Path(path).exists():
            return json.loads(Path(path).read_text())
        logger.warning(f"{path} not found — treating as empty")
        return {}

    twitter_data = load_json(DATA_DIR / "raw_twitter.json")
    reddit_data = load_json(DATA_DIR / "raw_reddit.json")
    rss_data = load_json(DATA_DIR / "raw_rss.json")

    # Get all tickers from any available source
    all_tickers = set(twitter_data) | set(reddit_data) | set(rss_data)
    if not all_tickers:
        logger.warning("No source data found. Run scraping steps first.")
        OUTPUT.write_text(json.dumps({}, indent=2))
        return {}

    logger.info(f"Loading sentiment model: {MODEL_NAME}")
    try:
        from transformers import pipeline
        pipe = pipeline(
            "text-classification",
            model=MODEL_NAME,
            top_k=1,
        )
        logger.info("Model loaded.")
    except Exception as e:
        logger.error(f"Failed to load sentiment model: {e}")
        logger.error("Install with: pip install transformers torch")
        raise

    results = {}
    for i, ticker in enumerate(sorted(all_tickers), 1):
        logger.info(f"[{i}/{len(all_tickers)}] Scoring sentiment for {ticker}...")
        corpus = load_text_corpus(ticker, twitter_data, reddit_data, rss_data)

        if not corpus:
            logger.info(f"  No text found for {ticker}")
            results[ticker] = aggregate_sentiment([])
            continue

        texts = [item["text"] for item in corpus]
        raw_scores = run_sentiment(texts, pipe)

        # Normalize label names (model may return "LABEL_0/1/2" or "positive/negative/neutral")
        label_map = {"LABEL_0": "negative", "LABEL_1": "neutral", "LABEL_2": "positive"}
        scored_items = []
        for raw in raw_scores:
            # pipeline with top_k=1 returns list of list
            item = raw[0] if isinstance(raw, list) else raw
            label = item["label"].lower()
            label = label_map.get(item["label"], label)
            scored_items.append({"label": label, "score": item["score"]})

        agg = aggregate_sentiment(scored_items)
        logger.info(
            f"  volume={agg['sentiment_volume']} "
            f"bullish={agg['bullish_score']:.2f} "
            f"bearish={agg['bearish_score']:.2f}"
        )
        results[ticker] = agg

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved sentiment scores → {OUTPUT}")
    return results


if __name__ == "__main__":
    main()
