"""
Step 2: Scrape Reddit via Arctic Shift (no auth, no API key required).

Arctic Shift is a public Reddit archive with a free search API.
https://arctic-shift.photon-reddit.com

Reads:  data/scan_results.json
Writes: data/raw_reddit.json
"""

import json
import logging
import random
import re
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
SCAN_RESULTS = DATA_DIR / "scan_results.json"
OUTPUT = DATA_DIR / "raw_reddit.json"

BASE_URL = "https://arctic-shift.photon-reddit.com/api"
SUBREDDITS = [
    "politics", "economics", "finance", "PredictionMarkets", "Kalshi",
    "golf", "sports", "investing", "news",
]
POSTS_PER_SUBREDDIT = 10
COMMENTS_PER_POST = 3
LOOKBACK = "2d"  # Arctic Shift relative time format

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


def arctic_get(endpoint: str, params: dict, max_retries: int = 3) -> list:
    url = BASE_URL + endpoint
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
        except requests.RequestException as e:
            logger.warning(f"Request error (attempt {attempt+1}): {e}")
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", [])
        elif resp.status_code == 429:
            reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 10))
            wait = max(reset - time.time(), 1) + random.uniform(0, 1)
            logger.warning(f"Rate limited — waiting {wait:.1f}s")
            time.sleep(wait)
        elif resp.status_code == 503:
            logger.warning(f"Arctic Shift unavailable (503), waiting 30s...")
            time.sleep(30)
        else:
            logger.warning(f"Arctic Shift {resp.status_code}: {resp.text[:150]}")
            return []
    return []


def fetch_posts(keywords: list, subreddit: str) -> list:
    # Use only the 2 most distinctive keywords — Arctic Shift title search is AND,
    # so too many terms produces zero results
    query = " ".join(keywords[:2])
    params = {
        "title": query,
        "subreddit": subreddit,
        "after": LOOKBACK,
        "limit": POSTS_PER_SUBREDDIT,
        "sort": "desc",
    }
    return arctic_get("/posts/search", params)


def fetch_comments(post_id: str) -> list:
    params = {
        "link_id": f"t3_{post_id}",
        "limit": COMMENTS_PER_POST,
    }
    raw = arctic_get("/comments/tree", params)
    comments = []
    for item in raw:
        if isinstance(item, dict) and item.get("kind") != "more":
            body = item.get("body", "").strip()
            if body:
                comments.append({
                    "body": body[:500],
                    "score": item.get("score", 0),
                })
    return comments


def scrape_reddit(markets: list) -> dict:
    results = {}

    for market in markets:
        ticker = market["ticker"]
        title = market.get("title", "")
        keywords = extract_keywords(title)

        if not keywords:
            logger.info(f"  {ticker}: no keywords, skipping")
            results[ticker] = {"keywords": [], "posts": []}
            continue

        logger.info(f"  Scraping Reddit for {ticker}: keywords={keywords}")
        posts_data = []

        for sub_name in SUBREDDITS:
            posts = fetch_posts(keywords, sub_name)
            for post in posts:
                post_id = post.get("id", "")
                comments = fetch_comments(post_id) if post_id else []
                posts_data.append({
                    "subreddit": sub_name,
                    "title": post.get("title", ""),
                    "selftext": post.get("selftext", "")[:1000],
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "created_utc": post.get("created_utc"),
                    "url": post.get("url", ""),
                    "comments": comments,
                })
            # Be polite to the free community API
            time.sleep(0.5)

        logger.info(f"    Got {len(posts_data)} posts across {len(SUBREDDITS)} subreddits")
        results[ticker] = {
            "keywords": keywords,
            "posts": posts_data,
        }

    return results


def main():
    if not SCAN_RESULTS.exists():
        logger.error(f"scan_results.json not found at {SCAN_RESULTS}")
        raise FileNotFoundError(str(SCAN_RESULTS))

    markets = json.loads(SCAN_RESULTS.read_text())
    logger.info(f"Scraping Reddit (Arctic Shift) for {len(markets)} markets...")

    results = scrape_reddit(markets)

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved raw Reddit data → {OUTPUT}")

    total_posts = sum(len(v.get("posts", [])) for v in results.values())
    logger.info(f"Total posts collected: {total_posts}")
    return results


if __name__ == "__main__":
    main()
