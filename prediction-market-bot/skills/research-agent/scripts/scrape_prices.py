"""
Step 0: Fetch real-time asset prices for price-threshold markets.

Detects markets whose resolution depends on an asset crossing a price level
(e.g. BTC, ETH, stock indices) and enriches them with a live price from a
free public API — no auth required.

Reads:  data/scan_results.json
Writes: data/raw_prices.json

Supported asset types (auto-detected from ticker prefix):
  KXBTCD  → Bitcoin  (Binance public API)
  KXETHD  → Ethereum (Binance public API)
  KXSOL   → Solana   (Binance public API)
"""

import json
import logging
import re
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
SCAN_RESULTS = DATA_DIR / "scan_results.json"
OUTPUT = DATA_DIR / "raw_prices.json"

# Map ticker prefix → asset identifiers across price sources
TICKER_PREFIX_TO_ASSET = {
    "KXBTCD": {"binance": "BTCUSDT", "coingecko": "bitcoin",   "kraken": "XBTUSD",  "coinbase": "BTC-USD"},
    "KXETHD": {"binance": "ETHUSDT", "coingecko": "ethereum",  "kraken": "ETHUSD",  "coinbase": "ETH-USD"},
    "KXSOLD": {"binance": "SOLUSDT", "coingecko": "solana",    "kraken": "SOLUSD",  "coinbase": "SOL-USD"},
    "KXBNBD": {"binance": "BNBUSDT", "coingecko": "binancecoin","kraken": None,     "coinbase": "BNB-USD"},
}

# Regex to extract threshold from ticker, e.g. T70749.99 → 70749.99
THRESHOLD_RE = re.compile(r"-T(\d+(?:\.\d+)?)$")


def detect_price_markets(markets: list) -> list:
    """Return markets that are price-threshold bets with a known asset mapping."""
    price_markets = []
    for m in markets:
        ticker = m["ticker"]
        for prefix, assets in TICKER_PREFIX_TO_ASSET.items():
            if ticker.startswith(prefix):
                match = THRESHOLD_RE.search(ticker)
                threshold = float(match.group(1)) if match else None
                price_markets.append({
                    "ticker": ticker,
                    "title": m.get("title", ""),
                    "assets": assets,
                    "threshold": threshold,
                    "yes_price": m.get("yes_bid", m.get("yes_price", 0.5)),
                })
                break
    return price_markets


def fetch_price(assets: dict) -> float | None:
    """Try each price source in order until one succeeds."""
    # 1. Binance
    sym = assets.get("binance")
    if sym:
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": sym}, timeout=8)
            if r.status_code == 200:
                return float(r.json()["price"])
        except Exception:
            pass

    # 2. Kraken
    pair = assets.get("kraken")
    if pair:
        try:
            r = requests.get(f"https://api.kraken.com/0/public/Ticker?pair={pair}", timeout=8)
            if r.status_code == 200:
                result = r.json().get("result", {})
                if result:
                    key = list(result.keys())[0]
                    return float(result[key]["c"][0])
        except Exception:
            pass

    # 3. Coinbase
    pair = assets.get("coinbase")
    if pair:
        try:
            r = requests.get(f"https://api.coinbase.com/v2/prices/{pair}/spot", timeout=8)
            if r.status_code == 200:
                return float(r.json()["data"]["amount"])
        except Exception:
            pass

    # 4. CoinGecko (slowest, use as last resort)
    cg_id = assets.get("coingecko")
    if cg_id:
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": cg_id, "vs_currencies": "usd"},
                timeout=10,
            )
            if r.status_code == 200:
                return float(r.json()[cg_id]["usd"])
        except Exception:
            pass

    return None


def scrape_prices(markets: list) -> dict:
    price_markets = detect_price_markets(markets)
    if not price_markets:
        logger.info("No price-threshold markets found — skipping")
        return {}

    # Fetch each unique asset once (keyed by binance symbol as cache key)
    asset_cache: dict[str, float | None] = {}
    for pm in price_markets:
        cache_key = pm["assets"].get("binance", pm["assets"].get("coingecko", ""))
        if cache_key not in asset_cache:
            price = fetch_price(pm["assets"])
            asset_cache[cache_key] = price
            if price:
                logger.info(f"  {cache_key}: ${price:,.2f}")
            else:
                logger.warning(f"  {cache_key}: all price sources failed")
            time.sleep(0.2)

    results = {}
    for pm in price_markets:
        ticker = pm["ticker"]
        cache_key = pm["assets"].get("binance", pm["assets"].get("coingecko", ""))
        live_price = asset_cache.get(cache_key)
        threshold = pm["threshold"]

        if live_price is None or threshold is None:
            results[ticker] = {"symbol": cache_key, "live_price": None, "threshold": threshold}
            continue

        distance = live_price - threshold
        pct_away = distance / threshold * 100

        results[ticker] = {
            "symbol": cache_key,
            "live_price": round(live_price, 2),
            "threshold": threshold,
            "distance": round(distance, 2),
            "pct_away": round(pct_away, 4),
            "currently_above": live_price > threshold,
            "summary": (
                f"${live_price:,.2f} — currently "
                f"{'ABOVE' if live_price > threshold else 'BELOW'} "
                f"threshold ${threshold:,.2f} by ${abs(distance):,.2f} "
                f"({abs(pct_away):.2f}%)"
            ),
        }
        logger.info(f"  {ticker}: {results[ticker]['summary']}")

    return results


def main():
    if not SCAN_RESULTS.exists():
        logger.error(f"scan_results.json not found")
        raise FileNotFoundError(str(SCAN_RESULTS))

    markets = json.loads(SCAN_RESULTS.read_text())
    logger.info(f"Checking {len(markets)} markets for price-threshold types...")

    results = scrape_prices(markets)

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved price data → {OUTPUT} ({len(results)} markets)")
    return results


if __name__ == "__main__":
    main()
