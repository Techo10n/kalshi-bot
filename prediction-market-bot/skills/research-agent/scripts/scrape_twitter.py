"""
Step 1: Scrape Twitter API v2 for market-relevant tweets.

Reads:  data/scan_results.json
Writes: data/raw_twitter.json

Requires: TWITTER_BEARER_TOKEN env var (skips gracefully if absent).
"""

import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    _env = Path(__file__).parents[3] / ".env.local"
    if not _env.exists():
        _env = Path(__file__).parents[3] / ".env"
    load_dotenv(_env, override=False)
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
SCAN_RESULTS = DATA_DIR / "scan_results.json"
OUTPUT = DATA_DIR / "raw_twitter.json"

TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

STOP_WORDS = {
    "will", "the", "a", "an", "in", "of", "to", "by", "for", "at", "on",
    "be", "is", "are", "was", "were", "has", "have", "had", "do", "does",
    "did", "can", "could", "would", "should", "may", "might", "must",
    "this", "that", "these", "those", "it", "its", "or", "and", "but",
    "with", "from", "than", "more", "most", "any", "all", "not", "no",
    "end", "close", "above", "below", "between", "least", "most", "than",
    "win", "wins", "winning",
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


def twitter_request(url: str, headers: dict, params: dict, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
        except requests.RequestException as e:
            logger.warning(f"Request error (attempt {attempt + 1}): {e}")
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            reset_ts = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
            wait = max(reset_ts - time.time(), 1) + random.uniform(0, 2)
            logger.warning(f"Rate limited. Waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait)
        elif resp.status_code == 401:
            logger.error("Twitter 401 Unauthorized — check TWITTER_BEARER_TOKEN")
            return None
        else:
            logger.warning(f"Twitter {resp.status_code}: {resp.text[:200]}")
            return None
    return None


def scrape_twitter(markets: list) -> dict:
    token = os.environ.get("TWITTER_BEARER_TOKEN")
    if not token:
        logger.warning("TWITTER_BEARER_TOKEN not set — skipping Twitter scraping")
        return {}

    headers = {"Authorization": f"Bearer {token}"}
    start_time = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    results = {}
    for market in markets:
        ticker = market["ticker"]
        title = market.get("title", "")
        keywords = extract_keywords(title)

        if not keywords:
            logger.info(f"  {ticker}: no keywords extracted, skipping")
            results[ticker] = {"keywords": [], "tweets": []}
            continue

        query = " ".join(keywords) + " -is:retweet lang:en"
        params = {
            "query": query,
            "max_results": 100,
            "start_time": start_time,
            "tweet.fields": "created_at,public_metrics,text",
        }

        logger.info(f"  Fetching tweets for {ticker}: query={query!r}")
        data = twitter_request(TWITTER_SEARCH_URL, headers, params)

        tweets = []
        if data and "data" in data:
            tweets = data["data"]
            logger.info(f"    Got {len(tweets)} tweets")
        else:
            logger.info(f"    No tweets found")

        results[ticker] = {
            "keywords": keywords,
            "query": query,
            "tweets": tweets,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        # Respect rate limits: ~1 req/sec
        time.sleep(1.1)

    return results


def main():
    if not SCAN_RESULTS.exists():
        logger.error(f"scan_results.json not found at {SCAN_RESULTS}")
        raise FileNotFoundError(str(SCAN_RESULTS))

    markets = json.loads(SCAN_RESULTS.read_text())
    logger.info(f"Scraping Twitter for {len(markets)} markets...")

    results = scrape_twitter(markets)

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved raw Twitter data → {OUTPUT}")

    total_tweets = sum(len(v.get("tweets", [])) for v in results.values())
    logger.info(f"Total tweets collected: {total_tweets}")
    return results


if __name__ == "__main__":
    main()
