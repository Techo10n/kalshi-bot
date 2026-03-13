# Source Patterns — Research Agent

Reference for Twitter API v2, PRAW, feedparser, credential handling, and keyword extraction.

---

## Twitter API v2

### Auth
Bearer token only (App-only auth). Set `TWITTER_BEARER_TOKEN` in environment.

```python
headers = {"Authorization": f"Bearer {os.environ['TWITTER_BEARER_TOKEN']}"}
```

### Recent Search Endpoint
```
GET https://api.twitter.com/2/tweets/search/recent
```

**Key parameters:**
| Param | Value | Notes |
|---|---|---|
| `query` | `"keyword1 keyword2 -is:retweet lang:en"` | Always exclude retweets |
| `max_results` | 10–100 | Free tier max 100 |
| `start_time` | ISO 8601 UTC | e.g. 24h ago |
| `tweet.fields` | `created_at,public_metrics,text` | Comma-separated |

**Free tier limits:**
- 500,000 tweets/month
- 1 request per second
- 100 tweets per request, 10 requests per 15-min window
- At 100 tweets × 20 markets = 2,000 tweets per full run (well within limit)

### Rate Limit Headers
```
x-rate-limit-limit: 500000
x-rate-limit-remaining: 499000
x-rate-limit-reset: 1703001600  # Unix timestamp
```

### Exponential Backoff Pattern
```python
import time, random

def twitter_request_with_backoff(url, headers, params, max_retries=3):
    for attempt in range(max_retries):
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            reset_time = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
            wait = max(reset_time - time.time(), 1) + random.uniform(0, 2)
            print(f"Rate limited. Waiting {wait:.1f}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait)
        else:
            print(f"Twitter error {resp.status_code}: {resp.text}")
            break
    return None
```

---

## PRAW (Python Reddit API Wrapper)

### Install
```bash
pip install praw
```

### Setup
```python
import praw

reddit = praw.Reddit(
    client_id=os.environ["REDDIT_CLIENT_ID"],
    client_secret=os.environ["REDDIT_CLIENT_SECRET"],
    user_agent=os.environ.get("REDDIT_USER_AGENT", "kalshi-bot/1.0"),
)
```

Get credentials at: https://www.reddit.com/prefs/apps (create a "script" app)

### Search Pattern
```python
subreddits = ["politics", "economics", "finance", "PredictionMarkets", "Kalshi"]

for sub_name in subreddits:
    sub = reddit.subreddit(sub_name)
    for post in sub.search(query, sort="hot", time_filter="week", limit=25):
        # post.title, post.selftext, post.score, post.created_utc
        post.comments.replace_more(limit=0)  # flatten MoreComments
        for comment in list(post.comments)[:5]:
            # comment.body, comment.score
```

### Time Filtering
PRAW's `time_filter` only supports: `"hour"`, `"day"`, `"week"`, `"month"`, `"year"`, `"all"`.
Use `"week"` then filter by `post.created_utc` manually for 48h:

```python
cutoff = time.time() - (48 * 3600)
if post.created_utc < cutoff:
    continue
```

### Rate Limits
Reddit allows ~60 requests/minute for OAuth apps. PRAW handles this automatically.
No explicit backoff needed for read-only scraping at this scale.

---

## feedparser

### Install
```bash
pip install feedparser
```

### Usage
```python
import feedparser

feed = feedparser.parse("https://feeds.reuters.com/reuters/topNews")
for entry in feed.entries:
    title = entry.get("title", "")
    summary = entry.get("summary", "")
    published = entry.get("published_parsed")  # time.struct_time or None
    link = entry.get("link", "")
```

### RSS Feeds Used
| Source | URL |
|---|---|
| Reuters Top News | `https://feeds.reuters.com/reuters/topNews` |
| AP Top News | `https://feeds.apnews.com/rss/apf-topnews` |
| Google News | `https://news.google.com/rss` |
| BBC News | `https://feeds.bbci.co.uk/news/rss.xml` |

### Time Filtering
feedparser returns `published_parsed` as `time.struct_time`. Convert to timestamp:

```python
import calendar, time

if entry.published_parsed:
    pub_ts = calendar.timegm(entry.published_parsed)
    cutoff = time.time() - (48 * 3600)
    if pub_ts < cutoff:
        continue
```

---

## Keyword Extraction for Prediction Market Titles

Market titles contain a lot of noise (dates, percentages, proper names with numbers).
Strategy: strip stop words + numbers + dates, keep nouns and named entities.

```python
import re

STOP_WORDS = {
    "will", "the", "a", "an", "in", "of", "to", "by", "for", "at", "on",
    "be", "is", "are", "was", "were", "has", "have", "had", "do", "does",
    "did", "can", "could", "would", "should", "may", "might", "must",
    "this", "that", "these", "those", "it", "its", "or", "and", "but",
    "with", "from", "than", "more", "most", "any", "all", "not", "no",
    "end", "close", "above", "below", "between", "least", "most", "than"
}

def extract_keywords(title: str, max_keywords: int = 5) -> list[str]:
    # Remove dates like "March 2025", "Q1 2025", "2025"
    title = re.sub(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{4}\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\bQ[1-4]\s*\d{4}\b', '', title)
    title = re.sub(r'\b\d{4}\b', '', title)
    # Remove percentages and numbers
    title = re.sub(r'\b\d+\.?\d*%?\b', '', title)
    # Remove punctuation
    title = re.sub(r'[^\w\s]', ' ', title)
    # Tokenize and filter
    words = title.lower().split()
    keywords = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    return keywords[:max_keywords]
```

**Examples:**
- `"Will the Fed cut rates in March 2025?"` → `["fed", "cut", "rates"]`
- `"Will Bitcoin exceed $100,000 by Q1 2025?"` → `["bitcoin", "exceed"]`
- `"Will Scottie Scheffler win the THE PLAYERS Championship?"` → `["scottie", "scheffler", "win", "players", "championship"]`

---

## Missing Credentials: Graceful Skip Pattern

```python
import os, logging

logger = logging.getLogger(__name__)

def check_credentials(required_vars: list[str]) -> bool:
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        logger.warning(f"Missing env vars: {missing}. Skipping this source.")
        return False
    return True
```

Usage:
```python
if not check_credentials(["TWITTER_BEARER_TOKEN"]):
    print("[WARNING] Twitter skipped — no TWITTER_BEARER_TOKEN")
    return {}
```

Always return an empty dict/list (not None) when skipping, so downstream
steps can handle missing sources without crashing.
