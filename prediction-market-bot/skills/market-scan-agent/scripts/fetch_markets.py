"""
fetch_markets.py — Step 1 of the market scan pipeline.

Paginates through GET /markets?status=open and saves all open markets
to data/raw_markets.json.
"""

import json
import time
import requests
from pathlib import Path

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
OUTPUT_FILE = Path(__file__).parents[3] / "data" / "raw_markets.json"


def fetch_all_markets() -> list[dict]:
    markets = []
    cursor = None
    page = 1

    while True:
        params: dict = {"status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(f"{BASE_URL}/markets", params=params, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [ERROR] Request failed on page {page}: {e}")
            raise

        data = resp.json()
        page_markets = data.get("markets", [])
        markets.extend(page_markets)
        print(f"  Page {page}: fetched {len(page_markets)} markets (total: {len(markets)})")

        cursor = data.get("cursor")
        if not cursor:
            break

        page += 1
        time.sleep(0.12)  # stay under ~10 req/s rate limit

    return markets


def main():
    print(f"Fetching open markets from Kalshi API...")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    markets = fetch_all_markets()

    with open(OUTPUT_FILE, "w") as f:
        json.dump(markets, f, indent=2)

    print(f"\n  Saved {len(markets)} markets to {OUTPUT_FILE}")
    return markets


if __name__ == "__main__":
    main()
