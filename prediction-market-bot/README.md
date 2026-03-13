# Prediction Market Trading Bot (Kalshi)

A multi-agent system for scanning, analyzing, and trading on [Kalshi](https://kalshi.com) — the US-regulated prediction market exchange.

---

## Architecture: 5-Agent Pipeline

```
Step 1 ✅  market-scan-agent      Scans open markets, detects anomalies, ranks opportunities
Step 2 TODO sentiment-agent       Pulls Twitter/Reddit sentiment for top market topics
Step 3 TODO analysis-agent        Synthesizes scan + sentiment into trade signals
Step 4 TODO risk-agent            Applies position sizing, exposure limits, and kill switches
Step 5 TODO execution-agent       Places and manages orders via authenticated Kalshi API
```

Each agent is a self-contained skill under `skills/` with its own scripts, references, and `SKILL.md` describing when and how to invoke it.

---

## Quick Start

### 1. Install dependencies

```bash
cd prediction-market-bot
python -m venv .venv
source .venv/bin/activate
pip install requests python-dotenv
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials (only needed for authenticated endpoints)
```

### 3. Run the market scanner (no auth required)

```bash
python skills/market-scan-agent/scripts/run_scan.py
```

Results are written to `data/scan_results.json` — the top 20 ranked markets with anomaly flags.

---

## Folder Structure

```
prediction-market-bot/
├── .env.example                        # Environment variable template
├── .gitignore
├── README.md
├── data/                               # Runtime outputs (git-ignored)
│   ├── raw_markets.json
│   ├── filtered_markets.json
│   ├── flagged_markets.json
│   └── scan_results.json
└── skills/
    ├── market-scan-agent/              ✅ COMPLETE
    │   ├── SKILL.md
    │   ├── references/
    │   │   └── api-patterns.md
    │   └── scripts/
    │       ├── fetch_markets.py
    │       ├── filter_markets.py
    │       ├── detect_anomalies.py
    │       ├── rank_markets.py
    │       └── run_scan.py
    ├── sentiment-agent/                TODO
    ├── analysis-agent/                 TODO
    ├── risk-agent/                     TODO
    └── execution-agent/                TODO
```

---

## Key Design Decisions

- **No auth for scanning** — `GET /markets` is public; credentials only needed for order placement.
- **Dollar fields are strings** — Kalshi returns prices like `"0.5600"`; always cast with `float()`.
- **Cursor pagination** — use the `cursor` field from each response to fetch the next page.
- **Rate limit** — ~10 requests/second on public endpoints; the scanner respects this automatically.
