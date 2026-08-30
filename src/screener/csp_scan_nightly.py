"""Nightly CSP scan — writes wheel-candidates JSON and regime-status JSON.

Usage:
    docker compose run --rm pipeline python3 -m src.screener.csp_scan_nightly

Reads the latest weekly macro note (data/trade-memos/YYYY-WW.md) to extract
regime caps. Falls back to defaults if note is absent or unparseable.

Outputs (host paths):
    data/wheel-candidates/YYYY-MM-DD.json
    data/regime-status.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from ..config import settings
from ..screener.csp_scanner import ScannerParams, run_csp_scan
from ..screener.wheel_scorer import score_wheel_candidates
from ..synthesis.macro_context import build_macro_context_str, fetch_spy_vix_snapshot
from ..synthesis.macro_note import find_latest_note

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parents[2] / "data"
_TRADE_MEMOS_DIR = _DATA_DIR / "trade-memos"
_CANDIDATES_DIR = _DATA_DIR / "wheel-candidates"
_REGIME_STATUS_PATH = _DATA_DIR / "regime-status.json"


@dataclass
class RegimeCaps:
    regime: str = "bull"
    delta_cap: float = 0.30
    iv_cap: float = 55.0
    vix_threshold: float = 25.0


def parse_regime_caps(note_text: str) -> RegimeCaps:
    """Extract regime caps from a macro note's Exhibit 2E section."""
    caps = RegimeCaps()

    m = re.search(r"\*\*Regime:\*\*\s*(Bull|Sideways|Bear)", note_text, re.IGNORECASE)
    if m:
        caps.regime = m.group(1).lower()

    m = re.search(r"Max delta.*?:\s*(0\.\d+)", note_text, re.IGNORECASE)
    if m:
        caps.delta_cap = float(m.group(1))

    # "IV range target: 35–55%" — take the upper bound
    m = re.search(r"IV range.*?:\s*\d+[–\-](\d+)%", note_text, re.IGNORECASE)
    if m:
        caps.iv_cap = float(m.group(1))

    m = re.search(r"VIX.*?threshold.*?:\s*(\d+(?:\.\d+)?)", note_text, re.IGNORECASE)
    if m:
        caps.vix_threshold = float(m.group(1))

    return caps


def compute_drift(snapshot: dict, caps: RegimeCaps) -> bool:
    """Return True if SPY crossed below 200 SMA or VIX exceeded threshold."""
    spy_price = snapshot.get("spy_price", float("inf"))
    spy_sma200 = snapshot.get("spy_sma200", 0.0)
    vix = snapshot.get("vix", 0.0)
    return (spy_price < spy_sma200) or (vix > caps.vix_threshold)


def _load_regime_caps() -> RegimeCaps:
    note_path = find_latest_note(_TRADE_MEMOS_DIR)
    if note_path is None:
        logger.warning("No macro note found in %s — using default regime caps", _TRADE_MEMOS_DIR)
        return RegimeCaps()
    note_text = note_path.read_text(encoding="utf-8")
    caps = parse_regime_caps(note_text)
    logger.info("Loaded regime caps from %s: %s", note_path.name, caps)
    return caps


def _write_candidates_json(
    candidates: list[dict],
    caps: RegimeCaps,
    today: date,
) -> Path:
    _CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for c in candidates:
        out.append({
            "ticker": c.get("symbol", ""),
            "strike": c.get("strike"),
            "expiry": c.get("expiration", ""),
            "dte": c.get("dte"),
            "delta": c.get("delta"),
            "iv_pct": c.get("impliedVolatility"),
            "adr20_pct": c.get("adr20_pct"),
            "ann_roc_pct": c.get("annualized_roc"),
            "target_premium": c.get("lastPrice"),
            "earnings_date": c.get("next_earnings_date", ""),
            "wheel_score": c.get("wheel_score"),
            "wheel_thesis": c.get("wheel_thesis", ""),
            "sector": c.get("sector", ""),
        })
    payload = {
        "date": today.isoformat(),
        "regime": caps.regime,
        "delta_cap": caps.delta_cap,
        "iv_cap": caps.iv_cap,
        "vix_threshold": caps.vix_threshold,
        "candidates": out,
    }
    path = _CANDIDATES_DIR / f"{today.isoformat()}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %d candidates to %s", len(out), path)
    return path


def _write_regime_status(snapshot: dict, caps: RegimeCaps, today: date) -> Path:
    spy_price = snapshot.get("spy_price", 0.0)
    spy_sma200 = snapshot.get("spy_sma200", 0.0)
    vix = snapshot.get("vix", 0.0)
    payload = {
        "date": today.isoformat(),
        "spy_close": spy_price,
        "spy_sma200": spy_sma200,
        "spy_above_sma200": spy_price >= spy_sma200,
        "vix_close": vix,
        "vix_threshold": caps.vix_threshold,
        "regime_from_note": caps.regime,
        "delta_cap": caps.delta_cap,
        "iv_cap": caps.iv_cap,
        "drift": compute_drift(snapshot, caps),
    }
    _REGIME_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGIME_STATUS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote regime status: drift=%s", payload["drift"])
    return _REGIME_STATUS_PATH


async def main() -> None:
    today = date.today()
    caps = _load_regime_caps()

    scan_params = ScannerParams(
        adr20_pct_min=3.5,
        max_vol_pct=caps.iv_cap,
        min_days_to_earnings=30,
    )

    logger.info("Running nightly CSP scan with caps: %s", caps)
    scan_result, snapshot, macro_str = await asyncio.gather(
        asyncio.to_thread(run_csp_scan, scan_params),
        asyncio.to_thread(fetch_spy_vix_snapshot),
        build_macro_context_str(),
    )
    candidates_raw = scan_result.get("candidates", [])
    logger.info("Scan returned %d candidates", len(candidates_raw))

    scored = await score_wheel_candidates(candidates_raw, macro_context=macro_str)

    _write_candidates_json(scored, caps, today)
    _write_regime_status(snapshot, caps, today)
    logger.info("Nightly scan complete")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main())
