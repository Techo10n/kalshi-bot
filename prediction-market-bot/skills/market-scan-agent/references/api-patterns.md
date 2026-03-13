# Kalshi API Patterns — Reference

## Base URL

```
https://api.elections.kalshi.com/trade-api/v2
```

> **Important quirk:** Despite the `elections` subdomain, this endpoint serves **all** Kalshi
> markets — not just election markets. This is the canonical base URL for the v2 trade API.
> Do not use `api.kalshi.com` — it routes to an older API version.

---

## Authentication

- **Public GET endpoints** (e.g., `/markets`, `/orderbook`) — **no auth required**.
- **Authenticated endpoints** (order placement, account info) require:
  - `KALSHI-ACCESS-KEY` header (your API Key ID)
  - `KALSHI-ACCESS-SIGNATURE` header (RSA-PS256 signature)
  - `KALSHI-ACCESS-TIMESTAMP` header (milliseconds since epoch)
  - Private key in PEM format (RSA, generated via Kalshi dashboard)

---

## Key Endpoints

### GET /markets

Fetch open markets with pagination.

```
GET /markets?status=open&limit=1000&cursor=<cursor>
```

**Query params:**
| Param | Type | Description |
|-------|------|-------------|
| `status` | string | `open`, `closed`, `settled` |
| `limit` | int | Max markets per page (1–1000) |
| `cursor` | string | Opaque cursor from previous response |
| `series_ticker` | string | Filter by series (e.g., `INXD`) |
| `event_ticker` | string | Filter by event |

**Response shape:**
```json
{
  "markets": [ ... ],
  "cursor": "next_page_cursor_or_empty_string"
}
```

Pagination ends when `cursor` is `""` or `null`.

---

### GET /markets/{ticker}

Fetch a single market by ticker.

```
GET /markets/INXD-24DEC31-B4800
```

---

### GET /markets/{ticker}/orderbook

Fetch the current order book for a market.

```
GET /markets/INXD-24DEC31-B4800/orderbook
```

**Response shape:**
```json
{
  "orderbook": {
    "yes": [[price_cents, quantity], ...],
    "no":  [[price_cents, quantity], ...]
  }
}
```

Prices in the orderbook are in **cents** (integers), not dollars.

---

## Key Market Fields

All dollar-denominated fields are returned as **strings** representing decimal dollar amounts.
**Always cast with `float()` before doing any math.**

| Field | Type | Description |
|-------|------|-------------|
| `ticker` | string | Unique market ID, e.g. `INXD-24DEC31-B4800` |
| `title` | string | Human-readable market question |
| `status` | string | `open`, `closed`, `settled` |
| `yes_bid` | string | Best bid for YES contracts, e.g. `"0.5600"` |
| `yes_ask` | string | Best ask for YES contracts, e.g. `"0.5900"` |
| `no_bid` | string | Best bid for NO contracts |
| `no_ask` | string | Best ask for NO contracts |
| `last_price` | string | Last traded price (dollars) |
| `previous_yes_bid` | string | YES bid from previous trading session |
| `previous_yes_ask` | string | YES ask from previous trading session |
| `volume` | int | Total contracts traded (lifetime) |
| `volume_24h` | int | Contracts traded in last 24 hours |
| `open_interest` | int | Open contracts currently outstanding |
| `volume_24h_fp` | float | 24h dollar volume (float, not string) |
| `open_interest_fp` | float | Dollar open interest (float, not string) |
| `close_time` | string | ISO 8601 UTC close time, e.g. `"2024-12-31T21:00:00Z"` |
| `expected_expiration_time` | string | ISO 8601 expected settlement time |
| `result` | string | `yes`, `no`, or `""` if unsettled |
| `can_close_early` | bool | Whether market can settle before close_time |
| `category` | string | Market category (e.g., `Politics`, `Economics`) |
| `series_ticker` | string | Parent series identifier |
| `event_ticker` | string | Parent event identifier |
| `subtitle` | string | Additional context for the market question |
| `rules_primary` | string | Primary resolution rules |

> **Note on `_fp` fields:** `volume_24h_fp` and `open_interest_fp` are pre-computed dollar
> floats (volume × contract price). Use these for dollar-value filtering instead of multiplying
> raw contract counts yourself.

---

## Ticker Format

```
{SERIES}-{DATE}-{THRESHOLD}
```

Examples:
- `INXD-24DEC31-B4800` — S&P 500, Dec 31 2024, above 4800
- `BTCD-25JAN01-B50000` — Bitcoin, Jan 1 2025, above $50,000
- `PRES-24NOV05-REP` — Presidential election, Nov 5 2024, Republican wins

The `{DATE}` component uses `YYMMMDD` format (2-digit year, 3-letter month abbreviation, 2-digit day).

---

## Pagination Pattern

```python
import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

def fetch_all_markets():
    markets = []
    cursor = None
    while True:
        params = {"status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{BASE_URL}/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
        markets.extend(data.get("markets", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return markets
```

---

## Rate Limits

| Endpoint type | Limit |
|---------------|-------|
| Public GET | ~10 requests/second |
| Authenticated | ~5 requests/second |

Exceeding limits returns HTTP 429. Add `time.sleep(0.1)` between paginated requests
to stay safely under the public limit.

---

## WebSocket (V2)

Kalshi offers a WebSocket API for real-time order book and market updates:

```
wss://api.elections.kalshi.com/trade-api/ws/v2
```

Subscribe to channels like `orderbook_delta`, `market_lifecycle`, and `trade`.
Authentication uses the same RSA signature scheme as REST.
WebSocket is useful for the execution agent (Step 5) to monitor fills in real time,
but the scanner (Step 1) uses REST polling for simplicity and reliability.

---

## Common Gotchas

1. **String → float:** `float(market["yes_bid"])` not `market["yes_bid"] * 100`.
2. **Missing fields:** Some markets lack `previous_yes_bid` — always use `.get()` with a default.
3. **Close time parsing:** Use `datetime.fromisoformat(close_time.replace("Z", "+00:00"))`.
4. **Volume vs dollar volume:** `volume_24h` is contract count; `volume_24h_fp` is dollar value.
5. **Spread calculation:** `spread = float(yes_ask) - float(yes_bid)`, not from the orderbook.
