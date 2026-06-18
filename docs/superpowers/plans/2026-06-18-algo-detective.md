# Algo Detective Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/algo_detective/` — a standalone module that constructs a labeled feature matrix (prime vs. control tickers) from stored OHLCV data and iteratively reverse-engineers the "prime state" scanner criteria.

**Architecture:** A pipeline of focused modules — `ingest` parses the CSV, `features` computes indicators, `universe` builds the control group, `build` orchestrates everything into SQLite, then `analyze` ranks features by KS statistic and generates criteria candidates, and `validate` scores any criteria JSON against the full dataset. All reads come from the existing `market_intelligence.db`; all writes go to two new `detective_*` tables and `data/detective/`.

**Tech Stack:** Python 3.12, pandas, pandas_ta (already installed), scipy (new dependency for KS test), SQLite via stdlib sqlite3.

## Global Constraints

- All Python runs inside Docker: `docker compose run --rm pipeline python -m src.algo_detective.<module>`
- Tests run via Docker: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_*.py -v`
- No new tables in existing pipeline tables — only `detective_features` and `detective_macro`
- Never import from `src.api`, `src.screener`, `src.synthesis`, or `src.notify` — detective is read-only relative to production
- Use `from __future__ import annotations` in every file
- Follow existing test pattern: `tempfile.NamedTemporaryFile` + `patch("src.algo_detective.store.settings")` for DB isolation
- `data/detective/` directory already exists with `prime_tickers.csv` committed

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `src/algo_detective/__init__.py` | Empty module marker |
| Create | `src/algo_detective/store.py` | DDL + read/write for `detective_features` and `detective_macro` |
| Create | `src/algo_detective/ingest.py` | Parse `prime_tickers.csv` into `PrimeTicker` dataclasses |
| Create | `src/algo_detective/features.py` | Compute all 50+ indicators for a (ticker, date) from a OHLCV DataFrame |
| Create | `src/algo_detective/universe.py` | Build the per-date control group from `universe_daily_ohlcv` + fundamentals |
| Create | `src/algo_detective/macro_context.py` | Pull VIX/posture/SPY state from `daily_signals` + `digests` + OHLCV |
| Create | `src/algo_detective/build.py` | CLI orchestrator: ingest → universe → features → store |
| Create | `src/algo_detective/analyze.py` | KS ranking, threshold grid-search, criteria candidate output |
| Create | `src/algo_detective/validate.py` | Score a criteria JSON against `detective_features` |
| Modify | `pyproject.toml` | Add `scipy>=1.11` dependency |
| Create | `tests/test_algo_detective_store.py` | Unit tests for store.py |
| Create | `tests/test_algo_detective_ingest.py` | Unit tests for ingest.py |
| Create | `tests/test_algo_detective_features.py` | Unit tests for features.py |
| Create | `tests/test_algo_detective_analyze.py` | Unit tests for analyze.py + validate.py |

---

## Task 1: Add scipy, create module scaffold and store.py

**Files:**
- Modify: `pyproject.toml`
- Create: `src/algo_detective/__init__.py`
- Create: `src/algo_detective/store.py`
- Test: `tests/test_algo_detective_store.py`

**Interfaces:**
- Produces:
  - `ensure_tables() -> None`
  - `get_computed_pairs() -> set[tuple[str, str]]` — set of (date, ticker) already in DB
  - `upsert_feature_rows_bulk(rows: list[dict]) -> int` — returns count inserted
  - `upsert_macro_row(row: dict) -> None`
  - `get_all_features() -> list[dict]`
  - `get_macro_for_date(date: str) -> dict | None`
  - `get_feature_counts() -> dict` — keys: total, prime, control, macro_dates
  - `_get_connection() -> sqlite3.Connection` (internal, used by build.py inspect)

- [ ] **Step 1: Add scipy to pyproject.toml**

Edit `pyproject.toml`, add `"scipy>=1.11",` to the dependencies list after `"pandas-ta>=0.3.14b0",`:

```toml
    "pandas-ta>=0.3.14b0",
    "scipy>=1.11",
```

- [ ] **Step 2: Rebuild the Docker image**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose build pipeline
```

Expected: build completes, `scipy` installed in image.

- [ ] **Step 3: Create `src/algo_detective/__init__.py`**

```python
```

(Empty file — just marks it as a package.)

- [ ] **Step 4: Write the failing test**

Create `tests/test_algo_detective_store.py`:

```python
from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    with patch("src.algo_detective.store.settings") as mock:
        mock.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


from src.algo_detective.store import (
    ensure_tables,
    get_computed_pairs,
    upsert_feature_rows_bulk,
    upsert_macro_row,
    get_all_features,
    get_macro_for_date,
    get_feature_counts,
)


def _make_feature_row(date="2025-10-07", ticker="GE", is_prime=1):
    return {
        "date": date,
        "ticker": ticker,
        "is_prime": is_prime,
        "close_price": 295.0,
        "volume": 1000000,
        "rsi": 64.0,
        "adx": 26.0,
        "ema20": 290.0, "ema50": 280.0, "ema150": 265.0, "ema200": 260.0,
        "sma20": 291.0, "sma50": 281.0, "sma150": 266.0, "sma200": 261.0,
        "price_vs_ema20_pct": 1.72, "price_vs_ema50_pct": 5.36,
        "price_vs_ema150_pct": 11.32, "price_vs_ema200_pct": 13.46,
        "price_vs_sma20_pct": 1.37, "price_vs_sma50_pct": 4.98,
        "price_vs_sma150_pct": 10.90, "price_vs_sma200_pct": 13.03,
        "price_above_ema20": 1, "price_above_ema50": 1,
        "price_above_ema150": 1, "price_above_ema200": 1,
        "price_above_sma20": 1, "price_above_sma50": 1,
        "price_above_sma150": 1, "price_above_sma200": 1,
        "ema20_above_ema50": 1, "ema50_above_ema150": 1,
        "ema50_above_ema200": 1, "ema150_above_ema200": 1,
        "sma20_above_sma50": 1, "sma50_above_sma150": 1,
        "sma50_above_sma200": 1, "sma150_above_sma200": 1,
        "bb_upper": 305.0, "bb_middle": 291.0, "bb_lower": 277.0,
        "bb_pct_b": 0.72, "bb_width_pct": 9.62,
        "price_above_bb_middle": 1, "price_above_bb_upper": 0, "price_below_bb_lower": 0,
        "rv20": 0.32, "atr_pct": 1.1, "volume_ratio": 1.3,
        "roc20": 4.5, "macd_histogram": 1.2, "pct_from_52wk_high": 2.1,
        "sector": "Industrials",
        "computed_at": "2026-06-18T00:00:00+00:00",
    }


def test_ensure_tables_creates_tables():
    ensure_tables()
    import sqlite3
    conn = sqlite3.connect(_tmp_db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "detective_features" in tables
    assert "detective_macro" in tables


def test_get_computed_pairs_empty():
    ensure_tables()
    assert get_computed_pairs() == set()


def test_upsert_and_retrieve_feature_row():
    ensure_tables()
    row = _make_feature_row()
    count = upsert_feature_rows_bulk([row])
    assert count == 1
    pairs = get_computed_pairs()
    assert ("2025-10-07", "GE") in pairs


def test_upsert_idempotent():
    ensure_tables()
    row = _make_feature_row(ticker="MSFT")
    upsert_feature_rows_bulk([row])
    upsert_feature_rows_bulk([row])  # second insert should not raise
    features = get_all_features()
    msft_rows = [f for f in features if f["ticker"] == "MSFT"]
    assert len(msft_rows) == 1


def test_get_feature_counts():
    ensure_tables()
    rows = [
        _make_feature_row(ticker="JPM", is_prime=1),
        _make_feature_row(ticker="BAC", is_prime=0),
        _make_feature_row(ticker="WFC", is_prime=0),
    ]
    upsert_feature_rows_bulk(rows)
    counts = get_feature_counts()
    assert counts["prime"] >= 1
    assert counts["control"] >= 2
    assert counts["total"] == counts["prime"] + counts["control"]


def test_upsert_and_retrieve_macro_row():
    ensure_tables()
    macro = {
        "date": "2025-10-07",
        "vix_score": 18.5,
        "vix_direction": "neutral",
        "market_posture": "Bullish",
        "composite_score": 0.45,
        "fear_greed_score": 62.0,
        "spy_above_ema50": 1,
        "spy_above_ema200": 1,
        "spy_rsi": 61.0,
        "top_sectors": '["Technology", "Financials"]',
    }
    upsert_macro_row(macro)
    result = get_macro_for_date("2025-10-07")
    assert result is not None
    assert result["market_posture"] == "Bullish"
    assert result["spy_above_ema50"] == 1


def test_get_macro_for_missing_date_returns_none():
    ensure_tables()
    assert get_macro_for_date("1990-01-01") is None
```

- [ ] **Step 5: Run the test to verify it fails**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py -v
```

Expected: `ImportError: No module named 'src.algo_detective.store'`

- [ ] **Step 6: Create `src/algo_detective/store.py`**

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS detective_features (
    date                    TEXT NOT NULL,
    ticker                  TEXT NOT NULL,
    is_prime                INTEGER NOT NULL,
    close_price             REAL,
    volume                  INTEGER,
    rsi                     REAL,
    adx                     REAL,
    ema20                   REAL,
    ema50                   REAL,
    ema150                  REAL,
    ema200                  REAL,
    sma20                   REAL,
    sma50                   REAL,
    sma150                  REAL,
    sma200                  REAL,
    price_vs_ema20_pct      REAL,
    price_vs_ema50_pct      REAL,
    price_vs_ema150_pct     REAL,
    price_vs_ema200_pct     REAL,
    price_vs_sma20_pct      REAL,
    price_vs_sma50_pct      REAL,
    price_vs_sma150_pct     REAL,
    price_vs_sma200_pct     REAL,
    price_above_ema20       INTEGER,
    price_above_ema50       INTEGER,
    price_above_ema150      INTEGER,
    price_above_ema200      INTEGER,
    price_above_sma20       INTEGER,
    price_above_sma50       INTEGER,
    price_above_sma150      INTEGER,
    price_above_sma200      INTEGER,
    ema20_above_ema50       INTEGER,
    ema50_above_ema150      INTEGER,
    ema50_above_ema200      INTEGER,
    ema150_above_ema200     INTEGER,
    sma20_above_sma50       INTEGER,
    sma50_above_sma150      INTEGER,
    sma50_above_sma200      INTEGER,
    sma150_above_sma200     INTEGER,
    bb_upper                REAL,
    bb_middle               REAL,
    bb_lower                REAL,
    bb_pct_b                REAL,
    bb_width_pct            REAL,
    price_above_bb_middle   INTEGER,
    price_above_bb_upper    INTEGER,
    price_below_bb_lower    INTEGER,
    rv20                    REAL,
    atr_pct                 REAL,
    volume_ratio            REAL,
    roc20                   REAL,
    macd_histogram          REAL,
    pct_from_52wk_high      REAL,
    sector                  TEXT,
    computed_at             TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS detective_macro (
    date                TEXT PRIMARY KEY,
    vix_score           REAL,
    vix_direction       TEXT,
    market_posture      TEXT,
    composite_score     REAL,
    fear_greed_score    REAL,
    spy_above_ema50     INTEGER,
    spy_above_ema200    INTEGER,
    spy_rsi             REAL,
    top_sectors         TEXT
);
"""


def _get_connection() -> sqlite3.Connection:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables() -> None:
    conn = _get_connection()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


def get_computed_pairs() -> set[tuple[str, str]]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT date, ticker FROM detective_features").fetchall()
        return {(r["date"], r["ticker"]) for r in rows}
    finally:
        conn.close()


def upsert_feature_rows_bulk(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = _get_connection()
    try:
        cols = list(rows[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("date", "ticker"))
        conn.executemany(
            f"INSERT INTO detective_features ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(date, ticker) DO UPDATE SET {updates}",
            [list(r.values()) for r in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def upsert_macro_row(row: dict) -> None:
    conn = _get_connection()
    try:
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "date")
        conn.execute(
            f"INSERT INTO detective_macro ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(date) DO UPDATE SET {updates}",
            list(row.values()),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_features() -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM detective_features").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_macro_for_date(date: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM detective_macro WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_feature_counts() -> dict:
    conn = _get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM detective_features"
        ).fetchone()["cnt"]
        prime = conn.execute(
            "SELECT COUNT(*) as cnt FROM detective_features WHERE is_prime = 1"
        ).fetchone()["cnt"]
        macro = conn.execute(
            "SELECT COUNT(*) as cnt FROM detective_macro"
        ).fetchone()["cnt"]
        return {"total": total, "prime": prime, "control": total - prime, "macro_dates": macro}
    finally:
        conn.close()
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/algo_detective/__init__.py src/algo_detective/store.py tests/test_algo_detective_store.py
git commit -m "feat(algo-detective): store module with detective_features and detective_macro tables"
```

---

## Task 2: ingest.py — CSV parsing

**Files:**
- Create: `src/algo_detective/ingest.py`
- Test: `tests/test_algo_detective_ingest.py`

**Interfaces:**
- Consumes: `data/detective/prime_tickers.csv`
- Produces:
  - `PrimeTicker` dataclass with fields: `date, ticker, expiration, strike, delta, premium, iv, return_pct, annual_yield_pct, pop_pct, spread_pct, cushion_pct, rsi, adx, collateral, mlabs_score`
  - `load_prime_tickers(csv_path: str | Path) -> list[PrimeTicker]`
  - `get_unique_dates(records: list[PrimeTicker]) -> list[str]` — sorted ascending
  - `get_prime_tickers_for_date(records: list[PrimeTicker], date: str) -> list[str]` — deduplicated
  - `get_prime_pairs(records: list[PrimeTicker]) -> set[tuple[str, str]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_ingest.py`:

```python
from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

import pytest

from src.algo_detective.ingest import (
    load_prime_tickers,
    get_unique_dates,
    get_prime_tickers_for_date,
    get_prime_pairs,
)

_SAMPLE_CSV = textwrap.dedent("""\
    date,ticker,expiration,strike,delta,premium,iv,return_pct,annual_yield_pct,pop_pct,spread_pct,cushion_pct,rsi,adx,collateral,mlabs_score
    2025-10-07,GE,2025-10-17,292.5,-0.25,2.26,36,0.77,31,79,9,3,64,26,29200,65.8
    2025-10-07,JPM,2025-10-10,305,-0.30,2.11,31,0.69,51,76,8,2,55,26,30500,61.4
    2025-10-08,GE,2025-10-17,292.5,-0.25,2.26,36,0.77,31,79,9,3,64,26,29200,65.8
    2025-10-07,GE,2025-10-17,292.5,-0.25,2.26,36,0.77,31,79,9,3,64,26,29200,65.8
""")


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "prime.csv"
    p.write_text(_SAMPLE_CSV)
    return p


def test_load_prime_tickers_count(csv_file):
    records = load_prime_tickers(csv_file)
    assert len(records) == 4


def test_load_prime_ticker_fields(csv_file):
    records = load_prime_tickers(csv_file)
    ge = records[0]
    assert ge.date == "2025-10-07"
    assert ge.ticker == "GE"
    assert ge.strike == 292.5
    assert ge.rsi == 64.0
    assert ge.adx == 26.0
    assert ge.mlabs_score == 65.8


def test_get_unique_dates(csv_file):
    records = load_prime_tickers(csv_file)
    dates = get_unique_dates(records)
    assert dates == ["2025-10-07", "2025-10-08"]


def test_get_prime_tickers_for_date_deduplicates(csv_file):
    records = load_prime_tickers(csv_file)
    tickers = get_prime_tickers_for_date(records, "2025-10-07")
    assert sorted(tickers) == ["GE", "JPM"]


def test_get_prime_pairs(csv_file):
    records = load_prime_tickers(csv_file)
    pairs = get_prime_pairs(records)
    assert ("2025-10-07", "GE") in pairs
    assert ("2025-10-08", "GE") in pairs
    assert ("2025-10-07", "JPM") in pairs


def test_load_skips_blank_rows(tmp_path):
    csv = tmp_path / "blank.csv"
    csv.write_text(
        "date,ticker,expiration,strike,delta,premium,iv,return_pct,"
        "annual_yield_pct,pop_pct,spread_pct,cushion_pct,rsi,adx,collateral,mlabs_score\n"
        ",,,,,,,,,,,,,,,\n"
        "2025-10-07,GE,2025-10-17,292.5,-0.25,2.26,36,0.77,31,79,9,3,64,26,29200,65.8\n"
    )
    records = load_prime_tickers(csv)
    assert len(records) == 1
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_ingest.py -v
```

Expected: `ImportError: No module named 'src.algo_detective.ingest'`

- [ ] **Step 3: Create `src/algo_detective/ingest.py`**

```python
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PrimeTicker:
    date: str
    ticker: str
    expiration: str
    strike: float
    delta: float
    premium: float
    iv: float
    return_pct: float
    annual_yield_pct: float
    pop_pct: float
    spread_pct: float
    cushion_pct: float
    rsi: float
    adx: float
    collateral: float
    mlabs_score: float


def load_prime_tickers(csv_path: str | Path) -> list[PrimeTicker]:
    records = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("date", "").strip() or not row.get("ticker", "").strip():
                continue
            try:
                records.append(PrimeTicker(
                    date=row["date"].strip(),
                    ticker=row["ticker"].strip(),
                    expiration=row["expiration"].strip(),
                    strike=float(row["strike"]),
                    delta=float(row["delta"]),
                    premium=float(row["premium"]),
                    iv=float(row["iv"]),
                    return_pct=float(row["return_pct"]),
                    annual_yield_pct=float(row["annual_yield_pct"]),
                    pop_pct=float(row["pop_pct"]),
                    spread_pct=float(row["spread_pct"]),
                    cushion_pct=float(row["cushion_pct"]),
                    rsi=float(row["rsi"]),
                    adx=float(row["adx"]),
                    collateral=float(row["collateral"]),
                    mlabs_score=float(row["mlabs_score"]),
                ))
            except (ValueError, KeyError):
                continue
    return records


def get_unique_dates(records: list[PrimeTicker]) -> list[str]:
    return sorted(set(r.date for r in records))


def get_prime_tickers_for_date(records: list[PrimeTicker], date: str) -> list[str]:
    seen: set[str] = set()
    result = []
    for r in records:
        if r.date == date and r.ticker not in seen:
            seen.add(r.ticker)
            result.append(r.ticker)
    return result


def get_prime_pairs(records: list[PrimeTicker]) -> set[tuple[str, str]]:
    return {(r.date, r.ticker) for r in records}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_ingest.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/ingest.py tests/test_algo_detective_ingest.py
git commit -m "feat(algo-detective): ingest module for prime_tickers CSV parsing"
```

---

## Task 3: features.py — indicator computation

**Files:**
- Create: `src/algo_detective/features.py`
- Test: `tests/test_algo_detective_features.py`

**Interfaces:**
- Consumes: `df: pd.DataFrame` with columns Open/High/Low/Close/Volume and DatetimeIndex (ascending), `ticker: str`, `as_of_date: str` (YYYY-MM-DD), `sector: str | None`
- Produces:
  - `compute_features(ticker: str, as_of_date: str, df: pd.DataFrame, sector: str | None = None) -> dict | None`
    Returns `None` if fewer than 210 bars before cutoff. Otherwise returns dict with all 50+ feature keys matching the `detective_features` schema (all keys except `date`, `ticker`, `is_prime`, `computed_at`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_features.py`:

```python
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.algo_detective.features import compute_features


def _make_ohlcv(n: int = 250, trend: str = "up") -> pd.DataFrame:
    """Build synthetic OHLCV with a clean uptrend."""
    np.random.seed(42)
    dates = pd.bdate_range("2024-01-02", periods=n)
    base = 100.0
    closes = []
    for i in range(n):
        noise = np.random.normal(0, 0.5)
        drift = 0.05 if trend == "up" else -0.05
        base = base + drift + noise
        closes.append(max(base, 1.0))
    closes = np.array(closes)
    highs = closes * 1.005
    lows = closes * 0.995
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = np.random.randint(500_000, 2_000_000, size=n)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )


def test_returns_none_for_insufficient_history():
    df = _make_ohlcv(n=100)
    result = compute_features("GE", "2024-06-01", df)
    assert result is None


def test_returns_dict_for_sufficient_history():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result is not None
    assert isinstance(result, dict)


def test_required_keys_present():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    required = [
        "rsi", "adx", "ema20", "ema50", "ema150", "ema200",
        "sma20", "sma50", "sma150", "sma200",
        "price_vs_ema50_pct", "price_above_ema50",
        "ema20_above_ema50", "ema50_above_ema200",
        "bb_pct_b", "bb_width_pct", "price_above_bb_middle",
        "rv20", "atr_pct", "volume_ratio", "roc20", "macd_histogram",
        "pct_from_52wk_high", "close_price", "volume", "sector",
    ]
    for key in required:
        assert key in result, f"Missing key: {key}"


def test_uptrend_booleans_are_set():
    df = _make_ohlcv(n=250, trend="up")
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result["price_above_ema50"] == 1
    assert result["price_above_ema200"] == 1
    assert result["ema20_above_ema50"] == 1


def test_rsi_in_valid_range():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result["rsi"] is not None
    assert 0 <= result["rsi"] <= 100


def test_rv20_is_annualized():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result["rv20"] is not None
    assert 0.0 < result["rv20"] < 5.0  # annualized, not raw daily


def test_bb_pct_b_range():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result["bb_pct_b"] is not None
    # Can exceed 0-1 if price breaks outside bands, but a steady trend stays inside
    assert -1.0 <= result["bb_pct_b"] <= 2.0


def test_sector_passes_through():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df, sector="Industrials")
    assert result["sector"] == "Industrials"


def test_no_lookahead():
    df = _make_ohlcv(n=250, trend="up")
    # Use a date 20 bars before the end — result should differ from using the full df
    cutoff_date = df.index[-20].strftime("%Y-%m-%d")
    result_early = compute_features("GE", cutoff_date, df)
    result_late = compute_features("GE", df.index[-1].strftime("%Y-%m-%d"), df)
    assert result_early is not None
    assert result_late is not None
    # RSI should differ because different data windows
    # (won't always differ by much but close_price must differ)
    assert result_early["close_price"] != result_late["close_price"]
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_features.py -v
```

Expected: `ImportError: No module named 'src.algo_detective.features'`

- [ ] **Step 3: Create `src/algo_detective/features.py`**

```python
from __future__ import annotations

import logging
import math

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)

_MIN_BARS = 210  # enough for EMA200 + warmup


def compute_features(
    ticker: str,
    as_of_date: str,
    df: pd.DataFrame,
    sector: str | None = None,
) -> dict | None:
    """Compute all features for ticker as of as_of_date.

    df must be sorted ascending by DatetimeIndex. Data after as_of_date is ignored.
    Returns None if fewer than _MIN_BARS of history are available.
    """
    cutoff = pd.Timestamp(as_of_date)
    df = df[df.index <= cutoff].copy()

    if len(df) < _MIN_BARS:
        logger.debug("Insufficient history for %s on %s: %d bars", ticker, as_of_date, len(df))
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    curr_close = float(close.iloc[-1])
    curr_volume = int(volume.iloc[-1])

    # ── EMAs ──────────────────────────────────────────────────────────────────
    ema20 = _last(ta.ema(close, length=20))
    ema50 = _last(ta.ema(close, length=50))
    ema150 = _last(ta.ema(close, length=150))
    ema200 = _last(ta.ema(close, length=200))

    # ── SMAs ──────────────────────────────────────────────────────────────────
    sma20 = _last(ta.sma(close, length=20))
    sma50 = _last(ta.sma(close, length=50))
    sma150 = _last(ta.sma(close, length=150))
    sma200 = _last(ta.sma(close, length=200))

    # ── RSI(14) ───────────────────────────────────────────────────────────────
    rsi = _last(ta.rsi(close, length=14))

    # ── ADX(14) — pandas_ta returns ADX_14, DMP_14, DMN_14 ───────────────────
    adx = None
    adx_df = ta.adx(high, low, close, length=14)
    if adx_df is not None and not adx_df.empty:
        adx_cols = [c for c in adx_df.columns if c.upper().startswith("ADX")]
        if adx_cols:
            adx = _last(adx_df[adx_cols[0]])

    # ── Bollinger Bands(20, 2σ) — pandas_ta returns BBL_, BBM_, BBU_, BBB_, BBP_ ──
    bb_upper = bb_middle = bb_lower = bb_pct_b = bb_width_pct = None
    price_above_bb_middle = price_above_bb_upper = price_below_bb_lower = None
    bb_df = ta.bbands(close, length=20, std=2.0)
    if bb_df is not None and not bb_df.empty:
        upper_cols = [c for c in bb_df.columns if c.startswith("BBU")]
        mid_cols = [c for c in bb_df.columns if c.startswith("BBM")]
        lower_cols = [c for c in bb_df.columns if c.startswith("BBL")]
        pct_b_cols = [c for c in bb_df.columns if c.startswith("BBP")]
        bw_cols = [c for c in bb_df.columns if c.startswith("BBB")]
        if upper_cols and mid_cols and lower_cols:
            bb_upper = _last(bb_df[upper_cols[0]])
            bb_middle = _last(bb_df[mid_cols[0]])
            bb_lower = _last(bb_df[lower_cols[0]])
            if pct_b_cols:
                bb_pct_b = _last(bb_df[pct_b_cols[0]])
            if bw_cols:
                bw_raw = _last(bb_df[bw_cols[0]])
                # pandas_ta BBB is already expressed as a % of middle band
                bb_width_pct = bw_raw
            if bb_upper is not None and bb_middle is not None and bb_lower is not None:
                price_above_bb_middle = int(curr_close > bb_middle)
                price_above_bb_upper = int(curr_close > bb_upper)
                price_below_bb_lower = int(curr_close < bb_lower)

    # ── MACD(12, 26, 9) — pandas_ta returns MACD_, MACDh_, MACDs_ ────────────
    macd_histogram = None
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        hist_cols = [c for c in macd_df.columns if c.startswith("MACDh")]
        if hist_cols:
            macd_histogram = _last(macd_df[hist_cols[0]])

    # ── ROC(20) ───────────────────────────────────────────────────────────────
    roc20 = _last(ta.roc(close, length=20))

    # ── ATR(14) as % of close ─────────────────────────────────────────────────
    atr_pct = None
    atr_val = _last(ta.atr(high, low, close, length=14))
    if atr_val is not None and curr_close > 0:
        atr_pct = atr_val / curr_close * 100

    # ── RV-20 (annualized realized volatility) ────────────────────────────────
    rv20 = None
    ret = close.pct_change().dropna()
    if len(ret) >= 20:
        rv20 = float(ret.iloc[-20:].std() * math.sqrt(252))

    # ── Volume ratio vs 20-day avg (excluding today) ──────────────────────────
    volume_ratio = None
    vol_window = volume.iloc[-21:-1]
    if len(vol_window) >= 10:
        avg = float(vol_window.mean())
        if avg > 0:
            volume_ratio = curr_volume / avg

    # ── Distance from 52-week high ────────────────────────────────────────────
    lookback = df["High"].iloc[-252:] if len(df) >= 252 else df["High"]
    high_52wk = float(lookback.max())
    pct_from_52wk_high = ((high_52wk - curr_close) / high_52wk * 100) if high_52wk > 0 else None

    def _vs_pct(ma: float | None) -> float | None:
        if ma is None or ma == 0:
            return None
        return round((curr_close - ma) / ma * 100, 4)

    def _above(ma: float | None) -> int | None:
        return int(curr_close > ma) if ma is not None else None

    def _gt(a: float | None, b: float | None) -> int | None:
        if a is None or b is None:
            return None
        return int(a > b)

    return {
        "close_price": round(curr_close, 4),
        "volume": curr_volume,
        "rsi": _r(rsi),
        "adx": _r(adx),
        "ema20": _r(ema20), "ema50": _r(ema50),
        "ema150": _r(ema150), "ema200": _r(ema200),
        "sma20": _r(sma20), "sma50": _r(sma50),
        "sma150": _r(sma150), "sma200": _r(sma200),
        "price_vs_ema20_pct": _vs_pct(ema20),
        "price_vs_ema50_pct": _vs_pct(ema50),
        "price_vs_ema150_pct": _vs_pct(ema150),
        "price_vs_ema200_pct": _vs_pct(ema200),
        "price_vs_sma20_pct": _vs_pct(sma20),
        "price_vs_sma50_pct": _vs_pct(sma50),
        "price_vs_sma150_pct": _vs_pct(sma150),
        "price_vs_sma200_pct": _vs_pct(sma200),
        "price_above_ema20": _above(ema20),
        "price_above_ema50": _above(ema50),
        "price_above_ema150": _above(ema150),
        "price_above_ema200": _above(ema200),
        "price_above_sma20": _above(sma20),
        "price_above_sma50": _above(sma50),
        "price_above_sma150": _above(sma150),
        "price_above_sma200": _above(sma200),
        "ema20_above_ema50": _gt(ema20, ema50),
        "ema50_above_ema150": _gt(ema50, ema150),
        "ema50_above_ema200": _gt(ema50, ema200),
        "ema150_above_ema200": _gt(ema150, ema200),
        "sma20_above_sma50": _gt(sma20, sma50),
        "sma50_above_sma150": _gt(sma50, sma150),
        "sma50_above_sma200": _gt(sma50, sma200),
        "sma150_above_sma200": _gt(sma150, sma200),
        "bb_upper": _r(bb_upper), "bb_middle": _r(bb_middle), "bb_lower": _r(bb_lower),
        "bb_pct_b": _r(bb_pct_b), "bb_width_pct": _r(bb_width_pct),
        "price_above_bb_middle": price_above_bb_middle,
        "price_above_bb_upper": price_above_bb_upper,
        "price_below_bb_lower": price_below_bb_lower,
        "rv20": _r(rv20),
        "atr_pct": _r(atr_pct),
        "volume_ratio": _r(volume_ratio),
        "roc20": _r(roc20),
        "macd_histogram": _r(macd_histogram),
        "pct_from_52wk_high": _r(pct_from_52wk_high),
        "sector": sector,
    }


def _last(series: pd.Series | None) -> float | None:
    if series is None or series.empty:
        return None
    val = series.iloc[-1]
    return float(val) if not pd.isna(val) else None


def _r(val: float | None, d: int = 4) -> float | None:
    return round(val, d) if val is not None else None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_features.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/features.py tests/test_algo_detective_features.py
git commit -m "feat(algo-detective): features module — 50+ indicators per ticker-date"
```

---

## Task 4: universe.py + macro_context.py

**Files:**
- Create: `src/algo_detective/universe.py`
- Create: `src/algo_detective/macro_context.py`

**Interfaces:**
- Produces:
  - `get_control_tickers(date: str, exclude: set[str], market_cap_min: float = 3.0, price_min: float = 5.0) -> list[str]`
  - `load_ohlcv_batch_for_date(tickers: list[str], as_of_date: str) -> dict[str, pd.DataFrame]` — one DB query for all tickers up to as_of_date, keeping last 504 rows per ticker
  - `compute_macro_for_date(date: str) -> dict | None` — returns macro dict or None if no pipeline data; always computes SPY fields from OHLCV

- [ ] **Step 1: Create `src/algo_detective/universe.py`**

No tests needed for `get_control_tickers` (it queries the real DB directly; tested in the E2E run). `load_ohlcv_batch_for_date` is integration-tested in Task 5.

```python
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ..config import settings
from .store import _get_connection

logger = logging.getLogger(__name__)

_LOOKBACK_ROWS = 504


def get_control_tickers(
    date: str,
    exclude: set[str],
    market_cap_min: float = 3.0,
    price_min: float = 5.0,
) -> list[str]:
    """Return tickers present in OHLCV on date, passing fundamentals filter, not in exclude."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT o.symbol
            FROM universe_daily_ohlcv o
            JOIN universe_fundamentals f ON o.symbol = f.symbol
            WHERE o.date = ?
              AND f.market_cap_b >= ?
              AND f.price >= ?
            ORDER BY o.symbol
            """,
            (date, market_cap_min, price_min),
        ).fetchall()
        return [r["symbol"] for r in rows if r["symbol"] not in exclude]
    finally:
        conn.close()


def load_ohlcv_batch_for_date(
    tickers: list[str],
    as_of_date: str,
) -> dict[str, pd.DataFrame]:
    """Batch-load OHLCV for multiple tickers up to as_of_date in a single query.

    Returns {ticker: DataFrame} with ascending DatetimeIndex, at most _LOOKBACK_ROWS rows.
    Tickers with no data are omitted from the result.
    """
    if not tickers:
        return {}

    conn = _get_connection()
    try:
        placeholders = ",".join("?" for _ in tickers)
        rows = conn.execute(
            f"""
            SELECT symbol, date, open, high, low, close, volume
            FROM universe_daily_ohlcv
            WHERE symbol IN ({placeholders})
              AND date <= ?
            ORDER BY symbol, date ASC
            """,
            (*tickers, as_of_date),
        ).fetchall()
    finally:
        conn.close()

    # Group into per-ticker lists, keep last _LOOKBACK_ROWS
    raw: dict[str, list[dict]] = {}
    for r in rows:
        raw.setdefault(r["symbol"], []).append({
            "Date": r["date"],
            "Open": r["open"],
            "High": r["high"],
            "Low": r["low"],
            "Close": r["close"],
            "Volume": r["volume"],
        })

    dfs: dict[str, pd.DataFrame] = {}
    for sym, records in raw.items():
        records = records[-_LOOKBACK_ROWS:]
        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        dfs[sym] = df

    return dfs
```

- [ ] **Step 2: Create `src/algo_detective/macro_context.py`**

```python
from __future__ import annotations

import json
import logging

import pandas as pd
import pandas_ta as ta

from .store import _get_connection
from .features import _last

logger = logging.getLogger(__name__)


def compute_macro_for_date(date: str) -> dict | None:
    """Build macro context row for a date.

    Pulls VIX + fear_greed from daily_signals, posture from digests,
    and SPY indicators from universe_daily_ohlcv. Returns None only
    if SPY OHLCV is missing; pipeline signal absence is handled gracefully.
    """
    conn = _get_connection()
    try:
        # ── Pipeline signals ──────────────────────────────────────────────────
        vix_row = conn.execute(
            "SELECT raw_value, direction, metadata FROM daily_signals "
            "WHERE date = ? AND source = 'vix'",
            (date,),
        ).fetchone()

        fg_row = conn.execute(
            "SELECT raw_value FROM daily_signals "
            "WHERE date = ? AND source = 'fear_greed'",
            (date,),
        ).fetchone()

        digest_row = conn.execute(
            "SELECT composite_score, posture FROM digests WHERE date = ?",
            (date,),
        ).fetchone()

        sector_rows = conn.execute(
            "SELECT metadata FROM daily_signals "
            "WHERE date = ? AND source = 'sector_etf'",
            (date,),
        ).fetchone()

        # ── SPY from OHLCV ────────────────────────────────────────────────────
        spy_rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM universe_daily_ohlcv
            WHERE symbol = 'SPY' AND date <= ?
            ORDER BY date ASC
            LIMIT 504
            """,
            (date,),
        ).fetchall()
    finally:
        conn.close()

    if not spy_rows:
        logger.warning("No SPY OHLCV data up to %s — skipping macro row", date)
        return None

    spy_df = pd.DataFrame(
        [{"Date": r["date"], "Close": r["close"]} for r in spy_rows]
    )
    spy_df["Date"] = pd.to_datetime(spy_df["Date"])
    spy_df.set_index("Date", inplace=True)

    spy_close = spy_df["Close"]
    spy_ema50 = _last(ta.ema(spy_close, length=50))
    spy_ema200 = _last(ta.ema(spy_close, length=200))
    spy_rsi = _last(ta.rsi(spy_close, length=14))
    curr_spy = float(spy_close.iloc[-1])

    # ── Top sectors ───────────────────────────────────────────────────────────
    top_sectors: list[str] = []
    if sector_rows:
        try:
            meta = json.loads(sector_rows["metadata"])
            top_sectors = meta.get("top_sectors", [])
        except (json.JSONDecodeError, KeyError):
            pass

    return {
        "date": date,
        "vix_score": float(vix_row["raw_value"]) if vix_row else None,
        "vix_direction": vix_row["direction"] if vix_row else None,
        "market_posture": digest_row["posture"] if digest_row else None,
        "composite_score": float(digest_row["composite_score"]) if digest_row else None,
        "fear_greed_score": float(fg_row["raw_value"]) if fg_row else None,
        "spy_above_ema50": int(curr_spy > spy_ema50) if spy_ema50 else None,
        "spy_above_ema200": int(curr_spy > spy_ema200) if spy_ema200 else None,
        "spy_rsi": round(spy_rsi, 2) if spy_rsi else None,
        "top_sectors": json.dumps(top_sectors),
    }
```

- [ ] **Step 3: Lint both files**

```bash
~/.local/bin/ruff check src/algo_detective/universe.py src/algo_detective/macro_context.py
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/algo_detective/universe.py src/algo_detective/macro_context.py
git commit -m "feat(algo-detective): universe and macro_context modules"
```

---

## Task 5: build.py — orchestrator CLI

**Files:**
- Create: `src/algo_detective/build.py`

**Interfaces:**
- Consumes: all prior modules
- Produces: populated `detective_features` and `detective_macro` tables; `--inspect DATE` prints a summary table to stdout

- [ ] **Step 1: Create `src/algo_detective/build.py`**

```python
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .features import compute_features
from .ingest import load_prime_tickers, get_unique_dates, get_prime_tickers_for_date, get_prime_pairs
from .macro_context import compute_macro_for_date
from .store import (
    _get_connection,
    ensure_tables,
    get_computed_pairs,
    get_feature_counts,
    upsert_feature_rows_bulk,
    upsert_macro_row,
)
from .universe import get_control_tickers, load_ohlcv_batch_for_date
from ..market_data.store import get_fundamentals_for_tickers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_CSV_PATH = Path(__file__).parent.parent.parent / "data" / "detective" / "prime_tickers.csv"


def run_build(csv_path: Path = _CSV_PATH) -> None:
    ensure_tables()

    records = load_prime_tickers(csv_path)
    logger.info("Loaded %d prime records (%d unique pairs)", len(records), len(get_prime_pairs(records)))

    prime_pairs = get_prime_pairs(records)
    computed_pairs = get_computed_pairs()
    dates = get_unique_dates(records)
    logger.info("Processing %d unique dates", len(dates))

    for date in dates:
        prime_tickers = get_prime_tickers_for_date(records, date)
        prime_set = set(prime_tickers)
        control_tickers = get_control_tickers(date, exclude=prime_set)

        all_ticker_flags: list[tuple[str, int]] = (
            [(t, 1) for t in prime_tickers] + [(t, 0) for t in control_tickers]
        )
        to_compute = [(t, f) for t, f in all_ticker_flags if (date, t) not in computed_pairs]

        if not to_compute:
            logger.debug("Date %s: all %d already computed", date, len(all_ticker_flags))
            continue

        logger.info(
            "Date %s: computing %d tickers (%d skipped)",
            date, len(to_compute), len(all_ticker_flags) - len(to_compute),
        )

        all_syms = [t for t, _ in to_compute]
        fund_rows = get_fundamentals_for_tickers(all_syms)
        sector_map = {r["symbol"]: r.get("sector") for r in fund_rows}

        macro = compute_macro_for_date(date)
        if macro:
            upsert_macro_row(macro)

        ohlcv_map = load_ohlcv_batch_for_date(all_syms, date)
        now = datetime.now(timezone.utc).isoformat()
        rows_to_insert: list[dict] = []

        for ticker, is_prime in to_compute:
            df = ohlcv_map.get(ticker)
            if df is None or df.empty:
                logger.warning("No OHLCV for %s on %s", ticker, date)
                continue

            feats = compute_features(ticker, date, df, sector=sector_map.get(ticker))
            if feats is None:
                logger.debug("Insufficient history for %s on %s", ticker, date)
                continue

            if is_prime:
                _cross_validate(ticker, date, feats, records)

            rows_to_insert.append({"date": date, "ticker": ticker, "is_prime": is_prime, **feats, "computed_at": now})

        count = upsert_feature_rows_bulk(rows_to_insert)
        logger.info("Date %s: inserted %d rows", date, count)

    counts = get_feature_counts()
    logger.info(
        "Build complete — total: %d  prime: %d  control: %d  macro_dates: %d",
        counts["total"], counts["prime"], counts["control"], counts["macro_dates"],
    )


def _cross_validate(ticker: str, date: str, feats: dict, records: list) -> None:
    csv_rec = next((r for r in records if r.date == date and r.ticker == ticker), None)
    if not csv_rec:
        return
    for field, computed in [("rsi", feats.get("rsi")), ("adx", feats.get("adx"))]:
        csv_val = getattr(csv_rec, field)
        if computed is not None and abs(computed - csv_val) > 5:
            logger.warning(
                "Cross-validation: %s on %s — %s computed=%.1f csv=%.1f (diff=%.1f)",
                ticker, date, field, computed, csv_val, abs(computed - csv_val),
            )


def run_inspect(date: str) -> None:
    ensure_tables()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker, is_prime, rsi, adx, price_above_ema50, "
            "ema20_above_ema50, rv20, bb_pct_b, sector "
            "FROM detective_features WHERE date = ? "
            "ORDER BY is_prime DESC, rsi DESC",
            (date,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"No data for {date} — run build first.")
        return

    prime_count = sum(1 for r in rows if r["is_prime"])
    print(f"\n=== {date}: {prime_count} prime / {len(rows) - prime_count} control ===\n")
    fmt = "{:<10} {:<6} {:<7} {:<7} {:<8} {:<10} {:<8} {:<8} {}"
    print(fmt.format("TICKER", "PRIME", "RSI", "ADX", "EMA50+", "EMA20>50", "RV20", "BB%B", "SECTOR"))
    print("-" * 80)
    for r in rows[:60]:
        print(fmt.format(
            r["ticker"],
            "YES" if r["is_prime"] else "-",
            f"{r['rsi']:.1f}" if r["rsi"] else "N/A",
            f"{r['adx']:.1f}" if r["adx"] else "N/A",
            str(r["price_above_ema50"]) if r["price_above_ema50"] is not None else "N/A",
            str(r["ema20_above_ema50"]) if r["ema20_above_ema50"] is not None else "N/A",
            f"{r['rv20']:.3f}" if r["rv20"] else "N/A",
            f"{r['bb_pct_b']:.2f}" if r["bb_pct_b"] else "N/A",
            r["sector"] or "",
        ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the algo detective feature matrix")
    parser.add_argument("--inspect", metavar="DATE", help="Print feature summary for YYYY-MM-DD")
    parser.add_argument("--csv", default=str(_CSV_PATH), help="Path to prime_tickers.csv")
    args = parser.parse_args()

    if args.inspect:
        run_inspect(args.inspect)
    else:
        run_build(Path(args.csv))
```

- [ ] **Step 2: Smoke-test build against real data**

```bash
docker compose run --rm pipeline python -m src.algo_detective.build
```

Expected output (approximately):
```
Loaded 230 prime records (230 unique pairs)
Processing 61 unique dates
Date 2025-09-09: computing ~750 tickers ...
...
Build complete — total: ~42000  prime: ~230  control: ~41770  macro_dates: ~61
```

Cross-validation warnings (if any) will appear inline. More than 5% warnings on RSI or ADX indicates a data freshness issue — check if `universe_daily_ohlcv` is current.

- [ ] **Step 3: Verify inspect output**

```bash
docker compose run --rm pipeline python -m src.algo_detective.build --inspect 2025-10-07
```

Expected: DELL, WPM, MS, HWM, GS, JPM, BAC, NTAP all appear with `PRIME = YES`. ~700 other rows follow. All prime rows should have RSI between 40–70 (cross-check against CSV values).

- [ ] **Step 4: Commit**

```bash
git add src/algo_detective/build.py
git commit -m "feat(algo-detective): build orchestrator — populates detective_features table"
```

---

## Task 6: analyze.py — KS ranking and criteria candidates

**Files:**
- Create: `src/algo_detective/analyze.py`
- Test: `tests/test_algo_detective_analyze.py` (partial — tests ranking logic only)

**Interfaces:**
- Consumes: `get_all_features()` from store.py
- Produces:
  - `rank_features(features: list[dict]) -> list[dict]` — returns list of `{feature, ks_stat, p_value, prime_mean, control_mean}` sorted by ks_stat descending
  - `find_thresholds(features: list[dict], top_n: int = 10) -> list[dict]` — returns candidate criteria sets with precision/recall
  - `run_analyze(output_dir: Path) -> None` — full pipeline, writes `analysis_YYYY-MM-DD.json` to output_dir

```python
# Analysis output JSON schema:
{
  "generated_at": "2026-06-18T...",
  "total_prime": 230,
  "total_control": 41770,
  "feature_rankings": [
    {"feature": "price_above_ema50", "ks_stat": 0.41, "p_value": 1.2e-18,
     "prime_mean": 0.89, "control_mean": 0.54},
    ...
  ],
  "criteria_candidates": [
    {
      "rank": 1,
      "criteria": {"price_above_ema50": true, "rsi_min": 42, "rsi_max": 68, ...},
      "precision": 0.87,
      "recall": 0.74,
      "true_positives": 170,
      "false_positives": 25,
      "false_negatives": 60
    },
    ...
  ]
}
```

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_analyze.py`:

```python
from __future__ import annotations

import pytest
from src.algo_detective.analyze import rank_features, find_thresholds


def _make_features(n_prime=50, n_control=500):
    """Synthetic feature rows where prime tickers cluster at RSI 50-65, control at 30-80."""
    import random
    random.seed(99)
    rows = []
    for i in range(n_prime):
        rows.append({
            "is_prime": 1,
            "rsi": random.uniform(50, 65),
            "adx": random.uniform(22, 38),
            "price_above_ema50": 1,
            "ema20_above_ema50": 1,
            "rv20": random.uniform(0.28, 0.45),
            "bb_pct_b": random.uniform(0.45, 0.75),
            "price_above_ema200": 1,
            "volume_ratio": random.uniform(0.9, 1.8),
            "pct_from_52wk_high": random.uniform(1, 10),
        })
    for i in range(n_control):
        rows.append({
            "is_prime": 0,
            "rsi": random.uniform(25, 75),
            "adx": random.uniform(10, 50),
            "price_above_ema50": random.randint(0, 1),
            "ema20_above_ema50": random.randint(0, 1),
            "rv20": random.uniform(0.15, 0.80),
            "bb_pct_b": random.uniform(0.1, 0.9),
            "price_above_ema200": random.randint(0, 1),
            "volume_ratio": random.uniform(0.3, 3.0),
            "pct_from_52wk_high": random.uniform(0, 40),
        })
    return rows


def test_rank_features_returns_sorted_by_ks():
    rows = _make_features()
    rankings = rank_features(rows)
    assert len(rankings) > 0
    ks_values = [r["ks_stat"] for r in rankings]
    assert ks_values == sorted(ks_values, reverse=True)


def test_rank_features_includes_required_fields():
    rows = _make_features()
    rankings = rank_features(rows)
    for r in rankings:
        assert "feature" in r
        assert "ks_stat" in r
        assert "prime_mean" in r
        assert "control_mean" in r


def test_discriminating_features_rank_high():
    rows = _make_features()
    rankings = rank_features(rows)
    top_features = [r["feature"] for r in rankings[:5]]
    # RSI, bb_pct_b, or adx should appear in top — they were given tighter distributions
    assert any(f in top_features for f in ["rsi", "bb_pct_b", "adx"])


def test_find_thresholds_returns_criteria_with_scores():
    rows = _make_features()
    candidates = find_thresholds(rows, top_n=5)
    assert len(candidates) > 0
    for c in candidates:
        assert "criteria" in c
        assert "precision" in c
        assert "recall" in c
        assert 0.0 <= c["precision"] <= 1.0
        assert 0.0 <= c["recall"] <= 1.0


def test_find_thresholds_precision_focus():
    rows = _make_features()
    candidates = find_thresholds(rows, top_n=5)
    # Best candidate should have reasonable precision
    best = max(candidates, key=lambda c: c["precision"])
    assert best["precision"] >= 0.5
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_analyze.py -v
```

Expected: `ImportError: No module named 'src.algo_detective.analyze'`

- [ ] **Step 3: Create `src/algo_detective/analyze.py`**

```python
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import ks_2samp

from .store import get_all_features

logger = logging.getLogger(__name__)

_NUMERIC_FEATURES = [
    "rsi", "adx", "rv20", "bb_pct_b", "bb_width_pct", "atr_pct",
    "volume_ratio", "roc20", "macd_histogram", "pct_from_52wk_high",
    "price_vs_ema20_pct", "price_vs_ema50_pct", "price_vs_ema150_pct", "price_vs_ema200_pct",
    "price_vs_sma20_pct", "price_vs_sma50_pct", "price_vs_sma150_pct", "price_vs_sma200_pct",
]

_BOOLEAN_FEATURES = [
    "price_above_ema20", "price_above_ema50", "price_above_ema150", "price_above_ema200",
    "price_above_sma20", "price_above_sma50", "price_above_sma150", "price_above_sma200",
    "ema20_above_ema50", "ema50_above_ema150", "ema50_above_ema200", "ema150_above_ema200",
    "sma20_above_sma50", "sma50_above_sma150", "sma50_above_sma200", "sma150_above_sma200",
    "price_above_bb_middle", "price_above_bb_upper", "price_below_bb_lower",
]


def rank_features(features: list[dict]) -> list[dict]:
    """Rank all features by KS statistic (prime vs control distribution separation)."""
    prime = [f for f in features if f["is_prime"] == 1]
    control = [f for f in features if f["is_prime"] == 0]
    if not prime or not control:
        return []

    rankings = []

    for feat in _NUMERIC_FEATURES:
        p_vals = [f[feat] for f in prime if f.get(feat) is not None]
        c_vals = [f[feat] for f in control if f.get(feat) is not None]
        if len(p_vals) < 5 or len(c_vals) < 5:
            continue
        stat, pval = ks_2samp(p_vals, c_vals)
        rankings.append({
            "feature": feat,
            "ks_stat": round(float(stat), 4),
            "p_value": float(pval),
            "prime_mean": round(float(np.mean(p_vals)), 4),
            "control_mean": round(float(np.mean(c_vals)), 4),
            "type": "numeric",
        })

    for feat in _BOOLEAN_FEATURES:
        p_vals = [f[feat] for f in prime if f.get(feat) is not None]
        c_vals = [f[feat] for f in control if f.get(feat) is not None]
        if len(p_vals) < 5 or len(c_vals) < 5:
            continue
        p_rate = float(np.mean(p_vals))
        c_rate = float(np.mean(c_vals))
        # Use absolute difference as the KS proxy for booleans
        stat = abs(p_rate - c_rate)
        rankings.append({
            "feature": feat,
            "ks_stat": round(stat, 4),
            "p_value": None,
            "prime_mean": round(p_rate, 4),
            "control_mean": round(c_rate, 4),
            "type": "boolean",
        })

    return sorted(rankings, key=lambda r: r["ks_stat"], reverse=True)


def _apply_criteria(row: dict, criteria: dict) -> bool:
    """Return True if row satisfies all criteria."""
    for key, val in criteria.items():
        if key.endswith("_min"):
            feat = key[:-4]
            if row.get(feat) is None or row[feat] < val:
                return False
        elif key.endswith("_max"):
            feat = key[:-4]
            if row.get(feat) is None or row[feat] > val:
                return False
        elif isinstance(val, bool):
            expected = int(val)
            if row.get(key) != expected:
                return False
    return True


def _score_criteria(features: list[dict], criteria: dict) -> dict:
    prime = [f for f in features if f["is_prime"] == 1]
    control = [f for f in features if f["is_prime"] == 0]
    tp = sum(1 for f in prime if _apply_criteria(f, criteria))
    fp = sum(1 for f in control if _apply_criteria(f, criteria))
    fn = len(prime) - tp
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / len(prime) if prime else 0.0
    return {
        "criteria": criteria,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }


def find_thresholds(features: list[dict], top_n: int = 10) -> list[dict]:
    """Grid-search thresholds for top-ranked features. Returns criteria candidates sorted by precision."""
    rankings = rank_features(features)
    top = rankings[:top_n]

    # Build a candidate pool — start with each top feature individually, then combine
    candidates = []

    # Boolean features: just require True for those with higher prime_mean than control_mean
    bool_criteria: dict = {}
    for r in top:
        if r["type"] == "boolean" and r["prime_mean"] > r["control_mean"] + 0.15:
            bool_criteria[r["feature"]] = True

    if bool_criteria:
        candidates.append(_score_criteria(features, bool_criteria))

    # Numeric features: grid-search percentile-based min/max thresholds
    prime = [f for f in features if f["is_prime"] == 1]
    for r in [x for x in top if x["type"] == "numeric"]:
        feat = r["feature"]
        p_vals = sorted(f[feat] for f in prime if f.get(feat) is not None)
        if len(p_vals) < 10:
            continue
        p10 = float(np.percentile(p_vals, 10))
        p90 = float(np.percentile(p_vals, 90))
        # Try with just this numeric constraint plus the bool constraints
        crit = {**bool_criteria, f"{feat}_min": round(p10, 2), f"{feat}_max": round(p90, 2)}
        candidates.append(_score_criteria(features, crit))

    # Combined: add top-2 numeric constraints together
    num_top = [r for r in top if r["type"] == "numeric"][:2]
    if len(num_top) == 2:
        crit = dict(bool_criteria)
        for r in num_top:
            feat = r["feature"]
            p_vals = sorted(f[feat] for f in prime if f.get(feat) is not None)
            if len(p_vals) >= 10:
                crit[f"{feat}_min"] = round(float(np.percentile(p_vals, 10)), 2)
                crit[f"{feat}_max"] = round(float(np.percentile(p_vals, 90)), 2)
        candidates.append(_score_criteria(features, crit))

    return sorted(candidates, key=lambda c: (c["precision"], c["recall"]), reverse=True)


def run_analyze(output_dir: Path | None = None) -> None:
    if output_dir is None:
        output_dir = Path(__file__).parent.parent.parent / "data" / "detective"
    output_dir.mkdir(parents=True, exist_ok=True)

    features = get_all_features()
    prime_count = sum(1 for f in features if f["is_prime"] == 1)
    control_count = len(features) - prime_count
    logger.info("Analyzing %d prime + %d control rows", prime_count, control_count)

    rankings = rank_features(features)
    candidates = find_thresholds(features, top_n=10)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_prime": prime_count,
        "total_control": control_count,
        "feature_rankings": rankings,
        "criteria_candidates": candidates,
    }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = output_dir / f"analysis_{today}.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info("Analysis written to %s", out_path)

    print(f"\n=== Top 10 discriminating features ===")
    for i, r in enumerate(rankings[:10], 1):
        print(f"  {i:2}. {r['feature']:<30} KS={r['ks_stat']:.3f}  prime_mean={r['prime_mean']:.3f}  control_mean={r['control_mean']:.3f}")

    print(f"\n=== Top 3 criteria candidates ===")
    for i, c in enumerate(candidates[:3], 1):
        print(f"  {i}. precision={c['precision']:.3f}  recall={c['recall']:.3f}  TP={c['true_positives']}  FP={c['false_positives']}")
        print(f"     {c['criteria']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_analyze()
```

- [ ] **Step 4: Run unit tests**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_analyze.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/analyze.py tests/test_algo_detective_analyze.py
git commit -m "feat(algo-detective): analyze module — KS ranking and criteria candidates"
```

---

## Task 7: validate.py — criteria scorer

**Files:**
- Create: `src/algo_detective/validate.py`

**Interfaces:**
- Consumes: `get_all_features()` from store.py; criteria JSON via `--criteria` CLI flag
- Produces: stdout report of precision, recall, false positives by sector, missed primes; return code 0

- [ ] **Step 1: Create `src/algo_detective/validate.py`**

```python
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from .analyze import _apply_criteria
from .store import get_all_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def validate_criteria(criteria: dict, features: list[dict] | None = None) -> dict:
    """Score a criteria dict against detective_features. Returns precision/recall report."""
    if features is None:
        features = get_all_features()

    prime = [f for f in features if f["is_prime"] == 1]
    control = [f for f in features if f["is_prime"] == 0]

    tp_rows = [f for f in prime if _apply_criteria(f, criteria)]
    fp_rows = [f for f in control if _apply_criteria(f, criteria)]
    fn_rows = [f for f in prime if not _apply_criteria(f, criteria)]

    tp = len(tp_rows)
    fp = len(fp_rows)
    fn = len(fn_rows)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / len(prime) if prime else 0.0

    fp_by_sector = Counter(f.get("sector") or "Unknown" for f in fp_rows)
    missed = [
        {"date": f["date"], "ticker": f["ticker"], "rsi": f.get("rsi"), "adx": f.get("adx"),
         "price_above_ema50": f.get("price_above_ema50"), "bb_pct_b": f.get("bb_pct_b")}
        for f in fn_rows
    ]

    return {
        "criteria": criteria,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "total_prime": len(prime),
        "fp_by_sector": dict(fp_by_sector.most_common()),
        "missed_primes": sorted(missed, key=lambda r: (r["date"], r["ticker"])),
    }


def print_report(report: dict) -> None:
    c = report["criteria"]
    print(f"\n{'='*60}")
    print(f"Criteria: {json.dumps(c, indent=2)}")
    print(f"\nPrecision : {report['precision']:.1%}  ({report['true_positives']} TP / {report['true_positives'] + report['false_positives']} fired)")
    print(f"Recall    : {report['recall']:.1%}  ({report['true_positives']} / {report['total_prime']} prime tickers caught)")
    print(f"False positives: {report['false_positives']}")
    print(f"Missed primes : {report['false_negatives']}")

    if report["fp_by_sector"]:
        print(f"\nFalse positives by sector:")
        for sector, count in list(report["fp_by_sector"].items())[:10]:
            print(f"  {sector:<30} {count}")

    if report["missed_primes"]:
        print(f"\nMissed prime tickers (first 20):")
        for r in report["missed_primes"][:20]:
            print(f"  {r['date']}  {r['ticker']:<8}  RSI={r['rsi']}  ADX={r['adx']}  EMA50+={r['price_above_ema50']}  BB%B={r['bb_pct_b']}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate criteria against the feature matrix")
    parser.add_argument(
        "--criteria",
        required=True,
        help='JSON string or path to .json file. E.g. \'{"rsi_min": 42, "price_above_ema50": true}\'',
    )
    args = parser.parse_args()

    criteria_input = args.criteria.strip()
    if criteria_input.endswith(".json") and Path(criteria_input).exists():
        criteria = json.loads(Path(criteria_input).read_text())
    else:
        criteria = json.loads(criteria_input)

    report = validate_criteria(criteria)
    print_report(report)
```

- [ ] **Step 2: Test validate via CLI with real data**

```bash
docker compose run --rm pipeline python -m src.algo_detective.validate \
  --criteria '{"price_above_ema50": true, "rsi_min": 40, "rsi_max": 70}'
```

Expected: precision and recall printed, list of missed prime tickers and false-positive sector breakdown.

- [ ] **Step 3: Commit**

```bash
git add src/algo_detective/validate.py
git commit -m "feat(algo-detective): validate module — criteria scorer with precision/recall report"
```

---

## Task 8: Full end-to-end integration run + doc commit

- [ ] **Step 1: Run full build (should be idempotent — no re-computation)**

```bash
docker compose run --rm pipeline python -m src.algo_detective.build
```

Expected: "0 skipped" logging shows all rows already computed. Counts match Task 5.

- [ ] **Step 2: Run analyze and inspect output**

```bash
docker compose run --rm pipeline python -m src.algo_detective.analyze
```

Expected: `data/detective/analysis_YYYY-MM-DD.json` created. Top features printed to stdout. Note which features rank highest — this is the first round of findings.

- [ ] **Step 3: Validate a starting criteria hypothesis**

```bash
docker compose run --rm pipeline python -m src.algo_detective.validate \
  --criteria '{"price_above_ema50": true, "ema20_above_ema50": true, "rsi_min": 40, "rsi_max": 70, "adx_min": 15}'
```

Record precision and recall. Adjust thresholds and re-run until satisfied.

- [ ] **Step 4: Run the full test suite**

```bash
docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py tests/test_algo_detective_ingest.py tests/test_algo_detective_features.py tests/test_algo_detective_analyze.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit spec, plan, CSV, and session notes**

```bash
git add docs/superpowers/specs/2026-06-18-prime-state-algo-detective-design.md
git add docs/superpowers/plans/2026-06-18-algo-detective.md
git add data/detective/prime_tickers.csv
git commit -m "docs: algo detective spec, plan, and prime tickers CSV"
```

- [ ] **Step 6: Save initial findings to sessions/**

```bash
mkdir -p /home/dev/workspace/Market-Intelligence/data/detective/sessions
```

After reviewing `analysis_*.json`, create `data/detective/sessions/session-01.md` manually with notes on top features and first criteria hypothesis. This is human-edited and not auto-generated.

---

## Self-Review Notes

**Spec coverage check:**
- ✅ `src/algo_detective/` module with all 8 files
- ✅ All features from spec: EMA/SMA 20/50/150/200, Bollinger Bands, RSI, ADX, ROC20, MACD histogram, RV-20, ATR%, volume ratio, 52wk high distance
- ✅ Crossover booleans: ema20>ema50, ema50>ema150, ema50>ema200, ema150>ema200, all SMA equivalents
- ✅ Bollinger Bands: upper/middle/lower values, %B, width%, three position booleans
- ✅ Macro context: VIX, Fear&Greed, posture, SPY EMA/RSI, top sectors
- ✅ Comparison universe: market_cap_b >= 3.0, price >= 5.0, excludes prime tickers per date
- ✅ Idempotent build (computed_pairs check)
- ✅ RSI/ADX cross-validation warnings
- ✅ `--inspect DATE` flag
- ✅ KS ranking (Pass 1), threshold discovery (Pass 2), criteria output (Pass 3) in analyze.py
- ✅ validate.py with precision/recall/sector breakdown/missed primes
- ✅ Persistence across sessions via SQLite + data/detective/
- ✅ scipy added to pyproject.toml

**Type consistency:** `_apply_criteria` defined once in `analyze.py`, imported by `validate.py` — consistent across both modules.

**No placeholders:** All code blocks are complete and runnable.
