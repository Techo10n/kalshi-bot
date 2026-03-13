---
name: research-agent
description: >
  Researches Kalshi prediction markets using social media and news sentiment. Trigger this
  skill when the user says things like "research markets", "run research", "scrape sentiment",
  "analyze narrative", "what's twitter saying about", "check reddit sentiment", "news sentiment",
  "sentiment analysis", or "what does the internet think about". Reads from data/scan_results.json
  (top 20 markets from market-scan-agent) and produces data/research_results.json with
  narrative edge scores.
---

# Research Agent

Enriches the top 20 markets from `data/scan_results.json` with social sentiment and news
narrative data. Sources: Twitter API v2, Reddit (PRAW), and RSS news feeds. Outputs
`data/research_results.json` with bullish/bearish scores and narrative edge flags.

## When to Use

Invoke this skill whenever the user wants to:
- Check what Twitter/Reddit/news is saying about a Kalshi market
- Find markets where public narrative diverges from current price
- Get sentiment data before deciding to place a trade
- Run research after a market scan (market-scan-agent → research-agent pipeline)

## Prerequisites

Run `market-scan-agent` first to produce `data/scan_results.json`, or ensure it already exists.

## Pipeline Overview

```
Step 1: scrape_twitter.py     → data/raw_twitter.json      (requires TWITTER_BEARER_TOKEN)
Step 2: scrape_reddit.py      → data/raw_reddit.json       (requires REDDIT_CLIENT_ID, etc.)
Step 3: scrape_rss.py         → data/raw_rss.json          (no auth needed)
Step 4: sentiment_analysis.py → data/sentiment_scores.json
Step 5: compare_narrative.py  → data/research_results.json
```

Run the full pipeline with:

```bash
python skills/research-agent/scripts/run_research.py
```

Or run individual steps:

```bash
python skills/research-agent/scripts/scrape_twitter.py
python skills/research-agent/scripts/scrape_reddit.py
python skills/research-agent/scripts/scrape_rss.py
python skills/research-agent/scripts/sentiment_analysis.py
python skills/research-agent/scripts/compare_narrative.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TWITTER_BEARER_TOKEN` | Optional | Twitter API v2 Bearer Token for tweet search |
| `REDDIT_CLIENT_ID` | Optional | Reddit app client ID (from reddit.com/prefs/apps) |
| `REDDIT_CLIENT_SECRET` | Optional | Reddit app client secret |
| `REDDIT_USER_AGENT` | Optional | Reddit user agent string (e.g. `kalshi-bot/1.0`) |

If Twitter or Reddit credentials are missing, those sources are skipped gracefully. RSS scraping
always runs — no auth needed.

## Step-by-Step Breakdown

### Step 1 — scrape_twitter.py
- Extracts keywords from each market title (strips dates, numbers, punctuation; keeps nouns)
- Searches Twitter API v2 recent search endpoint for last 100 tweets per market (24h window)
- Handles 429 rate limits with exponential backoff (max 3 retries)
- Saves raw tweet objects to `data/raw_twitter.json`
- Skips gracefully if `TWITTER_BEARER_TOKEN` not set

### Step 2 — scrape_reddit.py
- Searches these subreddits: r/politics, r/economics, r/finance, r/PredictionMarkets, r/Kalshi
- Top 25 posts + top 5 comments each, sorted by hot, within last 48h
- Saves to `data/raw_reddit.json`
- Skips gracefully if Reddit env vars not set

### Step 3 — scrape_rss.py
- Scrapes: Reuters top news, AP top news, Google News, BBC News
- Matches articles to markets by keyword overlap
- Filters to last 48h only
- Saves to `data/raw_rss.json`

### Step 4 — sentiment_analysis.py
- Uses `cardiffnlp/twitter-roberta-base-sentiment-latest` via HuggingFace transformers
- Scores all text (tweets + reddit titles/comments + rss headlines) as POSITIVE/NEGATIVE/NEUTRAL
- Aggregates per market:
  - `bullish_score` = mean confidence of POSITIVE items
  - `bearish_score` = mean confidence of NEGATIVE items
  - `sentiment_volume` = total items scored
- Saves to `data/sentiment_scores.json`

### Step 5 — compare_narrative.py
- Flags narrative divergence vs. current `yes_price`:
  - `NARRATIVE_BULLISH_UNDERPRICED`: bullish_score > 0.6 AND yes_price < 0.45
  - `NARRATIVE_BEARISH_OVERPRICED`: bearish_score > 0.6 AND yes_price > 0.55
- Computes `narrative_edge = abs(implied_sentiment_probability - yes_price)`
- Saves final output to `data/research_results.json`

## Output Format

`data/research_results.json` is a list of objects:

```json
[
  {
    "ticker": "SERIES-DATE-THRESHOLD",
    "title": "Market title",
    "yes_price": 0.42,
    "bullish_score": 0.71,
    "bearish_score": 0.18,
    "sentiment_volume": 143,
    "narrative_flags": ["NARRATIVE_BULLISH_UNDERPRICED"],
    "narrative_edge": 0.29,
    "raw_sample": [
      "Tweet text snippet...",
      "Reddit post title...",
      "RSS headline..."
    ]
  }
]
```

## Notes

- Re-run market-scan-agent first if scan_results.json is stale.
- The sentiment model downloads ~500MB on first run (cached to `~/.cache/huggingface`).
- Twitter free tier: 500k tweets/month. At 100 tweets × 20 markets = 2000 per run.
- See `references/source-patterns.md` for API field docs and rate limit strategies.
