# Market Overview Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "Latest Market Signals" text-card section on the main dashboard with a live Market Overview panel showing sector performance (1D/1W/1M %), VIX with % changes and term structure, GEX with rolling average and improved bucketing, and market breadth (% above 200d MA + A/D ratio).

**Architecture:** A new `src/fetchers/market_overview.py` module exposes a single `fetch_market_overview()` async function that collects all four signals concurrently. A new `GET /api/market-overview` FastAPI endpoint wraps it with the same market-hours-aware Redis caching pattern used by all other screener endpoints. The dashboard replaces the signals section with a 2×2 CSS grid panel; the LLM synthesis box becomes its own standalone section.

**Tech Stack:** Python 3.12, yfinance, httpx, asyncio, SQLite (universe_daily_ohlcv), Redis (via existing cache.py), vanilla JS/CSS (no build step).

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/fetchers/market_overview.py` | All four signal fetchers + coordinator |
| Create | `tests/test_market_overview.py` | Unit tests (mocked yfinance, respx, temp SQLite) |
| Modify | `src/cache.py` | Add `KEY_MARKET_OVERVIEW` constant |
| Modify | `src/api/main.py` | Add `GET /api/market-overview` endpoint |
| Modify | `src/web/index.html` | Replace signals section; add overview + LLM sections |
| Modify | `src/web/index.css` | Add `.market-overview-grid` and panel CSS |
| Modify | `src/web/app.js` | Replace `renderSignals`; add `fetchMarketOverview` + 4 render fns |

---

## Task 1: Add Cache Key + Create Test File Skeleton

**Files:**
- Modify: `src/cache.py`
- Create: `tests/test_market_overview.py`
- Create: `src/fetchers/market_overview.py` (skeleton only)

- [ ] **Step 1: Add the cache key constant to `src/cache.py`**

In `src/cache.py`, after the existing `KEY_MARKET_POSTURE` line, add:

```python
KEY_MARKET_OVERVIEW   = "market:overview"
```

- [ ] **Step 2: Create the module skeleton so tests can import it**

Create `src/fetchers/market_overview.py`:

```python
"""Live market overview fetcher — sectors, VIX, GEX, and breadth."""

from __future__ import annotations

import asyncio
import csv
import logging
import sqlite3
from contextlib import closing
from io import StringIO

import httpx
import yfinance as yf

from ..config import settings

logger = logging.getLogger(__name__)

GEX_CSV_URL = "https://squeezemetrics.com/monitor/static/DIX.csv"

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

DEFENSIVE = {"XLU", "XLP", "XLV", "XLRE"}
CYCLICAL  = {"XLK", "XLY", "XLF", "XLI", "XLE", "XLB", "XLC"}


def _pct_change(series, lookback: int) -> float | None:
    """Percentage change from `lookback` bars ago to the last bar. Returns None if insufficient data."""
    if len(series) < lookback + 1:
        return None
    prev = float(series.iloc[-(lookback + 1)])
    curr = float(series.iloc[-1])
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def _gex_bucket(value_b: float) -> tuple[str, str]:
    """Return (label, bucket) for a GEX value in billions."""
    if value_b < 0:
        return "Negative — High volatility risk", "negative"
    if value_b < 3:
        return "Low Positive — Muted hedging", "low"
    if value_b < 7:
        return "Moderate Positive — Normal pinning", "moderate"
    if value_b < 12:
        return "High Positive — Strong pinning", "high"
    return "Extreme — Max pinning, expect low realized vol", "extreme"


def _gex_trend(current_b: float, avg_b: float) -> str:
    """Return Rising / Falling / Flat based on current vs 20d average."""
    if avg_b == 0:
        return "Flat"
    ratio = current_b / avg_b
    if ratio > 1.1:
        return "Rising"
    if ratio < 0.9:
        return "Falling"
    return "Flat"


async def _fetch_sectors() -> dict:
    raise NotImplementedError


async def _fetch_vix() -> dict | None:
    raise NotImplementedError


async def _fetch_gex() -> dict:
    raise NotImplementedError


def _fetch_breadth() -> dict | None:
    raise NotImplementedError


async def fetch_market_overview() -> dict:
    raise NotImplementedError
```

- [ ] **Step 3: Create the test file skeleton**

Create `tests/test_market_overview.py`:

```python
"""Unit tests for src.fetchers.market_overview."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

import httpx
import pandas as pd
import pytest
import respx

from src.fetchers.market_overview import (
    _gex_bucket,
    _gex_trend,
    _fetch_sectors,
    _fetch_vix,
    _fetch_gex,
    _fetch_breadth,
    fetch_market_overview,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_yf_df(tickers: list[str], n_days: int, base: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """Build a fake yf.download multi-ticker DataFrame.

    close[i] = base + i * step for i in 0..n_days-1.
    All other columns are flat at base.
    """
    dates = pd.bdate_range(end="2024-01-31", periods=n_days)
    data = {}
    for ticker in tickers:
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col == "Close":
                data[(ticker, col)] = [base + i * step for i in range(n_days)]
            elif col == "Volume":
                data[(ticker, col)] = [1_000_000] * n_days
            else:
                data[(ticker, col)] = [base] * n_days
    df = pd.DataFrame(data, index=dates)
    df.columns = pd.MultiIndex.from_tuples(list(data.keys()))
    return df


def _make_gex_csv(gex_values: list[float]) -> str:
    """Build a fake DIX.csv string."""
    lines = ["date,price,dix,gex"]
    for i, gex in enumerate(gex_values):
        lines.append(f"2024-01-{i+1:02d},400.0,0.45,{gex:.0f}")
    return "\n".join(lines)


_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    with patch("src.fetchers.market_overview.settings") as mock_settings:
        mock_settings.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup_db():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


def _setup_breadth_db(tickers_ascending: list[str], tickers_descending: list[str], n_days: int = 210):
    """Populate the temp DB with OHLCV data for breadth tests."""
    conn = sqlite3.connect(_tmp_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universe_daily_ohlcv (
            symbol TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL NOT NULL,
            high   REAL NOT NULL,
            low    REAL NOT NULL,
            close  REAL NOT NULL,
            volume INTEGER NOT NULL,
            PRIMARY KEY (symbol, date)
        )
    """)
    dates = pd.bdate_range(end="2024-01-31", periods=n_days)
    rows = []
    for sym in tickers_ascending:
        for i, d in enumerate(dates):
            close = 100.0 + i
            rows.append((sym, str(d.date()), close, close, close, close, 1_000_000))
    for sym in tickers_descending:
        for i, d in enumerate(dates):
            close = 400.0 - i
            rows.append((sym, str(d.date()), close, close, close, close, 1_000_000))
    conn.executemany(
        "INSERT OR REPLACE INTO universe_daily_ohlcv (symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Commit the skeleton**

```bash
git add src/fetchers/market_overview.py src/cache.py tests/test_market_overview.py
git commit -m "feat: add market_overview skeleton, cache key, and test scaffold"
```

---

## Task 2: GEX Pure Helper Tests + Implementation

**Files:**
- Modify: `tests/test_market_overview.py`
- Modify: `src/fetchers/market_overview.py`

- [ ] **Step 1: Write failing tests for pure GEX helpers**

Append to `tests/test_market_overview.py`:

```python
# ── GEX pure helpers ──────────────────────────────────────────────────────────

def test_gex_bucket_negative():
    label, bucket = _gex_bucket(-1.0)
    assert bucket == "negative"
    assert "volatility" in label.lower()


def test_gex_bucket_low():
    label, bucket = _gex_bucket(1.5)
    assert bucket == "low"


def test_gex_bucket_moderate():
    label, bucket = _gex_bucket(5.0)
    assert bucket == "moderate"


def test_gex_bucket_high():
    label, bucket = _gex_bucket(9.0)
    assert bucket == "high"


def test_gex_bucket_extreme():
    label, bucket = _gex_bucket(15.0)
    assert bucket == "extreme"


def test_gex_bucket_boundaries():
    _, b0 = _gex_bucket(0.0)
    assert b0 == "low"        # exactly 0 = low (not negative)
    _, b3 = _gex_bucket(3.0)
    assert b3 == "moderate"   # exactly 3 = moderate
    _, b7 = _gex_bucket(7.0)
    assert b7 == "high"       # exactly 7 = high
    _, b12 = _gex_bucket(12.0)
    assert b12 == "extreme"   # exactly 12 = extreme


def test_gex_trend_rising():
    assert _gex_trend(6.0, 5.0) == "Rising"   # 6/5 = 1.2 > 1.1


def test_gex_trend_falling():
    assert _gex_trend(4.0, 5.0) == "Falling"  # 4/5 = 0.8 < 0.9


def test_gex_trend_flat():
    assert _gex_trend(5.0, 5.0) == "Flat"


def test_gex_trend_zero_avg():
    assert _gex_trend(5.0, 0.0) == "Flat"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_market_overview.py::test_gex_bucket_negative tests/test_market_overview.py::test_gex_trend_rising -v
```

Expected: `ImportError` or test failures because the helpers raise `NotImplementedError` — wait, actually `_gex_bucket` and `_gex_trend` are already implemented in the skeleton. Run to confirm they **pass** already.

```
PASSED tests/test_market_overview.py::test_gex_bucket_negative
PASSED tests/test_market_overview.py::test_gex_trend_rising
```

- [ ] **Step 3: Write and run the failing `_fetch_gex` integration test**

Append to `tests/test_market_overview.py`:

```python
@respx.mock
async def test_fetch_gex_values_and_trend():
    # 24 rows at 5B, then 1 row at 7B — last 20 = [5B x19, 7B] → avg = 5.1B
    gex_vals = [5_000_000_000] * 24 + [7_000_000_000]
    respx.get(
        "https://squeezemetrics.com/monitor/static/DIX.csv"
    ).mock(return_value=httpx.Response(200, text=_make_gex_csv(gex_vals)))

    result = await _fetch_gex()

    assert result["value_b"] == 7.0
    assert result["rolling_20d_avg_b"] == pytest.approx(5.1, abs=0.01)
    assert result["trend"] == "Rising"
    assert result["bucket"] == "high"
    assert "Strong pinning" in result["label"]


@respx.mock
async def test_fetch_gex_bucket_negative_live():
    gex_vals = [-2_000_000_000] * 25
    respx.get(
        "https://squeezemetrics.com/monitor/static/DIX.csv"
    ).mock(return_value=httpx.Response(200, text=_make_gex_csv(gex_vals)))

    result = await _fetch_gex()
    assert result["bucket"] == "negative"
    assert result["trend"] == "Flat"
```

Run:
```bash
.venv/bin/pytest tests/test_market_overview.py::test_fetch_gex_values_and_trend -v
```

Expected: `NotImplementedError` from the `_fetch_gex` stub.

- [ ] **Step 4: Implement `_fetch_gex`**

In `src/fetchers/market_overview.py`, replace `_fetch_gex`:

```python
async def _fetch_gex() -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(GEX_CSV_URL)
        resp.raise_for_status()

    rows = list(csv.reader(StringIO(resp.text)))
    data_rows = rows[1:]  # skip header
    if not data_rows:
        raise ValueError("GEX CSV is empty")

    gex_values: list[float] = []
    for row in data_rows:
        try:
            gex_values.append(float(row[3]))
        except (ValueError, IndexError):
            continue

    if not gex_values:
        raise ValueError("No GEX values parsed from CSV")

    current_b = round(gex_values[-1] / 1e9, 2)
    last_20 = gex_values[-20:]
    rolling_b = round((sum(last_20) / len(last_20)) / 1e9, 2)

    label, bucket = _gex_bucket(current_b)
    trend = _gex_trend(current_b, rolling_b)

    return {
        "value_b": current_b,
        "rolling_20d_avg_b": rolling_b,
        "trend": trend,
        "label": label,
        "bucket": bucket,
    }
```

- [ ] **Step 5: Run GEX tests and confirm they pass**

```bash
.venv/bin/pytest tests/test_market_overview.py -k "gex" -v
```

Expected: All `test_gex_*` tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fetchers/market_overview.py tests/test_market_overview.py
git commit -m "feat: implement _fetch_gex with rolling avg, bucketing, and trend"
```

---

## Task 3: Sector ETF Computation

**Files:**
- Modify: `tests/test_market_overview.py`
- Modify: `src/fetchers/market_overview.py`

- [ ] **Step 1: Write failing tests for `_fetch_sectors`**

Append to `tests/test_market_overview.py`:

```python
# ── Sector ETFs ───────────────────────────────────────────────────────────────

async def test_fetch_sectors_pct_changes():
    # 30 days, close = 100 + i (i=0..29)
    # close[-1]=129, close[-2]=128, close[-6]=124, close[-22]=108
    # 1D = (129-128)/128*100 = 0.78
    # 1W = (129-124)/124*100 = 4.03
    # 1M = (129-108)/108*100 = 19.44
    from src.fetchers.market_overview import SECTOR_ETFS
    tickers = list(SECTOR_ETFS.keys())
    mock_df = _make_yf_df(tickers, n_days=30, base=100.0, step=1.0)

    with patch("src.fetchers.market_overview.yf.download", return_value=mock_df):
        result = await _fetch_sectors()

    assert "XLK" in result
    xlk = result["XLK"]
    assert xlk["name"] == "Technology"
    assert xlk["pct_1d"] == pytest.approx(0.78, abs=0.01)
    assert xlk["pct_1w"] == pytest.approx(4.03, abs=0.01)
    assert xlk["pct_1m"] == pytest.approx(19.44, abs=0.01)


async def test_fetch_sectors_null_when_insufficient_data():
    from src.fetchers.market_overview import SECTOR_ETFS
    tickers = list(SECTOR_ETFS.keys())
    # Only 4 days — 1W and 1M should be None
    mock_df = _make_yf_df(tickers, n_days=4, base=100.0, step=1.0)

    with patch("src.fetchers.market_overview.yf.download", return_value=mock_df):
        result = await _fetch_sectors()

    assert result["XLK"]["pct_1d"] is not None
    assert result["XLK"]["pct_1w"] is None
    assert result["XLK"]["pct_1m"] is None


async def test_fetch_sectors_rotation_label_present():
    from src.fetchers.market_overview import SECTOR_ETFS
    tickers = list(SECTOR_ETFS.keys())
    mock_df = _make_yf_df(tickers, n_days=30, base=100.0, step=1.0)

    with patch("src.fetchers.market_overview.yf.download", return_value=mock_df):
        result = await _fetch_sectors()

    # Result is the sectors dict; rotation comes from the coordinator — just check structure
    assert all(
        {"name", "pct_1d"}.issubset(v.keys()) for v in result.values()
    )
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_market_overview.py::test_fetch_sectors_pct_changes -v
```

Expected: `NotImplementedError`.

- [ ] **Step 3: Implement `_fetch_sectors`**

In `src/fetchers/market_overview.py`, replace `_fetch_sectors`:

```python
async def _fetch_sectors() -> dict:
    tickers = list(SECTOR_ETFS.keys())
    tickers_str = " ".join(tickers)

    data = await asyncio.to_thread(
        yf.download,
        tickers_str,
        period="30d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )

    result: dict[str, dict] = {}
    for ticker, sector_name in SECTOR_ETFS.items():
        try:
            if ticker not in data.columns.get_level_values(0):
                continue
            closes = data[ticker]["Close"].dropna()
            if len(closes) < 2:
                continue
            result[ticker] = {
                "name": sector_name,
                "pct_1d": _pct_change(closes, 1),
                "pct_1w": _pct_change(closes, 5),
                "pct_1m": _pct_change(closes, 21),
            }
        except Exception:
            logger.debug("Sectors: failed to process %s", ticker, exc_info=True)
            continue

    return result
```

- [ ] **Step 4: Run sector tests and confirm they pass**

```bash
.venv/bin/pytest tests/test_market_overview.py -k "sector" -v
```

Expected: All `test_fetch_sectors_*` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fetchers/market_overview.py tests/test_market_overview.py
git commit -m "feat: implement _fetch_sectors with 1D/1W/1M percentage changes"
```

---

## Task 4: VIX Computation

**Files:**
- Modify: `tests/test_market_overview.py`
- Modify: `src/fetchers/market_overview.py`

- [ ] **Step 1: Write failing tests for `_fetch_vix`**

Append to `tests/test_market_overview.py`:

```python
# ── VIX ──────────────────────────────────────────────────────────────────────

async def test_fetch_vix_spot_and_pct_changes():
    # 10 days, close = 100 + i
    # close[-1]=109, close[-2]=108, close[-6]=104
    # 1D = (109-108)/108*100 = 0.93
    # 1W = (109-104)/104*100 = 4.81
    vix_df = _make_yf_df(["^VIX", "^VIX3M"], n_days=10, base=100.0, step=1.0)
    # Override ^VIX3M to be flat at 110 so spread = 110 - 109 = 1.0 → Contango
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        vix_df[("^VIX3M", col)] = 110.0 if col != "Volume" else 1_000_000

    with patch("src.fetchers.market_overview.yf.download", return_value=vix_df):
        result = await _fetch_vix()

    assert result is not None
    assert result["spot"] == pytest.approx(109.0, abs=0.1)
    assert result["pct_1d"] == pytest.approx(0.93, abs=0.01)
    assert result["pct_1w"] == pytest.approx(4.81, abs=0.01)
    assert result["term_structure"] == "Contango"
    assert result["spread"] == pytest.approx(1.0, abs=0.01)


async def test_fetch_vix_backwardation():
    vix_df = _make_yf_df(["^VIX", "^VIX3M"], n_days=10, base=100.0, step=1.0)
    # VIX3M flat at 105 → spread = 105 - 109 = -4 → Backwardation
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        vix_df[("^VIX3M", col)] = 105.0 if col != "Volume" else 1_000_000

    with patch("src.fetchers.market_overview.yf.download", return_value=vix_df):
        result = await _fetch_vix()

    assert result["term_structure"] == "Backwardation"


async def test_fetch_vix_no_1w_when_insufficient():
    vix_df = _make_yf_df(["^VIX", "^VIX3M"], n_days=3, base=20.0, step=0.5)

    with patch("src.fetchers.market_overview.yf.download", return_value=vix_df):
        result = await _fetch_vix()

    assert result is not None
    assert result["pct_1d"] is not None
    assert result["pct_1w"] is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_market_overview.py::test_fetch_vix_spot_and_pct_changes -v
```

Expected: `NotImplementedError`.

- [ ] **Step 3: Implement `_fetch_vix`**

In `src/fetchers/market_overview.py`, replace `_fetch_vix`:

```python
async def _fetch_vix() -> dict | None:
    data = await asyncio.to_thread(
        yf.download,
        "^VIX ^VIX3M",
        period="10d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )

    if "^VIX" not in data.columns.get_level_values(0):
        logger.warning("VIX: ^VIX data not available")
        return None

    vix_closes = data["^VIX"]["Close"].dropna()
    if len(vix_closes) < 2:
        return None

    spot = round(float(vix_closes.iloc[-1]), 2)
    result: dict = {
        "spot": spot,
        "pct_1d": _pct_change(vix_closes, 1),
        "pct_1w": _pct_change(vix_closes, 5),
    }

    if "^VIX3M" in data.columns.get_level_values(0):
        vix3m_closes = data["^VIX3M"]["Close"].dropna()
        if not vix3m_closes.empty:
            vix3m = round(float(vix3m_closes.iloc[-1]), 2)
            spread = round(vix3m - spot, 2)
            if spread > 0.5:
                term_structure, stress_note = "Contango", "normal, calm"
            elif spread < -0.5:
                term_structure, stress_note = "Backwardation", "stress building"
            else:
                term_structure, stress_note = "Flat", "transition zone"
            result.update({
                "vix3m": vix3m,
                "term_structure": term_structure,
                "spread": spread,
                "stress_note": stress_note,
            })

    return result
```

- [ ] **Step 4: Run VIX tests and confirm they pass**

```bash
.venv/bin/pytest tests/test_market_overview.py -k "vix" -v
```

Expected: All `test_fetch_vix_*` PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fetchers/market_overview.py tests/test_market_overview.py
git commit -m "feat: implement _fetch_vix with 1D/1W pct change and term structure"
```

---

## Task 5: Breadth Computation

**Files:**
- Modify: `tests/test_market_overview.py`
- Modify: `src/fetchers/market_overview.py`

- [ ] **Step 1: Write failing tests for `_fetch_breadth`**

Append to `tests/test_market_overview.py`:

```python
# ── Market Breadth ────────────────────────────────────────────────────────────

def test_fetch_breadth_ad_and_200ma():
    # 8 ascending tickers (above 200d MA, advancing today)
    # 2 descending tickers (below 200d MA, declining today)
    ascending = [f"UP{i}" for i in range(8)]
    descending = [f"DN{i}" for i in range(2)]
    _setup_breadth_db(ascending, descending, n_days=210)

    result = _fetch_breadth()

    assert result is not None
    assert result["advancing"] == 8
    assert result["declining"] == 2
    assert result["ad_ratio"] == pytest.approx(4.0, abs=0.01)
    assert result["pct_above_200ma"] == pytest.approx(80.0, abs=0.1)


def test_fetch_breadth_returns_none_when_empty():
    # Use a fresh temp DB with no rows
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        with patch("src.fetchers.market_overview.settings") as m:
            m.db_path = tmp_path
            result = _fetch_breadth()
        assert result is None
    finally:
        os.unlink(tmp_path)
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_market_overview.py::test_fetch_breadth_ad_and_200ma -v
```

Expected: `NotImplementedError`.

- [ ] **Step 3: Implement `_fetch_breadth`**

In `src/fetchers/market_overview.py`, replace `_fetch_breadth`:

```python
def _fetch_breadth() -> dict | None:
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.row_factory = sqlite3.Row

        # Need at least 2 dates for A/D
        dates = conn.execute(
            "SELECT DISTINCT date FROM universe_daily_ohlcv ORDER BY date DESC LIMIT 2"
        ).fetchall()
        if len(dates) < 2:
            return None

        today_date = dates[0]["date"]
        prev_date  = dates[1]["date"]

        rows = conn.execute(
            "SELECT symbol, date, close FROM universe_daily_ohlcv WHERE date IN (?, ?)",
            (today_date, prev_date),
        ).fetchall()

        today_close: dict[str, float] = {}
        prev_close:  dict[str, float] = {}
        for r in rows:
            if r["date"] == today_date:
                today_close[r["symbol"]] = r["close"]
            else:
                prev_close[r["symbol"]] = r["close"]

        advancing = sum(
            1 for sym in today_close
            if sym in prev_close and today_close[sym] > prev_close[sym]
        )
        declining = sum(
            1 for sym in today_close
            if sym in prev_close and today_close[sym] < prev_close[sym]
        )
        ad_ratio = round(advancing / declining, 2) if declining > 0 else None

        # % above 200d MA — only tickers with ≥ 200 rows
        ticker_counts = conn.execute(
            "SELECT symbol FROM (SELECT symbol, COUNT(*) AS cnt FROM universe_daily_ohlcv GROUP BY symbol) WHERE cnt >= 200"
        ).fetchall()
        qualifying = [r["symbol"] for r in ticker_counts]

        if len(qualifying) < 50:
            logger.warning(
                "Breadth: only %d tickers have 200d history (need 50+) — skipping %% above 200d MA",
                len(qualifying),
            )
            return {
                "pct_above_200ma": None,
                "advancing": advancing,
                "declining": declining,
                "ad_ratio": ad_ratio,
            }

        above_count = 0
        total_count = 0
        for sym in qualifying:
            closes = conn.execute(
                "SELECT close FROM universe_daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT 200",
                (sym,),
            ).fetchall()
            if len(closes) < 200:
                continue
            latest = closes[0]["close"]   # most recent (DESC order)
            ma200  = sum(r["close"] for r in closes) / 200
            if latest > ma200:
                above_count += 1
            total_count += 1

        pct_above_200ma = round(above_count / total_count * 100, 1) if total_count > 0 else None

        return {
            "pct_above_200ma": pct_above_200ma,
            "advancing": advancing,
            "declining": declining,
            "ad_ratio": ad_ratio,
        }
```

- [ ] **Step 4: Run breadth tests and confirm they pass**

```bash
.venv/bin/pytest tests/test_market_overview.py -k "breadth" -v
```

Expected: All `test_fetch_breadth_*` PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fetchers/market_overview.py tests/test_market_overview.py
git commit -m "feat: implement _fetch_breadth using universe_daily_ohlcv store"
```

---

## Task 6: Coordinator + API Endpoint

**Files:**
- Modify: `src/fetchers/market_overview.py`
- Modify: `tests/test_market_overview.py`
- Modify: `src/api/main.py`

- [ ] **Step 1: Write a failing test for `fetch_market_overview`**

Append to `tests/test_market_overview.py`:

```python
# ── Coordinator ───────────────────────────────────────────────────────────────

async def test_fetch_market_overview_structure():
    from unittest.mock import AsyncMock

    mock_sectors = {
        "XLK": {"name": "Technology", "pct_1d": 1.2, "pct_1w": 0.5, "pct_1m": 3.1},
        "XLU": {"name": "Utilities",   "pct_1d": 0.1, "pct_1w": 0.2, "pct_1m": 0.8},
    }
    mock_vix = {
        "spot": 20.0, "pct_1d": -1.0, "pct_1w": 2.0,
        "term_structure": "Contango", "spread": 1.0, "stress_note": "normal, calm",
    }
    mock_gex = {
        "value_b": 7.0, "rolling_20d_avg_b": 5.1,
        "trend": "Rising", "label": "High Positive — Strong pinning", "bucket": "high",
    }
    mock_breadth = {"pct_above_200ma": 65.0, "advancing": 350, "declining": 150, "ad_ratio": 2.33}

    with (
        patch("src.fetchers.market_overview._fetch_sectors", new=AsyncMock(return_value=mock_sectors)),
        patch("src.fetchers.market_overview._fetch_vix",     new=AsyncMock(return_value=mock_vix)),
        patch("src.fetchers.market_overview._fetch_gex",     new=AsyncMock(return_value=mock_gex)),
        patch("src.fetchers.market_overview._fetch_breadth", return_value=mock_breadth),
    ):
        result = await fetch_market_overview()

    assert "sectors" in result
    assert "vix" in result
    assert "gex" in result
    assert "breadth" in result
    assert "rotation" in result
    assert result["sectors"]["XLK"]["pct_1d"] == 1.2
    for s in result["sectors"].values():
        assert {"pct_1d", "pct_1w", "pct_1m"}.issubset(s.keys())


async def test_fetch_market_overview_graceful_on_gex_error():
    from unittest.mock import AsyncMock

    mock_sectors = {"XLK": {"name": "Technology", "pct_1d": 1.2, "pct_1w": 0.5, "pct_1m": 3.1}}
    mock_vix = {"spot": 20.0, "pct_1d": -1.0, "pct_1w": 2.0}

    with (
        patch("src.fetchers.market_overview._fetch_sectors", new=AsyncMock(return_value=mock_sectors)),
        patch("src.fetchers.market_overview._fetch_vix",     new=AsyncMock(return_value=mock_vix)),
        patch("src.fetchers.market_overview._fetch_gex",     new=AsyncMock(side_effect=Exception("network error"))),
        patch("src.fetchers.market_overview._fetch_breadth", return_value=None),
    ):
        result = await fetch_market_overview()

    assert result["gex"] is None        # failed gracefully
    assert result["sectors"] != {}      # rest still populated
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_market_overview.py::test_fetch_market_overview_structure -v
```

Expected: `NotImplementedError`.

- [ ] **Step 3: Implement `fetch_market_overview`**

In `src/fetchers/market_overview.py`, replace `fetch_market_overview`:

```python
async def fetch_market_overview() -> dict:
    results = await asyncio.gather(
        _fetch_sectors(),
        _fetch_vix(),
        _fetch_gex(),
        asyncio.to_thread(_fetch_breadth),
        return_exceptions=True,
    )

    sectors, vix, gex, breadth = results

    if isinstance(sectors, Exception):
        logger.error("Sectors fetch failed: %s", sectors)
        sectors = {}
    if isinstance(vix, Exception):
        logger.error("VIX fetch failed: %s", vix)
        vix = None
    if isinstance(gex, Exception):
        logger.error("GEX fetch failed: %s", gex)
        gex = None
    if isinstance(breadth, Exception):
        logger.error("Breadth fetch failed: %s", breadth)
        breadth = None

    # Compute rotation label from sector performances
    rotation = _compute_rotation(sectors)

    return {
        "sectors": sectors,
        "rotation": rotation,
        "vix": vix,
        "gex": gex,
        "breadth": breadth,
    }


def _compute_rotation(sectors: dict) -> str:
    def _group_avg(group: set[str]) -> float | None:
        vals = [sectors[t]["pct_1d"] for t in group if t in sectors and sectors[t].get("pct_1d") is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def_avg = _group_avg(DEFENSIVE)
    cyc_avg = _group_avg(CYCLICAL)

    if def_avg is None or cyc_avg is None:
        return "Insufficient data"
    spread = def_avg - cyc_avg
    if spread > 0.5:
        return "Risk-off (defensive leading)"
    if spread < -0.5:
        return "Risk-on (cyclical leading)"
    return "Neutral rotation"
```

- [ ] **Step 4: Run coordinator tests**

```bash
.venv/bin/pytest tests/test_market_overview.py -k "fetch_market_overview" -v
```

Expected: PASS (breadth will return None from the temp DB state — that's acceptable, the test just checks it's present in the dict).

- [ ] **Step 5: Add the API endpoint to `src/api/main.py`**

At the top of `src/api/main.py`, add this import alongside the existing fetcher imports:

```python
from ..fetchers.market_overview import fetch_market_overview
```

In the cache imports block, add `KEY_MARKET_OVERVIEW` to the existing import from `..cache`:

```python
from ..cache import (
    cache_get, cache_set, cache_delete, screener_ttl, scanner_ttl,
    invalidate_screener_cache, invalidate_market_posture,
    market_status_label, market_is_open,
    KEY_SCREENER_CSP, KEY_SCREENER_LEAPS, KEY_SCREENER_STOCKS,
    KEY_SCREENER_CSP_SCAN, KEY_MARKET_POSTURE,
    KEY_MARKET_OVERVIEW,
)
```

After the `GET /api/market-data/refresh` endpoint (end of file), add:

```python
# ── Market Overview ───────────────────────────────────────────────────────────

@app.get("/api/market-overview")
async def get_market_overview():
    """Return live market overview: sector ETF performance, VIX, GEX, and breadth.

    Market-hours-aware TTL: 5 min during market hours, until next open otherwise.
    """
    envelope = await cache_get(KEY_MARKET_OVERVIEW)
    if envelope is not None:
        payload = envelope["data"]
        payload.update(_cache_meta(envelope))
        return payload

    try:
        result = await fetch_market_overview()
        ttl = screener_ttl()
        await cache_set(KEY_MARKET_OVERVIEW, result, ttl=ttl)
        result.update(_cache_meta(None))
        return result
    except Exception as e:
        logger.exception("Market overview fetch failed")
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 6: Run the full test suite**

```bash
.venv/bin/pytest tests/test_market_overview.py -v
```

Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fetchers/market_overview.py src/api/main.py src/cache.py tests/test_market_overview.py
git commit -m "feat: add fetch_market_overview coordinator and GET /api/market-overview endpoint"
```

---

## Task 7: Frontend — HTML Structure + CSS

**Files:**
- Modify: `src/web/index.html`
- Modify: `src/web/index.css`

- [ ] **Step 1: Replace the signals section in `index.html`**

In `src/web/index.html`, find and replace this block:

```html
            <!-- Underlying Signals -->
            <section class="glass card full-width">
                <h2>Latest Market Signals</h2>
                <div id="signals-list" class="signals-grid loading">Aggregating sources...</div>

                <div class="llm-box">
                    <h3>🤖 AI Synthesis</h3>
                    <p id="llm-summary">Waiting for LLM analysis...</p>
                </div>
            </section>
```

Replace with:

```html
            <!-- Market Overview -->
            <section id="market-overview-section" class="glass card full-width">
                <div class="card-header">
                    <h2>Market Overview</h2>
                    <div class="badge blue">Live</div>
                    <span id="cache-status-overview" class="cache-badge"></span>
                </div>
                <div class="market-overview-grid">
                    <!-- Sector Performance -->
                    <div class="overview-panel" id="sectors-panel">
                        <div class="overview-panel-title">Sector Performance</div>
                        <div id="sector-rotation-label" class="sector-rotation"></div>
                        <div class="sector-bar-legend">
                            <span class="legend-item">1D</span>
                            <span class="legend-item legend-1w">1W</span>
                            <span class="legend-item legend-1m">1M</span>
                        </div>
                        <div id="sectors-bars" class="loading">Loading sectors...</div>
                    </div>
                    <!-- VIX -->
                    <div class="overview-panel" id="vix-panel">
                        <div class="overview-panel-title">VIX</div>
                        <div id="vix-content" class="loading">Loading...</div>
                    </div>
                    <!-- GEX -->
                    <div class="overview-panel" id="gex-panel">
                        <div class="overview-panel-title">Gamma Exposure (GEX)</div>
                        <div id="gex-content" class="loading">Loading...</div>
                    </div>
                    <!-- Breadth -->
                    <div class="overview-panel" id="breadth-panel">
                        <div class="overview-panel-title">Market Breadth</div>
                        <div id="breadth-content" class="loading">Loading...</div>
                    </div>
                </div>
            </section>

            <!-- AI Synthesis -->
            <section id="llm-section" class="glass card full-width">
                <div class="llm-box">
                    <h3>🤖 AI Synthesis</h3>
                    <p id="llm-summary">Waiting for LLM analysis...</p>
                </div>
            </section>
```

- [ ] **Step 2: Add CSS to `src/web/index.css`**

Append to `src/web/index.css`:

```css
/* ── Market Overview Grid ────────────────────────────────────────────────── */

.market-overview-grid {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 1rem;
    margin-top: 1rem;
}

.overview-panel {
    background: rgba(15, 23, 42, 0.5);
    border: 1px solid var(--glass-border);
    border-radius: 12px;
    padding: 1rem 1.25rem;
}

.overview-panel-title {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 0.6rem;
}

/* Rotation label */
.sector-rotation {
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin-bottom: 0.35rem;
}

/* Legend row above bars */
.sector-bar-legend {
    display: flex;
    gap: 0.5rem;
    font-size: 0.68rem;
    color: var(--text-secondary);
    margin-bottom: 0.4rem;
    padding-left: 152px; /* align with bars, after sector name column */
}

.legend-item { opacity: 1; }
.legend-1w   { opacity: 0.6; }
.legend-1m   { opacity: 0.35; }

/* Sector bar rows */
.sector-bar-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.3rem;
    font-size: 0.76rem;
    line-height: 1;
}

.sector-name {
    width: 148px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-secondary);
    flex-shrink: 0;
    font-size: 0.74rem;
}

.sector-bars {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 3px;
}

.sector-bar-track {
    position: relative;
    height: 5px;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 3px;
    overflow: hidden;
}

.center-line {
    position: absolute;
    left: 50%;
    top: 0;
    height: 100%;
    width: 1px;
    background: rgba(255, 255, 255, 0.18);
    pointer-events: none;
}

.sector-bar-fill {
    position: absolute;
    top: 0;
    height: 100%;
    border-radius: 3px;
    transition: width 0.35s ease;
}

.sector-pct-labels {
    display: flex;
    gap: 0.35rem;
    min-width: 130px;
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
    font-size: 0.72rem;
}

.pct-val-1d { color: var(--text-primary); font-weight: 500; min-width: 44px; }
.pct-val-1w { color: var(--text-secondary); min-width: 44px; }
.pct-val-1m { color: rgba(148, 163, 184, 0.55); min-width: 44px; }

/* VIX panel */
.vix-spot {
    font-size: 2rem;
    font-weight: 700;
    line-height: 1.1;
    margin-bottom: 0.4rem;
}

.vix-changes {
    display: flex;
    gap: 1.25rem;
    font-size: 0.82rem;
    margin-bottom: 0.6rem;
}

.vix-term {
    display: inline-block;
    font-size: 0.76rem;
    padding: 0.2rem 0.5rem;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 6px;
    color: var(--text-secondary);
}

/* GEX panel */
.gex-value {
    font-size: 1.7rem;
    font-weight: 700;
    line-height: 1.1;
    margin-bottom: 0.2rem;
}

.gex-label {
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
}

.gex-avg-trend {
    font-size: 0.78rem;
    color: var(--text-secondary);
}

/* Breadth panel */
.breadth-row {
    margin-bottom: 1rem;
}

.breadth-row:last-child {
    margin-bottom: 0;
}

.breadth-metric-label {
    font-size: 0.74rem;
    color: var(--text-secondary);
    margin-bottom: 0.25rem;
}

.breadth-metric-value {
    font-size: 1.05rem;
    font-weight: 600;
    margin-bottom: 0.3rem;
}

.breadth-fill-track {
    height: 5px;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 3px;
    overflow: hidden;
}

.breadth-fill-bar {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
}

/* Mobile */
@media (max-width: 768px) {
    .market-overview-grid {
        grid-template-columns: 1fr;
    }

    .sector-bar-legend {
        padding-left: 0;
    }

    .sector-name {
        width: 120px;
    }

    .sector-pct-labels {
        min-width: 110px;
    }
}
```

- [ ] **Step 3: Verify HTML is valid (no unclosed tags)**

```bash
grep -c "<section" src/web/index.html && grep -c "</section>" src/web/index.html
```

Expected: Both counts are equal.

- [ ] **Step 4: Commit**

```bash
git add src/web/index.html src/web/index.css
git commit -m "feat: add market overview HTML structure and CSS panel layout"
```

---

## Task 8: Frontend — JavaScript

**Files:**
- Modify: `src/web/app.js`

- [ ] **Step 1: Update `initDashboard` to call `fetchMarketOverview`**

In `src/web/app.js`, replace the `initDashboard` function:

```javascript
async function initDashboard() {
    Promise.allSettled([
        fetchMarketPosture(),
        fetchMarketOverview(),
        fetchCspCandidates(),
        fetchLeapsCandidates(),
        fetchStockScreener()
    ]);
}
```

- [ ] **Step 2: Update `fetchMarketPosture` to render LLM summary directly (since `renderSignals` is being removed)**

In `src/web/app.js`, replace the `fetchMarketPosture` function:

```javascript
async function fetchMarketPosture() {
    try {
        const response = await fetch(`${API_BASE}/market-posture`);
        if (!response.ok) throw new Error("Failed to fetch posture");
        const data = await response.json();
        renderPosture(data);
        const llmBox = document.getElementById("llm-summary");
        llmBox.innerText = data.llm_summary || "No AI analysis available for today.";
    } catch (err) {
        console.error(err);
        document.getElementById("posture-widget").innerText = "Error Loading Data";
        document.getElementById("posture-widget").classList.remove("loading");
    }
}
```

- [ ] **Step 3: Remove `renderSignals` and add `fetchMarketOverview`**

In `src/web/app.js`, delete the entire `renderSignals` function (lines starting with `function renderSignals(data) {` through its closing `}`).

Then add these functions after `fetchMarketPosture`:

```javascript
async function fetchMarketOverview() {
    try {
        const res = await fetch(`${API_BASE}/market-overview`);
        if (!res.ok) throw new Error('Failed to fetch market overview');
        const data = await res.json();
        updateCacheStatus('overview', data);
        renderSectors(data);
        renderVix(data.vix);
        renderGex(data.gex);
        renderBreadth(data.breadth);
    } catch (err) {
        console.error(err);
        ['sectors-bars', 'vix-content', 'gex-content', 'breadth-content'].forEach(id => {
            const el = document.getElementById(id);
            if (el) { el.classList.remove('loading'); el.innerHTML = '<span style="color:var(--text-secondary)">Unavailable</span>'; }
        });
    }
}
```

- [ ] **Step 4: Add `renderSectors`**

```javascript
const SECTOR_BAR_MAX_PCT = 5.0;

function renderSectors(data) {
    const barsEl = document.getElementById('sectors-bars');
    barsEl.classList.remove('loading');

    const sectors  = data.sectors  || {};
    const rotation = data.rotation || '';
    document.getElementById('sector-rotation-label').textContent = rotation;

    if (Object.keys(sectors).length === 0) {
        barsEl.innerHTML = '<div style="color:var(--text-secondary);font-size:.8rem">No sector data</div>';
        return;
    }

    const sorted = Object.entries(sectors).sort(
        (a, b) => (b[1].pct_1d ?? -999) - (a[1].pct_1d ?? -999)
    );

    function makeBar(pct, opacity) {
        if (pct === null || pct === undefined) {
            return `<div class="sector-bar-track"><div class="center-line"></div></div>`;
        }
        const isPos   = pct >= 0;
        const fillPct = Math.min(Math.abs(pct) / SECTOR_BAR_MAX_PCT * 50, 50);
        const color   = isPos
            ? `rgba(16,185,129,${opacity})`
            : `rgba(239,68,68,${opacity})`;
        const side    = isPos ? 'left:50%' : 'right:50%';
        return `
            <div class="sector-bar-track">
                <div class="center-line"></div>
                <div class="sector-bar-fill" style="${side};width:${fillPct}%;background:${color}"></div>
            </div>`;
    }

    function fmtPct(pct, cls) {
        if (pct === null || pct === undefined) return `<span class="${cls}" style="color:var(--text-secondary)">—</span>`;
        const sign  = pct >= 0 ? '+' : '';
        const color = pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
        return `<span class="${cls}" style="color:${color}">${sign}${pct.toFixed(2)}%</span>`;
    }

    barsEl.innerHTML = sorted.map(([ticker, s]) => `
        <div class="sector-bar-row">
            <div class="sector-name" title="${s.name} (${ticker})">${s.name}</div>
            <div class="sector-bars">
                ${makeBar(s.pct_1d, 1.0)}
                ${makeBar(s.pct_1w, 0.55)}
                ${makeBar(s.pct_1m, 0.30)}
            </div>
            <div class="sector-pct-labels">
                ${fmtPct(s.pct_1d, 'pct-val-1d')}
                ${fmtPct(s.pct_1w, 'pct-val-1w')}
                ${fmtPct(s.pct_1m, 'pct-val-1m')}
            </div>
        </div>
    `).join('');
}
```

- [ ] **Step 5: Add `renderVix`**

```javascript
function renderVix(vix) {
    const el = document.getElementById('vix-content');
    el.classList.remove('loading');

    if (!vix) {
        el.innerHTML = '<span style="color:var(--text-secondary)">VIX data unavailable</span>';
        return;
    }

    function fmtPct(pct) {
        if (pct === null || pct === undefined) return '—';
        // Higher VIX = fear (red), lower VIX = calm (green)
        const color = pct > 0 ? 'var(--accent-red)' : 'var(--accent-green)';
        const sign  = pct > 0 ? '+' : '';
        return `<span style="color:${color}">${sign}${pct.toFixed(2)}%</span>`;
    }

    const termColor = vix.term_structure === 'Backwardation'
        ? 'var(--accent-red)'
        : vix.term_structure === 'Contango'
        ? 'var(--accent-green)'
        : 'var(--text-secondary)';

    const termHtml = vix.term_structure ? `
        <div class="vix-term" style="color:${termColor}">
            ${vix.term_structure} — ${vix.stress_note || ''}
            ${vix.spread !== undefined ? ` (spread ${vix.spread > 0 ? '+' : ''}${vix.spread.toFixed(2)})` : ''}
        </div>` : '';

    el.innerHTML = `
        <div class="vix-spot">${vix.spot.toFixed(2)}</div>
        <div class="vix-changes">
            <span>1D: ${fmtPct(vix.pct_1d)}</span>
            <span>1W: ${fmtPct(vix.pct_1w)}</span>
        </div>
        ${termHtml}
    `;
}
```

- [ ] **Step 6: Add `renderGex`**

```javascript
function renderGex(gex) {
    const el = document.getElementById('gex-content');
    el.classList.remove('loading');

    if (!gex) {
        el.innerHTML = '<span style="color:var(--text-secondary)">GEX data unavailable</span>';
        return;
    }

    const valColor = gex.value_b < 0
        ? 'var(--accent-red)'
        : gex.value_b < 3
        ? '#f59e0b'
        : 'var(--accent-green)';

    const trendArrow = gex.trend === 'Rising' ? '↑' : gex.trend === 'Falling' ? '↓' : '→';
    const trendColor = gex.trend === 'Rising'
        ? 'var(--accent-green)'
        : gex.trend === 'Falling'
        ? 'var(--accent-red)'
        : 'var(--text-secondary)';

    el.innerHTML = `
        <div class="gex-value" style="color:${valColor}">$${gex.value_b.toFixed(2)}B</div>
        <div class="gex-label">${gex.label}</div>
        <div class="gex-avg-trend">
            20d avg: $${gex.rolling_20d_avg_b.toFixed(2)}B &nbsp;
            <span style="color:${trendColor}">${trendArrow} ${gex.trend}</span>
        </div>
    `;
}
```

- [ ] **Step 7: Add `renderBreadth`**

```javascript
function renderBreadth(breadth) {
    const el = document.getElementById('breadth-content');
    el.classList.remove('loading');

    if (!breadth) {
        el.innerHTML = '<span style="color:var(--text-secondary)">Breadth data unavailable</span>';
        return;
    }

    // % above 200d MA
    const ma = breadth.pct_above_200ma;
    const maColor = ma === null ? 'var(--text-secondary)'
        : ma >= 60 ? 'var(--accent-green)'
        : ma >= 40 ? '#f59e0b'
        : 'var(--accent-red)';

    const maHtml = ma !== null
        ? `<div class="breadth-row">
               <div class="breadth-metric-label">Above 200d MA</div>
               <div class="breadth-metric-value" style="color:${maColor}">${ma.toFixed(1)}%</div>
               <div class="breadth-fill-track">
                   <div class="breadth-fill-bar" style="width:${ma}%;background:${maColor}"></div>
               </div>
           </div>`
        : `<div class="breadth-row">
               <div class="breadth-metric-label">Above 200d MA</div>
               <div style="color:var(--text-secondary);font-size:.8rem">Insufficient history</div>
           </div>`;

    // A/D ratio
    const ratio = breadth.ad_ratio;
    const adColor = ratio === null ? 'var(--text-secondary)'
        : ratio >= 1.2 ? 'var(--accent-green)'
        : ratio >= 0.8 ? '#f59e0b'
        : 'var(--accent-red)';

    const adHtml = `
        <div class="breadth-row">
            <div class="breadth-metric-label">Advance / Decline</div>
            <div class="breadth-metric-value" style="color:${adColor}">
                ${breadth.advancing} ↑ &nbsp; ${breadth.declining} ↓
                ${ratio !== null ? `<span style="font-size:.8rem;color:var(--text-secondary)"> ratio ${ratio.toFixed(2)}</span>` : ''}
            </div>
        </div>`;

    el.innerHTML = maHtml + adHtml;
}
```

- [ ] **Step 8: Run the full test suite to confirm no regressions**

```bash
.venv/bin/pytest --ignore=tests/test_stock_screener.py -v
```

Expected: All tests PASS.

- [ ] **Step 9: Commit**

```bash
git add src/web/app.js
git commit -m "feat: add market overview JS — sector bar chart, VIX, GEX, breadth render functions"
```

---

## Task 9: Smoke Test and Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite one final time**

```bash
.venv/bin/pytest --ignore=tests/test_stock_screener.py -v 2>&1 | tail -20
```

Expected: All tests PASS, zero failures.

- [ ] **Step 2: Verify the API endpoint exists and returns the right shape**

Start the API locally (or check Docker), then:

```bash
curl -s http://localhost:8000/api/market-overview | python3 -m json.tool | head -40
```

Expected: JSON with `sectors`, `vix`, `gex`, `breadth`, `rotation`, `cached`, `market_status` keys. Each sector has `pct_1d`, `pct_1w`, `pct_1m`.

- [ ] **Step 3: Open the dashboard in a browser and verify the overview panel renders**

Open `http://localhost` (or your dashboard URL). Confirm:
- Market Overview section replaces the old signals grid
- All 11 sectors appear as bar rows with 3 bars each
- VIX card shows spot price, 1D/1W changes, term structure
- GEX card shows value, label, trend vs 20d avg
- Breadth card shows % above 200d MA (or "Insufficient history") and A/D ratio
- LLM Synthesis box appears as a separate section below overview
- Mobile layout (≤768px): panels stack vertically

- [ ] **Step 4: Final commit if any fixes needed, then push the branch**

```bash
git log --oneline feature/market-overview-dashboard ^main
```

Confirm all commits are present, then open a PR.
