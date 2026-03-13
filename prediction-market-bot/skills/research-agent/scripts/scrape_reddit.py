"""
Step 2: Scrape Reddit for market-relevant posts and comments.

Reads:  data/scan_results.json
Writes: data/raw_reddit.json

Requires: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT env vars.
Skips gracefully if any are missing.
"""

import json
import logging
import os
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
SCAN_RESULTS = DATA_DIR / "scan_results.json"
OUTPUT = DATA_DIR / "raw_reddit.json"

SUBREDDITS = ["politics", "economics", "finance", "PredictionMarkets", "Kalshi"]
LOOKBACK_HOURS = 48

STOP_WORDS = {
    "will", "the", "a", "an", "in", "of", "to", "by", "for", "at", "on",
    "be", "is", "are", "was", "were", "has", "have", "had", "do", "does",
    "did", "can", "could", "would", "should", "may", "might", "must",
    "this", "that", "these", "those", "it", "its", "or", "and", "but",
    "with", "from", "than", "more", "most", "any", "all", "not", "no",
    "end", "close", "above", "below", "between", "least", "most", "than",
}


def extract_keywords(title: str, max_keywords: int = 5) -> list:
    title = re.sub(
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{4}\b',
        '', title, flags=re.IGNORECASE
    )
    title = re.sub(r'\bQ[1-4]\s*\d{4}\b', '', title)
    title = re.sub(r'\b\d{4}\b', '', title)
    title = re.sub(r'\b\d+\.?\d*%?\b', '', title)
    title = re.sub(r'[^\w\s]', ' ', title)
    words = title.lower().split()
    keywords = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    return keywords[:max_keywords]


def check_reddit_creds() -> bool:
    required = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        logger.warning(f"Missing Reddit env vars: {missing} — skipping Reddit scraping")
        return False
    return True


def scrape_reddit(markets: list) -> dict:
    if not check_reddit_creds():
        return {}

    try:
        import praw
    except ImportError:
        logger.error("praw not installed. Run: pip install praw")
        return {}

    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get("REDDIT_USER_AGENT", "kalshi-bot/1.0"),
    )

    cutoff = time.time() - (LOOKBACK_HOURS * 3600)
    results = {}

    for market in markets:
        ticker = market["ticker"]
        title = market.get("title", "")
        keywords = extract_keywords(title)

        if not keywords:
            logger.info(f"  {ticker}: no keywords, skipping")
            results[ticker] = {"keywords": [], "posts": []}
            continue

        query = " ".join(keywords)
        logger.info(f"  Scraping Reddit for {ticker}: query={query!r}")

        posts_data = []
        for sub_name in SUBREDDITS:
            try:
                sub = reddit.subreddit(sub_name)
                for post in sub.search(query, sort="hot", time_filter="week", limit=25):
                    if post.created_utc < cutoff:
                        continue

                    post.comments.replace_more(limit=0)
                    top_comments = []
                    for comment in list(post.comments)[:5]:
                        if hasattr(comment, "body"):
                            top_comments.append({
                                "body": comment.body[:500],
                                "score": comment.score,
                            })

                    posts_data.append({
                        "subreddit": sub_name,
                        "title": post.title,
                        "selftext": post.selftext[:1000],
                        "score": post.score,
                        "num_comments": post.num_comments,
                        "created_utc": post.created_utc,
                        "url": post.url,
                        "comments": top_comments,
                    })
            except Exception as e:
                logger.warning(f"    Error scraping r/{sub_name}: {e}")
                continue

        logger.info(f"    Got {len(posts_data)} posts across {len(SUBREDDITS)} subreddits")
        results[ticker] = {
            "keywords": keywords,
            "query": query,
            "posts": posts_data,
        }

    return results


def main():
    if not SCAN_RESULTS.exists():
        logger.error(f"scan_results.json not found at {SCAN_RESULTS}")
        raise FileNotFoundError(str(SCAN_RESULTS))

    markets = json.loads(SCAN_RESULTS.read_text())
    logger.info(f"Scraping Reddit for {len(markets)} markets...")

    results = scrape_reddit(markets)

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved raw Reddit data → {OUTPUT}")

    total_posts = sum(len(v.get("posts", [])) for v in results.values())
    logger.info(f"Total posts collected: {total_posts}")
    return results


if __name__ == "__main__":
    main()
