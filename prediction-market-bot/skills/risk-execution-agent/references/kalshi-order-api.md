# Kalshi Order API Reference

## Base URL
```
https://trading-api.kalshi.com/trade-api/v2
```

---

## RSA Authentication

Kalshi API v2 uses RSA-256 request signing (not HMAC).

### Setup
1. Generate an RSA key pair (or use one from Kalshi account settings)
2. Upload the public key to Kalshi API settings page
3. Save the private key to a local `.pem` file
4. Set env vars:
   - `KALSHI_ACCESS_KEY` — the key ID shown in Kalshi settings
   - `KALSHI_PRIVATE_KEY_PATH` — path to your private key file

### Python Implementation

```python
import base64, time, hashlib
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

def load_private_key(key_path: str):
    with open(key_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())

def sign_request(access_key: str, private_key, method: str, path: str) -> dict:
    """Returns auth headers for a Kalshi API request."""
    timestamp_ms = str(int(time.time() * 1000))
    msg_string = timestamp_ms + method.upper() + path

    signature = private_key.sign(
        msg_string.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode("utf-8")

    return {
        "KALSHI-ACCESS-KEY": access_key,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "Content-Type": "application/json",
    }
```

### Usage
```python
path = "/trade-api/v2/portfolio/orders"
headers = sign_request(access_key, private_key, "POST", path)
resp = requests.post(BASE_URL + "/portfolio/orders", headers=headers, json=payload)
```

---

## Portfolio Endpoints

### GET /portfolio/balance
Returns available cash balance.

**Response fields:**
```json
{
  "balance": 100000,        // in cents
  "payout_count": 0,
  "portfolio_value": 120000 // in cents (balance + open position value)
}
```

### GET /portfolio/positions
Returns all open positions.

**Response:**
```json
{
  "market_positions": [
    {
      "ticker": "SERIES-DATE",
      "position": 10,         // positive = long YES, negative = long NO
      "market_exposure": 420, // cents at risk
      "realized_pnl": 0,
      "total_cost": 420
    }
  ]
}
```

---

## Order Endpoints

### POST /portfolio/orders

**Request body:**
```json
{
  "ticker": "SERIES-DATE-THRESHOLD",
  "action": "buy",
  "side": "yes",
  "type": "limit",
  "count": 24,              // number of contracts (integer)
  "yes_price": 42,          // price in cents (integer, 1-99)
  "client_order_id": "bot-TICKER-1703001600"
}
```

**Key fields:**
| Field | Type | Notes |
|---|---|---|
| `ticker` | string | Full market ticker |
| `action` | "buy" \| "sell" | Always "buy" for new positions |
| `side` | "yes" \| "no" | Which side to buy |
| `type` | "limit" \| "market" | Always use "limit" to control price |
| `count` | int | Number of contracts (1 contract = $1 notional) |
| `yes_price` | int | Price in cents (1–99). For NO orders, Kalshi derives no_price = 100 - yes_price |
| `client_order_id` | string | Your unique ID for dedup/tracking |

**Count calculation:**
```python
# For YES bets: contracts = how many $1 payoffs we want
contracts = int(bet_size_dollars / (yes_price_cents / 100))

# For NO bets: we pay (100 - yes_price) cents per contract
contracts = int(bet_size_dollars / ((100 - yes_price_cents) / 100))
```

**Response:**
```json
{
  "order": {
    "order_id": "abc123",
    "client_order_id": "bot-TICKER-1703001600",
    "ticker": "SERIES-DATE-THRESHOLD",
    "status": "resting",
    "yes_price": 42,
    "no_price": 58,
    "count": 24,
    "filled_count": 0,
    "action": "buy",
    "side": "yes",
    "type": "limit",
    "created_time": "2025-03-14T18:00:00Z"
  }
}
```

### GET /portfolio/orders/{order_id}
Check status of a specific order.

**Status values:** `resting`, `filled`, `partially_filled`, `cancelled`, `expired`

### DELETE /portfolio/orders/{order_id}
Cancel an open order.

---

## Common Error Codes

| Code | Meaning | Fix |
|---|---|---|
| 400 | Invalid order params | Check count > 0, price 1-99, valid ticker |
| 401 | Auth failed | Verify KALSHI_ACCESS_KEY and signature |
| 403 | Forbidden | Check key permissions in Kalshi settings |
| 404 | Market not found | Ticker may be closed/expired |
| 409 | Duplicate client_order_id | Add timestamp to client_order_id |
| 429 | Rate limited | Back off 1-2 seconds, retry |
| 503 | Server unavailable | Retry after 30 seconds |

---

## Handling Partial Fills

A resting limit order may fill partially if there isn't enough liquidity at your price:

```python
order = get_order(order_id)
if order["status"] == "partially_filled":
    filled = order["filled_count"]
    remaining = order["count"] - filled
    # Either wait for more fills, or cancel and accept partial
```

Always log `filled_count` separately from `count` to track actual exposure.

---

## Price Format: cents as integers

**IMPORTANT:** Kalshi prices are always **integers in cents** (1–99), NOT floats.

```python
# Correct
yes_price_cents = int(yes_price_float * 100)  # 0.42 → 42

# Wrong — will return 400
yes_price = 0.42  # DO NOT send floats
```

---

## count vs count_fp

- `count` (integer): number of contracts. Each contract pays $1 if it resolves in your favor.
- `count_fp` (float): used in some response fields, equal to `count * 100` in cents

Always use `count` (integer) when placing orders.

---

## Rate Limits

- Order placement: ~10 requests/second
- Read endpoints (GET): ~100 requests/second
- 429 responses include `Retry-After` header in seconds

---

## Dry-Run Pattern

```python
def place_order(payload: dict, dry_run: bool = False) -> dict:
    if dry_run:
        print(f"[DRY RUN] Would place: {payload}")
        return {"order_id": "dry-run", "status": "dry_run"}
    # ... real API call
```
