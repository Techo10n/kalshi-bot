"""
Step 3: Scrape RSS news feeds and match articles to markets by keyword.

Reads:  data/scan_results.json
Writes: data/raw_rss.json

No auth needed.
"""

import calendar
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
SCAN_RESULTS = DATA_DIR / "scan_results.json"
OUTPUT = DATA_DIR / "raw_rss.json"

RSS_FEEDS = [
    ("Reuters", "https://feeds.reuters.com/reuters/topNews"),
    ("AP", "https://feeds.apnews.com/rss/apf-topnews"),
    ("Google News", "https://news.google.com/rss"),
    ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
]

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


def keyword_overlap(text: str, keywords: list) -> int:
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw in text_lower)


def fetch_all_articles() -> list:
    try:
        import feedparser
    except ImportError:
        logger.error("feedparser not installed. Run: pip install feedparser")
        return []

    cutoff = time.time() - (LOOKBACK_HOURS * 3600)
    articles = []

    for source_name, url in RSS_FEEDS:
        try:
            logger.info(f"  Fetching {source_name} RSS...")
            feed = feedparser.parse(url)
            count = 0
            for entry in feed.entries:
                pub_ts = None
                if entry.get("published_parsed"):
                    pub_ts = calendar.timegm(entry.published_parsed)
                elif entry.get("updated_parsed"):
                    pub_ts = calendar.timegm(entry.updated_parsed)

                if pub_ts and pub_ts < cutoff:
                    continue

                articles.append({
                    "source": source_name,
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:500],
                    "link": entry.get("link", ""),
                    "published_ts": pub_ts,
                })
                count += 1

            logger.info(f"    {count} recent articles from {source_name}")
        except Exception as e:
            logger.warning(f"    Error fetching {source_name}: {e}")

    return articles


def match_articles_to_markets(markets: list, articles: list) -> dict:
    results = {}

    for market in markets:
        ticker = market["ticker"]
        title = market.get("title", "")
        keywords = extract_keywords(title)

        if not keywords:
            results[ticker] = {"keywords": [], "articles": []}
            continue

        matched = []
        for article in articles:
            text = article["title"] + " " + article.get("summary", "")
            overlap = keyword_overlap(text, keywords)
            if overlap >= 1:
                matched.append({**article, "keyword_overlap": overlap})

        matched.sort(key=lambda x: x["keyword_overlap"], reverse=True)

        logger.info(f"  {ticker}: {len(matched)} matching articles")
        results[ticker] = {
            "keywords": keywords,
            "articles": matched[:20],  # cap per market
        }

    return results


def main():
    if not SCAN_RESULTS.exists():
        logger.error(f"scan_results.json not found at {SCAN_RESULTS}")
        raise FileNotFoundError(str(SCAN_RESULTS))

    markets = json.loads(SCAN_RESULTS.read_text())
    logger.info(f"Fetching RSS feeds for {len(markets)} markets...")

    articles = fetch_all_articles()
    logger.info(f"Total RSS articles (last {LOOKBACK_HOURS}h): {len(articles)}")

    results = match_articles_to_markets(markets, articles)

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved raw RSS data → {OUTPUT}")

    total_matched = sum(len(v.get("articles", [])) for v in results.values())
    logger.info(f"Total matched article references: {total_matched}")
    return results


if __name__ == "__main__":
    main()
