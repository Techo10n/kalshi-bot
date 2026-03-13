"""
Step 2: For each LOSS, run a structured postmortem using Claude.

Reads:  newly settled records (passed in from detect_losses.py)
        data/historical_results.json (updates failure_mode field)
Returns: list of postmortem records

Requires: ANTHROPIC_API_KEY
"""

import json
import logging
import os
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parents[3] / "data"
HISTORICAL = DATA_DIR / "historical_results.json"

FAILURE_MODES = [
    "SENTIMENT_WRONG",
    "MODEL_OVERCONFIDENT",
    "TIMING_BAD",
    "LIQUIDITY_IMPACT",
    "BLACK_SWAN",
    "DATA_MISSING",
]

SYSTEM_PROMPT = """You are a prediction market analyst performing a postmortem on a losing trade.
Identify the failure mode from this exact list:
- SENTIMENT_WRONG: narrative/sentiment didn't predict actual outcome
- MODEL_OVERCONFIDENT: confidence was high but the edge was false/spurious
- TIMING_BAD: right direction, wrong timeframe (market resolved before the event we predicted)
- LIQUIDITY_IMPACT: spread or price moved against us at entry/exit
- BLACK_SWAN: genuinely unpredictable event outside any reasonable model
- DATA_MISSING: key information wasn't available in our data sources

Respond with ONLY a valid JSON object:
{"failure_mode": "<one of the modes above>", "explanation": "<1-2 sentences>", "suggested_fix": "<1 sentence>", "severity": <1|2|3>}

severity: 1=minor, 2=systematic concern, 3=critical/fundamental flaw"""

USER_TEMPLATE = """Lost trade postmortem:

Market: {title}
Our prediction: {signal} at {entry_price} (final_probability={final_probability}, confidence={confidence})
Actual outcome: {outcome_str}
Bullish sentiment: {bullish_score}, Bearish sentiment: {bearish_score}
Narrative snippets used: {snippets}
Hours to close at entry: {hours_to_close}

What caused this loss?"""


def parse_postmortem_response(text: str) -> dict:
    text = re.sub(r'```(?:json)?\s*', '', text).strip().rstrip('`').strip()
    try:
        data = json.loads(text)
        mode = str(data.get("failure_mode", "UNANALYZED")).upper()
        if mode not in FAILURE_MODES:
            mode = "UNANALYZED"
        return {
            "failure_mode": mode,
            "explanation": str(data.get("explanation", ""))[:500],
            "suggested_fix": str(data.get("suggested_fix", ""))[:300],
            "severity": int(data.get("severity", 1)),
        }
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Failed to parse postmortem response: {e}")
        return {
            "failure_mode": "UNANALYZED",
            "explanation": "Parse failed",
            "suggested_fix": "",
            "severity": 1,
        }


def call_claude_postmortem(client, record: dict, exec_entry: dict) -> dict:
    outcome_str = "YES won" if record.get("outcome") == 1 else "NO won"
    sent = record.get("sentiment_scores", {})
    snippets = exec_entry.get("raw_sample", ["(not available)"])
    if isinstance(snippets, list):
        snippets_str = " | ".join(snippets[:3]) or "(not available)"
    else:
        snippets_str = str(snippets)

    user_msg = USER_TEMPLATE.format(
        title=record.get("ticker", ""),
        signal=record.get("signal", ""),
        entry_price=record.get("entry_price", "?"),
        final_probability=record.get("final_probability", "?"),
        confidence=record.get("confidence", "?"),
        outcome_str=outcome_str,
        bullish_score=sent.get("bullish_score", "?"),
        bearish_score=sent.get("bearish_score", "?"),
        snippets=snippets_str,
        hours_to_close=exec_entry.get("hours_to_close", "?"),
    )

    try:
        import anthropic
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return parse_postmortem_response(response.content[0].text.strip())
    except Exception as e:
        logger.warning(f"Claude postmortem API error: {e}")
        return {
            "failure_mode": "UNANALYZED",
            "explanation": f"API error: {e}",
            "suggested_fix": "",
            "severity": 1,
        }


def analyze_failures(settled_records: list) -> list:
    """Analyze LOSS records. Updates historical_results.json with failure_mode."""
    losses = [r for r in settled_records if r.get("classification") == "LOSS"]
    if not losses:
        logger.info("No losses to analyze")
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    postmortem_results = []

    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — marking losses as UNANALYZED")
        for record in losses:
            record["failure_mode"] = "UNANALYZED"
            record["failure_explanation"] = "API key not set"
            postmortem_results.append({
                "ticker": record["ticker"],
                "failure_mode": "UNANALYZED",
                "explanation": "API key not set",
                "suggested_fix": "",
                "severity": 1,
                "pnl": record.get("pnl", 0),
            })
        _update_historical_failure_modes(losses)
        return postmortem_results

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        logger.error("anthropic not installed: pip install anthropic")
        raise

    # Load execution log for additional context
    exec_log = []
    if (DATA_DIR / "execution_log.json").exists():
        exec_log = json.loads((DATA_DIR / "execution_log.json").read_text())
    exec_index = {e.get("ticker"): e for e in exec_log}

    for record in losses:
        ticker = record["ticker"]
        logger.info(f"  Analyzing failure: {ticker} (pnl=${record.get('pnl', 0):.2f})")
        exec_entry = exec_index.get(ticker, {})
        analysis = call_claude_postmortem(client, record, exec_entry)

        record["failure_mode"] = analysis["failure_mode"]
        record["failure_explanation"] = analysis["explanation"]

        result = {
            "ticker": ticker,
            "pnl": record.get("pnl", 0),
            **analysis,
        }
        postmortem_results.append(result)
        logger.info(f"    → {analysis['failure_mode']} (severity={analysis['severity']})")

    _update_historical_failure_modes(losses)
    return postmortem_results


def _update_historical_failure_modes(records: list):
    """Write failure_mode back to historical_results.json."""
    if not HISTORICAL.exists():
        return
    historical = json.loads(HISTORICAL.read_text())
    update_index = {r["ticker"]: r for r in records}
    for h in historical:
        if h["ticker"] in update_index:
            updated = update_index[h["ticker"]]
            h["failure_mode"] = updated.get("failure_mode")
            h["failure_explanation"] = updated.get("failure_explanation")
    HISTORICAL.write_text(json.dumps(historical, indent=2))


def main(settled_records: list = None):
    if settled_records is None:
        # Standalone: load from historical, find unanalyzed losses
        if not HISTORICAL.exists():
            logger.info("No historical_results.json found")
            return []
        historical = json.loads(HISTORICAL.read_text())
        settled_records = [
            r for r in historical
            if r.get("classification") == "LOSS" and r.get("failure_mode") is None
        ]
        if not settled_records:
            logger.info("No unanalyzed losses found")
            return []

    return analyze_failures(settled_records)


if __name__ == "__main__":
    results = main()
    print(f"\nAnalyzed {len(results)} losses")
    for r in results:
        print(f"  {r['ticker']}: {r['failure_mode']} (sev={r['severity']}) pnl=${r['pnl']:.2f}")
