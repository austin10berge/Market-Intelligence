# Wheel Equity Curve vs SPY Benchmark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a YTD mark-to-market equity curve chart with SPY benchmark comparison and expandable stats panel to the v2 wheel tracker page.

**Architecture:** New `wt_equity_curve` table stores precomputed daily portfolio values and SPY closes. A builder module replays `wt_trades` chronologically, prices open equity positions via yfinance, and writes the curve. The API endpoint reads the curve, computes TWR % returns (adjusting for deposits) and stats, and serves it to a `lightweight-charts` dual-line chart in `wheel.js`.

**Tech Stack:** Python 3.12, SQLite, yfinance (via existing `download_with_retry`), lightweight-charts v3.8.0 (already loaded in v2), FastAPI

## Global Constraints

- All Python runs inside Docker — use `docker compose run --rm test` for tests.
- yfinance calls MUST go through `src.fetchers._yf_lock.download_with_retry` (serialization lock).
- Exclude `tests/test_stock_screener.py` when running full test suite.
- Frontend is vanilla JS (no build step), served from `src/web/v2/`.
- `~/.local/bin/ruff` auto-formats on save via hook — no manual format step.

---

### Task 1: Database table and store helpers

**Files:**
- Modify: `src/wheel_tracker/store.py` — add table DDL to `ensure_wheel_tables()`, add read/write helpers
- Test: `tests/test_wheel_equity_curve.py` — new file

**Interfaces:**
- Consumes: nothing new
- Produces:
  - `ensure_wheel_tables(conn)` — now also creates `wt_equity_curve`
  - `write_equity_curve(conn: sqlite3.Connection, rows: list[dict]) -> None` — DELETE+INSERT full replace. Each dict has keys: `date` (str), `equity` (float), `cash` (float), `deposits` (float), `spy_close` (float|None).
  - `read_equity_curve(conn: sqlite3.Connection, since: str) -> list[dict]` — returns rows where `date >= since`, ordered by date. Same dict shape as write input.

- [ ] **Step 1: Write failing tests**

Create `tests/test_wheel_equity_curve.py`:

```python
"""Tests for wt_equity_curve table and store helpers."""
from __future__ import annotations

import sqlite3
import pytest
from src.wheel_tracker.store import ensure_wheel_tables, write_equity_curve, read_equity_curve


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    ensure_wheel_tables(c)
    return c


def test_write_and_read_equity_curve(conn):
    rows = [
        {"date": "2026-01-02", "equity": 20000.0, "cash": 20000.0, "deposits": 20000.0, "spy_close": 480.0},
        {"date": "2026-01-03", "equity": 20050.0, "cash": 19800.0, "deposits": 20000.0, "spy_close": 481.5},
    ]
    write_equity_curve(conn, rows)
    result = read_equity_curve(conn, "2026-01-01")
    assert len(result) == 2
    assert result[0]["date"] == "2026-01-02"
    assert result[0]["equity"] == 20000.0
    assert result[1]["spy_close"] == 481.5


def test_write_replaces_existing(conn):
    rows_v1 = [{"date": "2026-01-02", "equity": 100.0, "cash": 100.0, "deposits": 0.0, "spy_close": 480.0}]
    rows_v2 = [{"date": "2026-01-02", "equity": 200.0, "cash": 200.0, "deposits": 0.0, "spy_close": 482.0}]
    write_equity_curve(conn, rows_v1)
    write_equity_curve(conn, rows_v2)
    result = read_equity_curve(conn, "2026-01-01")
    assert len(result) == 1
    assert result[0]["equity"] == 200.0


def test_read_filters_by_date(conn):
    rows = [
        {"date": "2025-12-30", "equity": 19000.0, "cash": 19000.0, "deposits": 20000.0, "spy_close": 475.0},
        {"date": "2026-01-02", "equity": 20000.0, "cash": 20000.0, "deposits": 20000.0, "spy_close": 480.0},
    ]
    write_equity_curve(conn, rows)
    result = read_equity_curve(conn, "2026-01-01")
    assert len(result) == 1
    assert result[0]["date"] == "2026-01-02"


def test_read_empty_table(conn):
    result = read_equity_curve(conn, "2026-01-01")
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_equity_curve.py -v`
Expected: ImportError — `write_equity_curve` and `read_equity_curve` don't exist yet.

- [ ] **Step 3: Implement store helpers**

In `src/wheel_tracker/store.py`:

Add to `ensure_wheel_tables()`, after the existing `CREATE TABLE` statements inside the `executescript`:

```sql
CREATE TABLE IF NOT EXISTS wt_equity_curve (
    date       TEXT PRIMARY KEY,
    equity     REAL NOT NULL,
    cash       REAL NOT NULL,
    deposits   REAL NOT NULL DEFAULT 0,
    spy_close  REAL
);
```

Add two new functions at the bottom of the file:

```python
def write_equity_curve(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.execute("DELETE FROM wt_equity_curve")
    conn.executemany(
        """
        INSERT INTO wt_equity_curve (date, equity, cash, deposits, spy_close)
        VALUES (:date, :equity, :cash, :deposits, :spy_close)
        """,
        rows,
    )
    conn.commit()


def read_equity_curve(conn: sqlite3.Connection, since: str) -> list[dict]:
    _prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT date, equity, cash, deposits, spy_close FROM wt_equity_curve WHERE date >= ? ORDER BY date",
        (since,),
    ).fetchall()
    conn.row_factory = _prev
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_equity_curve.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wheel_tracker/store.py tests/test_wheel_equity_curve.py
git commit -m "feat(wheel): add wt_equity_curve table and store helpers"
```

---

### Task 2: Equity curve builder

**Files:**
- Create: `src/wheel_tracker/equity_curve.py`
- Test: `tests/test_wheel_equity_curve.py` (append)

**Interfaces:**
- Consumes:
  - `write_equity_curve(conn, rows)` from Task 1
  - `download_with_retry` from `src.fetchers._yf_lock`
  - `wt_trades` table (read)
- Produces:
  - `rebuild_equity_curve(conn: sqlite3.Connection) -> int` — builds the full YTD equity curve from trade history, fetches SPY+held-ticker prices via yfinance, writes to `wt_equity_curve`. Returns number of rows written. This is an `async` function (because `download_with_retry` is async).
  - `DEPOSIT_EVENTS: list[dict]` — module-level constant, list of `{"date": str, "amount": float}` for known deposits.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_wheel_equity_curve.py`:

```python
from unittest.mock import patch, AsyncMock
import asyncio
from src.wheel_tracker.equity_curve import rebuild_equity_curve, DEPOSIT_EVENTS


def test_deposit_events_defined():
    assert len(DEPOSIT_EVENTS) >= 2
    for evt in DEPOSIT_EVENTS:
        assert "date" in evt
        assert "amount" in evt


def _insert_test_trades(conn):
    """Insert a minimal set of trades: one CSP open (premium in) and a stock buy."""
    from src.wheel_tracker.store import ensure_wheel_tables
    ensure_wheel_tables(conn)
    conn.executemany(
        """INSERT INTO wt_trades
           (schwab_transaction_id, account_id, executed_at, asset_type, symbol,
            underlying, option_type, strike, expiration, instruction, quantity,
            price, commission, net_amount)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("t1", "A1", "2026-01-06T10:00:00", "OPTION", "AAPL  260117P00200000",
             "AAPL", "PUT", 200.0, "2026-01-17", "SELL_TO_OPEN", 1, 3.50, 0.65, 349.35),
            ("t2", "A1", "2026-01-10T10:00:00", "EQUITY", "AAPL",
             None, None, None, None, "BUY", 100, 195.0, 0.0, -19500.0),
        ],
    )
    conn.commit()


def _make_spy_df():
    """Build a minimal DataFrame mimicking yfinance output for SPY."""
    import pandas as pd
    dates = pd.bdate_range("2026-01-02", "2026-01-14")
    closes = [480.0 + i * 0.5 for i in range(len(dates))]
    return pd.DataFrame({"Close": closes}, index=dates)


def _make_aapl_df():
    """Build a minimal DataFrame mimicking yfinance output for AAPL."""
    import pandas as pd
    dates = pd.bdate_range("2026-01-02", "2026-01-14")
    closes = [195.0 + i * 0.3 for i in range(len(dates))]
    return pd.DataFrame({"Close": closes}, index=dates)


def test_rebuild_equity_curve_basic(conn):
    _insert_test_trades(conn)

    async def mock_download(*args, **kwargs):
        import pandas as pd
        tickers_arg = args[0] if args else kwargs.get("tickers", "")
        if "SPY" in tickers_arg and "AAPL" in tickers_arg:
            spy_df = _make_spy_df()
            aapl_df = _make_aapl_df()
            result = pd.concat({"SPY": spy_df, "AAPL": aapl_df}, axis=1)
            return result
        elif "SPY" in tickers_arg:
            return _make_spy_df()
        return _make_aapl_df()

    with patch("src.wheel_tracker.equity_curve.download_with_retry", side_effect=mock_download):
        with patch("src.wheel_tracker.equity_curve._ytd_start", return_value="2026-01-02"):
            count = asyncio.get_event_loop().run_until_complete(rebuild_equity_curve(conn))

    assert count > 0
    from src.wheel_tracker.store import read_equity_curve
    curve = read_equity_curve(conn, "2026-01-01")
    assert len(curve) == count
    assert curve[0]["spy_close"] is not None
    # After the AAPL buy on Jan 10, equity should include mark-to-market stock value
    post_buy = [r for r in curve if r["date"] >= "2026-01-10"]
    assert len(post_buy) > 0
    for r in post_buy:
        assert r["equity"] > r["cash"]  # equity includes stock position value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_equity_curve.py::test_rebuild_equity_curve_basic -v`
Expected: ImportError — `rebuild_equity_curve` doesn't exist yet.

- [ ] **Step 3: Implement the builder**

Create `src/wheel_tracker/equity_curve.py`:

```python
"""Build precomputed YTD equity curve from wt_trades + yfinance prices."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime

import pandas as pd

from ..fetchers._yf_lock import download_with_retry
from .store import write_equity_curve

logger = logging.getLogger(__name__)

DEPOSIT_EVENTS = [
    {"date": "2025-12-01", "amount": 20000.0},
    # Exact date TBD — will be confirmed from Schwab transaction history
    {"date": "2026-07-01", "amount": 25000.0},
]


def _ytd_start() -> str:
    return f"{date.today().year}-01-01"


def _cumulative_deposits_at(as_of: str) -> float:
    return sum(d["amount"] for d in DEPOSIT_EVENTS if d["date"] <= as_of)


def _deposit_on_date(dt: str) -> float:
    return sum(d["amount"] for d in DEPOSIT_EVENTS if d["date"] == dt)


async def rebuild_equity_curve(conn: sqlite3.Connection) -> int:
    ytd = _ytd_start()

    trades = conn.execute(
        """
        SELECT executed_at, asset_type, symbol, underlying, instruction,
               quantity, net_amount
        FROM wt_trades
        ORDER BY executed_at
        """,
    ).fetchall()
    cols = ["executed_at", "asset_type", "symbol", "underlying", "instruction",
            "quantity", "net_amount"]
    trades = [dict(zip(cols, r)) for r in trades]

    initial_cash = _cumulative_deposits_at(ytd)

    cash = initial_cash
    positions: dict[str, dict] = {}  # symbol -> {"qty": float, "avg_cost": float}

    # Replay all trades before YTD to establish starting state
    pre_ytd = [t for t in trades if t["executed_at"][:10] < ytd]
    for t in pre_ytd:
        cash, positions = _apply_trade(cash, positions, t)

    ytd_trades = [t for t in trades if t["executed_at"][:10] >= ytd]
    trade_by_date: dict[str, list[dict]] = {}
    for t in ytd_trades:
        d = t["executed_at"][:10]
        trade_by_date.setdefault(d, []).append(t)

    held_tickers = set()
    for t in trades:
        sym = t["underlying"] or t["symbol"]
        if t["asset_type"] == "EQUITY":
            held_tickers.add(sym)
    held_tickers.add("SPY")

    tickers_str = " ".join(sorted(held_tickers))
    prices_df = await download_with_retry(
        tickers_str,
        start=ytd,
        progress=False,
        auto_adjust=True,
    )

    if prices_df is None or prices_df.empty:
        logger.warning("equity_curve: no price data returned from yfinance")
        return 0

    if isinstance(prices_df.columns, pd.MultiIndex):
        trading_dates = sorted(prices_df.index)
    else:
        trading_dates = sorted(prices_df.index)

    rows = []
    for dt in trading_dates:
        dt_str = dt.strftime("%Y-%m-%d")

        day_trades = trade_by_date.get(dt_str, [])
        for t in day_trades:
            cash, positions = _apply_trade(cash, positions, t)

        deposit_today = _deposit_on_date(dt_str)
        if deposit_today > 0 and dt_str >= ytd:
            cash += deposit_today

        stock_value = 0.0
        for sym, pos in positions.items():
            if pos["qty"] == 0:
                continue
            try:
                if isinstance(prices_df.columns, pd.MultiIndex):
                    close = float(prices_df.loc[dt, (sym, "Close")])
                else:
                    close = float(prices_df.loc[dt, "Close"])
            except (KeyError, TypeError):
                close = pos["avg_cost"]
            if pd.isna(close):
                close = pos["avg_cost"]
            stock_value += pos["qty"] * close

        try:
            if isinstance(prices_df.columns, pd.MultiIndex):
                spy_close = float(prices_df.loc[dt, ("SPY", "Close")])
            else:
                spy_close = float(prices_df.loc[dt, "Close"]) if "SPY" in tickers_str else None
        except (KeyError, TypeError):
            spy_close = None
        if spy_close is not None and pd.isna(spy_close):
            spy_close = None

        equity = cash + stock_value
        cumulative_deps = _cumulative_deposits_at(dt_str)

        rows.append({
            "date": dt_str,
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "deposits": cumulative_deps,
            "spy_close": round(spy_close, 4) if spy_close is not None else None,
        })

    write_equity_curve(conn, rows)
    logger.info("equity_curve: wrote %d rows", len(rows))
    return len(rows)


def _apply_trade(
    cash: float,
    positions: dict[str, dict],
    trade: dict,
) -> tuple[float, dict[str, dict]]:
    net = trade["net_amount"] or 0
    cash += net

    if trade["asset_type"] == "EQUITY":
        sym = trade["symbol"]
        qty = abs(trade["quantity"] or 0)
        price = abs(net) / qty if qty > 0 else 0

        if trade["instruction"] in ("BUY", "BUY_TO_OPEN"):
            pos = positions.setdefault(sym, {"qty": 0, "avg_cost": 0})
            total_cost = pos["qty"] * pos["avg_cost"] + qty * price
            pos["qty"] += qty
            pos["avg_cost"] = total_cost / pos["qty"] if pos["qty"] > 0 else 0
        elif trade["instruction"] in ("SELL", "SELL_TO_CLOSE"):
            pos = positions.get(sym)
            if pos:
                pos["qty"] = max(0, pos["qty"] - qty)
                if pos["qty"] == 0:
                    pos["avg_cost"] = 0

    return cash, positions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_equity_curve.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wheel_tracker/equity_curve.py tests/test_wheel_equity_curve.py
git commit -m "feat(wheel): add equity curve builder with mark-to-market replay"
```

---

### Task 3: Stats computation

**Files:**
- Create: `src/wheel_tracker/curve_stats.py`
- Test: `tests/test_wheel_equity_curve.py` (append)

**Interfaces:**
- Consumes: equity curve rows from `read_equity_curve()` (Task 1)
- Produces:
  - `compute_curve_stats(curve: list[dict]) -> dict` — takes the raw `wt_equity_curve` rows and returns:
    ```python
    {
        "net_pnl": float,           # final equity - (initial deposits)
        "net_pnl_pct": float,       # TWR percentage return
        "max_drawdown_pct": float,  # negative number
        "sharpe_ratio": float | None,
        "sortino_ratio": float | None,
        "annualized_yield_pct": float | None,
        "avg_weekly_roc_pct": float | None,
    }
    ```
  - `compute_twr_curve(curve: list[dict]) -> list[dict]` — returns `[{"date": str, "pct": float}]` with TWR-adjusted % returns (0.0 on first day). Splits at deposit boundaries and chains sub-period returns.
  - `compute_spy_curve(curve: list[dict]) -> list[dict]` — returns `[{"date": str, "pct": float}]` normalized to 0% on first day.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_wheel_equity_curve.py`:

```python
from src.wheel_tracker.curve_stats import compute_curve_stats, compute_twr_curve, compute_spy_curve


def _sample_curve():
    """20 days of steadily rising equity, no deposits after start."""
    return [
        {"date": f"2026-01-{d:02d}", "equity": 20000 + d * 50, "cash": 20000 + d * 50,
         "deposits": 20000.0, "spy_close": 480.0 + d * 0.5}
        for d in range(2, 22)
    ]


def test_compute_spy_curve():
    curve = _sample_curve()
    spy = compute_spy_curve(curve)
    assert len(spy) == len(curve)
    assert spy[0]["pct"] == 0.0
    assert spy[-1]["pct"] > 0  # SPY rose over the period


def test_compute_twr_curve_no_deposits():
    curve = _sample_curve()
    twr = compute_twr_curve(curve)
    assert len(twr) == len(curve)
    assert twr[0]["pct"] == 0.0
    assert twr[-1]["pct"] > 0


def test_compute_twr_curve_with_deposit():
    curve = [
        {"date": "2026-01-02", "equity": 20000, "cash": 20000, "deposits": 20000, "spy_close": 480},
        {"date": "2026-01-03", "equity": 20100, "cash": 20100, "deposits": 20000, "spy_close": 481},
        # Deposit of 5000 on Jan 6 — deposits jumps from 20000 to 25000
        {"date": "2026-01-06", "equity": 25200, "cash": 25200, "deposits": 25000, "spy_close": 482},
        {"date": "2026-01-07", "equity": 25400, "cash": 25400, "deposits": 25000, "spy_close": 483},
    ]
    twr = compute_twr_curve(curve)
    # Without TWR, raw return = (25400 - 20000) / 20000 = 27%
    # With TWR, the deposit is factored out — return should be much lower
    assert twr[-1]["pct"] < 20.0


def test_compute_stats_basic():
    curve = _sample_curve()
    stats = compute_curve_stats(curve)
    assert stats["net_pnl"] > 0
    assert stats["net_pnl_pct"] > 0
    assert stats["max_drawdown_pct"] <= 0
    assert stats["sharpe_ratio"] is not None
    assert stats["annualized_yield_pct"] is not None
    assert stats["avg_weekly_roc_pct"] is not None


def test_compute_stats_empty():
    stats = compute_curve_stats([])
    assert stats["net_pnl"] == 0
    assert stats["sharpe_ratio"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_equity_curve.py::test_compute_stats_basic -v`
Expected: ImportError — `curve_stats` module doesn't exist.

- [ ] **Step 3: Implement stats computation**

Create `src/wheel_tracker/curve_stats.py`:

```python
"""Stats and TWR computation for the wheel equity curve."""
from __future__ import annotations

import math


def compute_spy_curve(curve: list[dict]) -> list[dict]:
    if not curve or curve[0]["spy_close"] is None:
        return []
    first = curve[0]["spy_close"]
    if first <= 0:
        return []
    return [
        {"date": r["date"], "pct": round(((r["spy_close"] / first) - 1) * 100, 4) if r["spy_close"] else 0.0}
        for r in curve
    ]


def compute_twr_curve(curve: list[dict]) -> list[dict]:
    if not curve:
        return []

    result = [{"date": curve[0]["date"], "pct": 0.0}]
    cumulative = 1.0
    prev_deposits = curve[0]["deposits"]

    for i in range(1, len(curve)):
        prev_equity = curve[i - 1]["equity"]
        curr_equity = curve[i]["equity"]
        curr_deposits = curve[i]["deposits"]
        deposit_delta = curr_deposits - prev_deposits

        if prev_equity > 0:
            adjusted_prev = prev_equity + deposit_delta
            period_return = (curr_equity / adjusted_prev) if adjusted_prev > 0 else 1.0
        else:
            period_return = 1.0

        cumulative *= period_return
        prev_deposits = curr_deposits
        result.append({"date": curve[i]["date"], "pct": round((cumulative - 1) * 100, 4)})

    return result


def compute_curve_stats(curve: list[dict]) -> dict:
    empty = {
        "net_pnl": 0, "net_pnl_pct": 0, "max_drawdown_pct": 0,
        "sharpe_ratio": None, "sortino_ratio": None,
        "annualized_yield_pct": None, "avg_weekly_roc_pct": None,
    }
    if len(curve) < 2:
        return empty

    twr = compute_twr_curve(curve)
    total_return_pct = twr[-1]["pct"]

    final_equity = curve[-1]["equity"]
    final_deposits = curve[-1]["deposits"]
    net_pnl = final_equity - final_deposits

    # Daily returns from TWR curve
    daily_returns = []
    for i in range(1, len(twr)):
        prev_factor = 1 + twr[i - 1]["pct"] / 100
        curr_factor = 1 + twr[i]["pct"] / 100
        if prev_factor > 0:
            daily_returns.append(curr_factor / prev_factor - 1)

    # Sharpe
    sharpe = None
    if len(daily_returns) > 1:
        avg = sum(daily_returns) / len(daily_returns)
        std = _std(daily_returns)
        if std > 0:
            sharpe = round((avg / std) * math.sqrt(252), 3)

    # Sortino
    sortino = None
    if len(daily_returns) > 1:
        avg = sum(daily_returns) / len(daily_returns)
        down = [r for r in daily_returns if r < 0]
        if down:
            down_std = _std(down)
            if down_std > 0:
                sortino = round((avg / down_std) * math.sqrt(252), 3)

    # Max drawdown (on TWR cumulative factors)
    factors = [1 + t["pct"] / 100 for t in twr]
    max_dd = 0.0
    peak = factors[0]
    for f in factors:
        if f > peak:
            peak = f
        dd = (peak - f) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Annualized yield
    n_days = len(curve)
    annualized = None
    total_factor = 1 + total_return_pct / 100
    if n_days > 1 and total_factor > 0:
        annualized = round((math.pow(total_factor, 252 / n_days) - 1) * 100, 2)

    # Avg weekly ROC
    weekly_roc = None
    if len(twr) >= 5:
        weekly_returns = []
        i = 0
        while i + 5 <= len(twr):
            prev_f = 1 + twr[i]["pct"] / 100
            curr_f = 1 + twr[i + 5]["pct"] / 100 if i + 5 < len(twr) else 1 + twr[-1]["pct"] / 100
            if prev_f > 0:
                weekly_returns.append((curr_f / prev_f - 1) * 100)
            i += 5
        if weekly_returns:
            weekly_roc = round(sum(weekly_returns) / len(weekly_returns), 3)

    return {
        "net_pnl": round(net_pnl, 2),
        "net_pnl_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(-max_dd * 100, 2),
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "annualized_yield_pct": annualized,
        "avg_weekly_roc_pct": weekly_roc,
    }


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_equity_curve.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wheel_tracker/curve_stats.py tests/test_wheel_equity_curve.py
git commit -m "feat(wheel): add TWR and stats computation for equity curve"
```

---

### Task 4: API endpoint and sync integration

**Files:**
- Modify: `src/api/main.py` — add `GET /api/wheel/equity-curve` and `POST /api/wheel/rebuild-curve`
- Modify: `src/wheel_tracker/sync.py` — call `rebuild_equity_curve` after trade sync
- Modify: `src/wheel_tracker/__init__.py` — re-export `rebuild_equity_curve`
- Test: `tests/test_wheel_equity_curve.py` (append)

**Interfaces:**
- Consumes:
  - `read_equity_curve(conn, since)` from Task 1
  - `compute_curve_stats(curve)`, `compute_twr_curve(curve)`, `compute_spy_curve(curve)` from Task 3
  - `rebuild_equity_curve(conn)` from Task 2
- Produces:
  - `GET /api/wheel/equity-curve` — returns `{"portfolio_curve": [...], "spy_curve": [...], "stats": {...}}`
  - `POST /api/wheel/rebuild-curve` — triggers rebuild, returns `{"rows_written": N}`

- [ ] **Step 1: Write failing test for the API endpoint**

Append to `tests/test_wheel_equity_curve.py`:

```python
def test_api_equity_curve_response_shape(conn):
    """Verify the API-layer transform produces the expected response shape."""
    from src.wheel_tracker.store import write_equity_curve, read_equity_curve
    from src.wheel_tracker.curve_stats import compute_curve_stats, compute_twr_curve, compute_spy_curve

    rows = _sample_curve()
    write_equity_curve(conn, rows)
    curve = read_equity_curve(conn, f"{2026}-01-01")

    portfolio = compute_twr_curve(curve)
    spy = compute_spy_curve(curve)
    stats = compute_curve_stats(curve)

    assert len(portfolio) == len(rows)
    assert len(spy) == len(rows)
    assert portfolio[0]["pct"] == 0.0
    assert spy[0]["pct"] == 0.0
    assert "net_pnl" in stats
    assert "sharpe_ratio" in stats
    assert "annualized_yield_pct" in stats
    assert "avg_weekly_roc_pct" in stats
```

- [ ] **Step 2: Run test to verify it passes (this is an integration assertion, should pass with Tasks 1+3)**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_equity_curve.py::test_api_equity_curve_response_shape -v`
Expected: PASS (validates the data pipeline end-to-end).

- [ ] **Step 3: Add the API endpoints to `src/api/main.py`**

Add after the existing `wheel_stats` endpoint (around line 955):

```python
@app.get("/api/wheel/equity-curve")
def wheel_equity_curve(req: Request):
    from src.wheel_tracker.curve_stats import compute_curve_stats, compute_twr_curve, compute_spy_curve
    from src.wheel_tracker.store import read_equity_curve as wt_read_curve
    try:
        with closing(sqlite3.connect(settings.db_path)) as conn:
            from datetime import date
            ytd_start = f"{date.today().year}-01-01"
            curve = wt_read_curve(conn, ytd_start)
            if not curve:
                return {"portfolio_curve": [], "spy_curve": [], "stats": None}
            return {
                "portfolio_curve": compute_twr_curve(curve),
                "spy_curve": compute_spy_curve(curve),
                "stats": compute_curve_stats(curve),
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wheel/rebuild-curve")
async def wheel_rebuild_curve(req: Request):
    from src.wheel_tracker.equity_curve import rebuild_equity_curve
    try:
        with closing(sqlite3.connect(settings.db_path)) as conn:
            count = await rebuild_equity_curve(conn)
            return {"rows_written": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 4: Hook rebuild into the sync flow**

In `src/wheel_tracker/sync.py`, add the equity curve rebuild after alerts (around line 463, inside the `try` block, after `check_alerts`):

```python
            from .equity_curve import rebuild_equity_curve

            try:
                curve_rows = await rebuild_equity_curve(conn)
                logger.info("wheel_tracker: equity curve rebuilt (%d rows)", curve_rows)
            except Exception as exc:
                logger.warning("wheel_tracker: equity curve rebuild failed (non-fatal): %s", exc)
```

- [ ] **Step 5: Update `__init__.py` to export `rebuild_equity_curve`**

In `src/wheel_tracker/__init__.py`:

```python
from .sync import run_sync
from .equity_curve import rebuild_equity_curve

__all__ = ["run_sync", "rebuild_equity_curve"]
```

- [ ] **Step 6: Run full wheel tracker test suite**

Run: `docker compose run --rm test python3 -m pytest tests/test_wheel_equity_curve.py tests/test_wheel_tracker_store.py tests/test_wheel_tracker_sync.py -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/api/main.py src/wheel_tracker/sync.py src/wheel_tracker/__init__.py tests/test_wheel_equity_curve.py
git commit -m "feat(wheel): add equity-curve API endpoint and sync integration"
```

---

### Task 5: Frontend chart and expandable stats panel

**Files:**
- Modify: `src/web/v2/wheel.js` — add chart section and stats panel at top of page

**Interfaces:**
- Consumes: `GET /api/wheel/equity-curve` (Task 4)
- Produces: visual chart + expandable stats in the wheel tracker view

- [ ] **Step 1: Add the chart and stats rendering functions to `wheel.js`**

Add these functions after the existing `moneyColor` function (around line 33) and before the `renderStats` function:

```javascript
    // ── Performance Chart ──

    function renderPerfChart(data) {
        const portfolio = data.portfolio_curve || [];
        const spy = data.spy_curve || [];
        const stats = data.stats;

        if (!portfolio.length) {
            return `<div class="list-message">No equity curve data — run a trade sync to generate</div>`;
        }

        const headlinePct = stats ? stats.net_pnl_pct : 0;
        const headlineColor = headlinePct >= 0 ? 'var(--tv-green)' : 'var(--tv-red)';
        const headlineSign = headlinePct >= 0 ? '+' : '';

        let statsHtml = '';
        if (stats) {
            statsHtml = `
            <div id="whl-perf-stats" style="display:none;padding:0 14px 8px">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
                    <div class="overview-card">
                        <div class="overview-card-title">Net P&L</div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:${moneyColor(stats.net_pnl)}">
                            ${fmtMoney(stats.net_pnl)} <span style="font-size:13px;opacity:0.7">${headlineSign}${stats.net_pnl_pct.toFixed(2)}%</span>
                        </div>
                    </div>
                    <div class="overview-card">
                        <div class="overview-card-title">Max Drawdown</div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:var(--tv-red)">
                            ${stats.max_drawdown_pct.toFixed(2)}%
                        </div>
                    </div>
                    <div class="overview-card">
                        <div class="overview-card-title">Sharpe Ratio</div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600">
                            ${stats.sharpe_ratio != null ? stats.sharpe_ratio.toFixed(2) : '—'}
                        </div>
                    </div>
                    <div class="overview-card">
                        <div class="overview-card-title">Sortino Ratio</div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600">
                            ${stats.sortino_ratio != null ? stats.sortino_ratio.toFixed(2) : '—'}
                        </div>
                    </div>
                    <div class="overview-card">
                        <div class="overview-card-title">Annualized Yield</div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600;color:var(--tv-green)">
                            ${stats.annualized_yield_pct != null ? stats.annualized_yield_pct.toFixed(1) + '%' : '—'}
                        </div>
                    </div>
                    <div class="overview-card">
                        <div class="overview-card-title">Avg Weekly ROC</div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:600">
                            ${stats.avg_weekly_roc_pct != null ? stats.avg_weekly_roc_pct.toFixed(3) + '%' : '—'}
                        </div>
                    </div>
                </div>
            </div>`;
        }

        return `
        <div style="padding:10px 14px 4px">
            <div style="font-size:13px;font-weight:600;color:var(--tv-muted);margin-bottom:6px">Performance vs benchmark (SPY)</div>
            <div id="whl-chart" style="height:220px;width:100%"></div>
            <div style="display:flex;gap:16px;justify-content:center;padding:6px 0;font-size:12px;color:var(--tv-muted)">
                <span><span style="display:inline-block;width:12px;height:2px;background:#3b82f6;vertical-align:middle;margin-right:4px"></span>Portfolio</span>
                <span><span style="display:inline-block;width:12px;height:2px;background:#94a3b8;vertical-align:middle;margin-right:4px;border-top:1px dashed #94a3b8"></span>SPY</span>
            </div>
        </div>
        <div style="padding:0 14px;cursor:pointer" onclick="(function(e){var d=document.getElementById('whl-perf-stats');var c=e.currentTarget.querySelector('.whl-stats-chevron');if(d.style.display==='none'){d.style.display='block';c.style.transform='rotate(90deg)';}else{d.style.display='none';c.style.transform='rotate(0deg)';}})(event)">
            <div style="display:flex;align-items:center;gap:6px;padding:4px 0 8px">
                <span class="whl-stats-chevron" style="display:inline-block;font-size:10px;color:var(--tv-muted);transition:transform 0.15s;transform:rotate(0deg);line-height:1">▶</span>
                <span style="font-size:13px;color:var(--tv-muted)">YTD</span>
                <span style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;color:${headlineColor}">${headlineSign}${headlinePct.toFixed(2)}%</span>
            </div>
        </div>
        ${statsHtml}`;
    }

    function mountPerfChart(portfolio, spy) {
        const container = document.getElementById('whl-chart');
        if (!container || !window.LightweightCharts || !portfolio.length) return;
        const chart = window.LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 220,
            layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8' },
            grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
            timeScale: { borderColor: 'rgba(255,255,255,0.1)' },
            rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
        });
        const eqSeries = chart.addAreaSeries({
            lineColor: '#3b82f6', topColor: 'rgba(59,130,246,0.25)', bottomColor: 'rgba(59,130,246,0.0)', lineWidth: 2,
        });
        eqSeries.setData(portfolio.map(d => ({ time: d.date, value: d.pct })));
        if (spy.length) {
            const spySeries = chart.addLineSeries({ color: '#94a3b8', lineWidth: 1, lineStyle: 2 });
            spySeries.setData(spy.map(d => ({ time: d.date, value: d.pct })));
        }
        chart.timeScale().fitContent();
        const resizer = () => chart.applyOptions({ width: container.clientWidth });
        window.addEventListener('resize', resizer);
        container.__chartResizer = resizer;
    }
```

- [ ] **Step 2: Update the `render` function to include the performance section**

Replace the beginning of the `render` function's `el.innerHTML` assignment (the `el.innerHTML = \`` template string) to add the performance chart section at the top:

```javascript
    function render(el) {
        el.innerHTML = `
            <div class="scanner-header">
                <span class="scanner-title">Wheel Tracker</span>
                <span class="data-freshness-badge" id="whl-badge"></span>
            </div>
            <div id="whl-perf-section"><div class="list-message loading">Loading…</div></div>

            <div id="whl-stats"><div class="list-message loading">Loading…</div></div>

            <div class="section-header" style="padding-top:8px">
                <span class="section-title">Open Holdings</span>
            </div>
            <div id="whl-holdings"><div class="list-message loading">Loading…</div></div>

            <div class="section-header" style="padding-top:4px">
                <span class="section-title">Open Trades</span>
            </div>
            <div id="whl-trades"><div class="list-message loading">Loading…</div></div>

            <div class="section-header" style="padding-top:4px">
                <span class="section-title">Symbol Performance</span>
            </div>
            <div id="whl-perf" style="padding-bottom:16px"><div class="list-message loading">Loading…</div></div>
        `;

        const base = (window.MARKET_INTELLIGENCE_CONFIG?.apiBase) || '';
        Promise.all([
            fetch(`${base}/wheel/stats`).then(r => r.json()),
            fetch(`${base}/wheel/positions`).then(r => r.json()),
            fetch(`${base}/wheel/tickers`).then(r => r.json()),
            fetch(`${base}/wheel/equity-curve`).then(r => r.json()).catch(() => null),
        ]).then(([stats, posData, tickerData, curveData]) => {
            if (!document.getElementById('whl-stats')) return;
            const positions = posData.positions || [];
            document.getElementById('whl-stats').innerHTML    = renderStats(stats);
            document.getElementById('whl-holdings').innerHTML  = renderHoldings(positions);
            document.getElementById('whl-trades').innerHTML    = renderOpenTrades(positions);
            document.getElementById('whl-perf').innerHTML      = renderSymbolPerf(tickerData.tickers || []);

            const perfSection = document.getElementById('whl-perf-section');
            if (perfSection && curveData) {
                perfSection.innerHTML = renderPerfChart(curveData);
                mountPerfChart(curveData.portfolio_curve || [], curveData.spy_curve || []);
            } else if (perfSection) {
                perfSection.innerHTML = '';
            }

            const badge = document.getElementById('whl-badge');
            if (badge) { badge.className = 'data-freshness-badge fresh'; badge.textContent = 'Live'; }
        }).catch(err => {
            console.error('[WheelView]', err);
            const el = document.getElementById('whl-stats');
            if (el) el.innerHTML = `<div class="list-message">Failed to load — check API connection</div>`;
        });
    }
```

- [ ] **Step 3: Restart API container and verify in browser**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose up -d --no-build api
```

Then use Playwright MCP to navigate to `https://dev-mi.austin10berge.com/v2/`, click the Wheel Tracker tab, and take a screenshot. Verify:
- The performance chart section appears at the top (or shows "No equity curve data" if no curve has been built yet)
- Existing stats, holdings, trades, and symbol performance sections still render correctly below

- [ ] **Step 4: Trigger a curve rebuild and verify the chart renders**

Use Playwright or curl to `POST /api/wheel/rebuild-curve`, then reload the page and verify the chart shows two lines (portfolio + SPY).

```bash
curl -X POST https://dev-mi.austin10berge.com/api/wheel/rebuild-curve
```

Take a screenshot and verify the chart renders with both lines and the expandable stats section works.

- [ ] **Step 5: Commit**

```bash
git add src/web/v2/wheel.js
git commit -m "feat(wheel): add performance vs SPY chart with expandable stats panel"
```

---

### Task 6: Confirm deposit dates and end-to-end verification

**Files:**
- Modify: `src/wheel_tracker/equity_curve.py` — update `DEPOSIT_EVENTS` with confirmed dates

**Interfaces:**
- Consumes: Schwab MCP `get_transactions` (to find actual deposit dates)
- Produces: corrected deposit dates in `DEPOSIT_EVENTS`

- [ ] **Step 1: Query Schwab transaction history for deposit/transfer events**

Use the Schwab MCP to look up transfer transactions. Search for non-TRADE transactions (JOURNAL, TRANSFER types) in the date ranges around the known deposits:

```bash
# Use the schwab MCP get_transactions tool with appropriate date ranges
# Look for type != TRADE, especially JOURNAL or TRANSFER entries
# around Dec 2025 ($20K) and mid-2026 ($25K)
```

- [ ] **Step 2: Update DEPOSIT_EVENTS with confirmed dates**

Update the `DEPOSIT_EVENTS` list in `src/wheel_tracker/equity_curve.py` with the exact dates found from Schwab.

- [ ] **Step 3: Rebuild curve and verify**

```bash
curl -X POST https://dev-mi.austin10berge.com/api/wheel/rebuild-curve
```

Navigate to the wheel tracker page in Playwright, take a screenshot, and verify:
- Chart shows both portfolio and SPY lines for YTD
- The portfolio line doesn't show a fake jump at the deposit date
- Expandable stats section shows reasonable values
- Collapsed state shows "YTD +X.XX%"
- Clicking expands to show all 6 stat cards

- [ ] **Step 4: Commit**

```bash
git add src/wheel_tracker/equity_curve.py
git commit -m "fix(wheel): update equity curve deposit dates from Schwab history"
```
