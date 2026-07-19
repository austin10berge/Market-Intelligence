# Algo Detective — Real Delta Collection via Schwab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect real per-contract put delta (plus `bid`, `ask`, `open_interest`) nightly via Schwab for the narrow (all-time-prime) ticker universe, storing it in `detective_options`, so a future gate-search project can consider delta as a discriminating feature.

**Architecture:** One new module (`schwab_options.py`) that calls the already-running `schwab-mcp` service as a plain MCP client (no Claude involved), parses its compact text chain output, selects the near-the-money put closest to a 0.20 target delta per ticker, and upserts into the existing `detective_options` table (schema extended with 4 new columns). Wired into `main.py`'s existing `_run_algo_detective_steps` as one more independently non-fatal step, reusing the narrow-universe ticker list the existing options-snapshot step already computes.

**Tech Stack:** Python 3.12, `mcp` SDK (streamable-HTTP client), SQLite (existing `market_intelligence.db`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-19-algo-detective-schwab-delta-design.md`

## Global Constraints

- Python 3.12, no local virtualenv — run tests via `docker compose run --rm test python3 -m pytest tests/...`. This repo's test image bakes `src/`/`tests/` at build time — run `docker compose build test` before `docker compose run --rm test` if new files aren't picked up.
- A `PostToolUse` hook auto-runs ruff on every edited `.py` file, but prior work in this codebase found it doesn't always fully resolve every finding (ruff-format-fixable E501/E402 have survived a commit before) — explicitly run `ruff check` and `ruff format --check` on touched files before committing rather than assuming the hook caught everything.
- All new modules use `from __future__ import annotations`, per existing repo convention.
- Follow the existing non-fatal try/except-log-and-continue convention for every pipeline step in `main.py`.
- No historical delta backfill — real delta only, forward-collecting from ship date (confirmed in the design spec: Black-Scholes/rv20 approximation tested and rejected as unreliable).
- Scoped to the narrow universe only (every ticker that has ever been `is_prime=1`) — not the full tracked control universe.
- Target delta for contract selection: 0.20 (midpoint of mLabs' stated 0.15–0.30 range).
- The `mcp` Python SDK is already installed transitively (via `mcp-proxy`, confirmed version `1.28.1` in the pipeline image) but must be added as an explicit direct dependency in `pyproject.toml` since `schwab_options.py` imports it directly.

---

## File Structure

```
src/algo_detective/
├── store.py            (MODIFY — add delta/bid/ask/open_interest to
│                         _OPTIONS_COLUMNS, extend upsert_options_rows)
└── schwab_options.py    (NEW — _parse_put_chain(), _select_target_delta_contract(),
                           _fetch_chain_via_mcp(), fetch_delta_snapshot())

src/
└── main.py              (MODIFY — hoist narrow-universe lookup out of the
                           options-snapshot step into its own non-fatal
                           block shared by both consumers, add Step 8/8)

docker-compose.yml        (MODIFY — pipeline service depends_on schwab-mcp)

pyproject.toml            (MODIFY — add explicit `mcp` dependency)

tests/
├── test_algo_detective_store.py               (MODIFY — new-column
│                                                 migration + upsert tests)
├── test_algo_detective_schwab_options.py       (NEW — parsing, selection,
│                                                 orchestration tests)
└── test_main_pipeline_algo_detective_steps.py  (MODIFY — new step
                                                   ordering/isolation tests)
```

---

### Task 1: `detective_options` schema — delta/bid/ask/open_interest columns

**Files:**
- Modify: `src/algo_detective/store.py`
- Modify: `tests/test_algo_detective_store.py`

**Interfaces:**
- Produces: `detective_options` table has `delta REAL`, `bid REAL`, `ask REAL`, `open_interest INTEGER` columns after `ensure_tables()` runs.

- [ ] **Step 1: Write the failing test**

In `tests/test_algo_detective_store.py`, add this test right after `test_ensure_tables_creates_detective_options` (around line 224):

```python
def test_ensure_tables_adds_delta_bid_ask_open_interest_columns():
    ensure_tables()
    conn = sqlite3.connect(_tmp_db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(detective_options)").fetchall()}
    conn.close()
    assert {"delta", "bid", "ask", "open_interest"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py::test_ensure_tables_adds_delta_bid_ask_open_interest_columns -v`
Expected: FAIL — `assert {'delta', 'bid', 'ask', 'open_interest'} <= {...}` (missing columns)

- [ ] **Step 3: Add the columns to `_OPTIONS_COLUMNS`**

In `src/algo_detective/store.py`, `_OPTIONS_COLUMNS` currently reads (around line 132):

```python
_OPTIONS_COLUMNS = [
    ("pcr_vol", "REAL"),
    ("pcr_oi", "REAL"),
]
```

Replace with:

```python
_OPTIONS_COLUMNS = [
    ("pcr_vol", "REAL"),
    ("pcr_oi", "REAL"),
    ("delta", "REAL"),
    ("bid", "REAL"),
    ("ask", "REAL"),
    ("open_interest", "INTEGER"),
]
```

(`ensure_tables()`'s existing `for col, col_type in _OPTIONS_COLUMNS: ... ALTER TABLE detective_options ADD COLUMN ...` loop, already idempotent via its `except sqlite3.OperationalError: pass`, needs no other changes.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/store.py tests/test_algo_detective_store.py
git commit -m "feat(algo-detective): add delta/bid/ask/open_interest columns to detective_options"
```

---

### Task 2: `upsert_options_rows` — accept partial Schwab-only rows

**Files:**
- Modify: `src/algo_detective/store.py`
- Modify: `tests/test_algo_detective_store.py`

**Interfaces:**
- Consumes: the new columns from Task 1.
- Produces: `upsert_options_rows(rows: list[dict]) -> int` accepts rows containing only `date`/`ticker`/`delta`/`bid`/`ask`/`open_interest` (no Alpaca-side keys) without raising, and vice versa — both sets of columns merge via `COALESCE` on conflict, matching the existing `best_iv`/`pcr_vol` behavior exactly.

- [ ] **Step 1: Write the failing test**

In `tests/test_algo_detective_store.py`, add these two tests right after `test_upsert_options_coalesces_null_iv_without_clobbering_existing` (check the existing file for that function's exact end line, then insert after it — before `test_upsert_options_rows_idempotent`):

```python
def test_upsert_options_rows_accepts_schwab_only_row():
    """A Schwab-sourced row supplies only delta/bid/ask/open_interest — the
    Alpaca-only columns (best_iv, best_volume, occ_symbol, pcr_vol, pcr_oi)
    must default to None rather than raising a binding error."""
    ensure_tables()
    row = {
        "date": "2026-07-20",
        "ticker": "HOOD",
        "delta": -0.223,
        "bid": 1.85,
        "ask": 1.95,
        "open_interest": 8325,
    }
    count = upsert_options_rows([row])
    assert count == 1

    stored = get_options_index()[("2026-07-20", "HOOD")]
    assert stored["delta"] == -0.223
    assert stored["bid"] == 1.85
    assert stored["ask"] == 1.95
    assert stored["open_interest"] == 8325
    assert stored["best_iv"] is None


def test_upsert_options_coalesces_delta_without_clobbering_iv():
    """Writing delta/bid/ask/open_interest for a (date, ticker) that already
    has an Alpaca-sourced best_iv/pcr_vol row must not erase those fields,
    and vice versa — the two sources merge into one row per (date, ticker)."""
    ensure_tables()
    ticker = "AAPL"
    upsert_options_rows([_make_options_row(ticker=ticker)])  # Alpaca-style row
    upsert_options_rows([{
        "date": "2026-06-18",
        "ticker": ticker,
        "delta": -0.21,
        "bid": 2.5,
        "ask": 2.6,
        "open_interest": 900,
    }])

    stored = get_options_index()[("2026-06-18", ticker)]
    assert stored["best_iv"] == 0.42
    assert stored["pcr_vol"] == 1.15
    assert stored["delta"] == -0.21
    assert stored["bid"] == 2.5
    assert stored["ask"] == 2.6
    assert stored["open_interest"] == 900
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py::test_upsert_options_rows_accepts_schwab_only_row -v`
Expected: FAIL — `sqlite3.ProgrammingError: You did not supply a value for binding parameter :best_iv` (or similar `KeyError`)

- [ ] **Step 3: Extend `upsert_options_rows`**

In `src/algo_detective/store.py`, `upsert_options_rows` currently reads (around line 270):

```python
def upsert_options_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = _get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO detective_options (date, ticker, best_iv, best_volume, occ_symbol, pcr_vol, pcr_oi)
            VALUES (:date, :ticker, :best_iv, :best_volume, :occ_symbol,
                    :pcr_vol, :pcr_oi)
            ON CONFLICT(date, ticker) DO UPDATE SET
                best_iv = COALESCE(excluded.best_iv, detective_options.best_iv),
                best_volume = COALESCE(excluded.best_volume, detective_options.best_volume),
                occ_symbol = COALESCE(excluded.occ_symbol, detective_options.occ_symbol),
                pcr_vol = COALESCE(excluded.pcr_vol, detective_options.pcr_vol),
                pcr_oi  = COALESCE(excluded.pcr_oi,  detective_options.pcr_oi)
            """,
            [{**r, "pcr_vol": r.get("pcr_vol"), "pcr_oi": r.get("pcr_oi")} for r in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()
```

Replace the whole function with:

```python
def upsert_options_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = _get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO detective_options
                (date, ticker, best_iv, best_volume, occ_symbol, pcr_vol, pcr_oi,
                 delta, bid, ask, open_interest)
            VALUES (:date, :ticker, :best_iv, :best_volume, :occ_symbol,
                    :pcr_vol, :pcr_oi, :delta, :bid, :ask, :open_interest)
            ON CONFLICT(date, ticker) DO UPDATE SET
                best_iv = COALESCE(excluded.best_iv, detective_options.best_iv),
                best_volume = COALESCE(excluded.best_volume, detective_options.best_volume),
                occ_symbol = COALESCE(excluded.occ_symbol, detective_options.occ_symbol),
                pcr_vol = COALESCE(excluded.pcr_vol, detective_options.pcr_vol),
                pcr_oi  = COALESCE(excluded.pcr_oi,  detective_options.pcr_oi),
                delta = COALESCE(excluded.delta, detective_options.delta),
                bid = COALESCE(excluded.bid, detective_options.bid),
                ask = COALESCE(excluded.ask, detective_options.ask),
                open_interest = COALESCE(excluded.open_interest, detective_options.open_interest)
            """,
            [
                {
                    "date": r["date"],
                    "ticker": r["ticker"],
                    "best_iv": r.get("best_iv"),
                    "best_volume": r.get("best_volume"),
                    "occ_symbol": r.get("occ_symbol"),
                    "pcr_vol": r.get("pcr_vol"),
                    "pcr_oi": r.get("pcr_oi"),
                    "delta": r.get("delta"),
                    "bid": r.get("bid"),
                    "ask": r.get("ask"),
                    "open_interest": r.get("open_interest"),
                }
                for r in rows
            ],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_store.py -v`
Expected: PASS (all tests in the file — this change is a strict generalization of the existing defensive-default pattern, so no prior test should break)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/store.py tests/test_algo_detective_store.py
git commit -m "feat(algo-detective): let upsert_options_rows accept partial Schwab-only rows"
```

---

### Task 3: Parse Schwab's compact chain text + pick the target-delta contract

**Files:**
- Create: `src/algo_detective/schwab_options.py`
- Test: `tests/test_algo_detective_schwab_options.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure text-parsing + selection, no DB, no network).
- Produces: `_parse_put_chain(raw_text: str) -> list[dict]` — each dict `{"strike": float, "bid": float, "ask": float, "delta": float, "open_interest": int}`. `_select_target_delta_contract(contracts: list[dict], target_delta: float = 0.20) -> dict | None`.

**Real payload format (captured live 2026-07-19 via `mcp__schwab__get_option_chain`, not AI-summarized):**

`schwab-mcp`'s `get_option_chain` tool (non-verbose/compact mode, the default) returns a single text block. It is *not* raw JSON — it's the `schwab-mcp` server's own custom compact display formatting (confirmed by fetching the same tool in `verbose=True` mode, which still returned this same custom formatting, just with more per-contract fields — there is no JSON mode available). The section relevant here, `putExpDateMap`, pairs a per-strike header line `"STRIKE"[N,]{field1,field2,...}:` with a single CSV value line directly below it, once per strike per expiration:

```
symbol: HOOD
status: SUCCESS
...
underlyingPrice: 99.96
...
putExpDateMap:
  "2026-07-24:5":
    "93.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      1.33,1.41,1.37,1.37,77,19,-0.223,0.029,-0.222,0.041,-0.004,370,"2026-07-24T20:00:00.000+00:00",5,W,false
    "94.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      1.59,1.67,1.62,1.63,21,34,-0.255,0.032,-0.24,0.044,-0.005,155,"2026-07-24T20:00:00.000+00:00",5,W,false
  "2026-07-31:12":
    "93.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      3.3,4.0,3.59,3.65,419,14,-0.306,0.021,-0.218,0.068,-0.013,2012,"2026-07-31T20:00:00.000+00:00",12,W,false
```

The field-name list in the header (`{bid,ask,...}`) has been consistent across every ticker and call observed during design — but the parser must read the field order from each header rather than hardcoding column positions, since it's regenerated per response.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_schwab_options.py`:

```python
"""Tests for src/algo_detective/schwab_options.py — parses schwab-mcp's
compact get_option_chain text output and selects the put contract closest
to a target delta, for nightly narrow-universe delta collection (Step 8 of
the pipeline). HTML/text snippets below are trimmed excerpts of a real
schwab-mcp response captured 2026-07-19, not a synthetic mockup.
See docs/superpowers/specs/2026-07-19-algo-detective-schwab-delta-design.md.
"""
from __future__ import annotations

from src.algo_detective.schwab_options import (
    _parse_put_chain,
    _select_target_delta_contract,
)

_HOOD_CHAIN_FIXTURE = """symbol: HOOD
status: SUCCESS
strategy: SINGLE
interval: 0
isDelayed: false
isIndex: false
interestRate: 3.707
underlyingPrice: 99.96
volatility: 29.0
daysToExpiration: 5.0
dividendYield: 0
numberOfContracts: 30
assetMainType: EQUITY
assetSubType: COE
isChainTruncated: false
ethOptionEligible: true
hasBinaryOptions: false
putExpDateMap:
  "2026-07-24:5":
    "93.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      1.33,1.41,1.37,1.37,77,19,-0.223,0.029,-0.222,0.041,-0.004,370,"2026-07-24T20:00:00.000+00:00",5,W,false
    "94.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      1.59,1.67,1.62,1.63,21,34,-0.255,0.032,-0.24,0.044,-0.005,155,"2026-07-24T20:00:00.000+00:00",5,W,false
    "95.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      1.85,1.95,1.9,1.9,92,24,-0.288,0.034,-0.253,0.047,-0.006,8325,"2026-07-24T20:00:00.000+00:00",5,W,false
  "2026-07-31:12":
    "93.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      3.3,4.0,3.59,3.65,419,14,-0.306,0.021,-0.218,0.068,-0.013,2012,"2026-07-31T20:00:00.000+00:00",12,W,false
    "94.0"[1,]{bid,ask,last,mark,bidSize,askSize,delta,gamma,theta,vega,rho,openInterest,expirationDate,daysToExpiration,expirationType,inTheMoney}:
      4.05,4.45,4.25,4.25,46,154,-0.332,0.021,-0.234,0.071,-0.014,275,"2026-07-31T20:00:00.000+00:00",12,W,false
"""

_EMPTY_CHAIN_FIXTURE = """symbol: XXXX
status: SUCCESS
numberOfContracts: 0
putExpDateMap: {}
"""


class TestParsePutChain:
    def test_parses_all_contracts_across_expirations(self):
        contracts = _parse_put_chain(_HOOD_CHAIN_FIXTURE)
        assert len(contracts) == 5

        first = contracts[0]
        assert first["strike"] == 93.0
        assert first["delta"] == -0.223
        assert first["bid"] == 1.33
        assert first["ask"] == 1.41
        assert first["open_interest"] == 370

    def test_returns_empty_list_for_chain_with_no_contracts(self):
        assert _parse_put_chain(_EMPTY_CHAIN_FIXTURE) == []


class TestSelectTargetDeltaContract:
    def test_picks_contract_closest_to_target_delta(self):
        contracts = _parse_put_chain(_HOOD_CHAIN_FIXTURE)
        selected = _select_target_delta_contract(contracts, target_delta=0.20)

        assert selected["strike"] == 93.0
        assert selected["delta"] == -0.223
        assert selected["bid"] == 1.33
        assert selected["ask"] == 1.41
        assert selected["open_interest"] == 370

    def test_returns_none_for_empty_contract_list(self):
        assert _select_target_delta_contract([], target_delta=0.20) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_schwab_options.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo_detective.schwab_options'`

- [ ] **Step 3: Implement the parser and selector**

Create `src/algo_detective/schwab_options.py`:

```python
"""Collects real per-contract put delta (plus bid/ask/open_interest) for
the narrow (all-time-prime) ticker universe via schwab-mcp, the nightly
pipeline's Step 8. schwab-mcp's get_option_chain tool returns its own
compact text formatting (not raw JSON) — see the module-level parser
functions' docstrings for the exact shape, captured live during design.
See docs/superpowers/specs/2026-07-19-algo-detective-schwab-delta-design.md.
"""
from __future__ import annotations

import csv
import logging
import re

logger = logging.getLogger(__name__)

_STRIKE_HEADER_RE = re.compile(
    r'^\s*"([\d.]+)"\[\d+,?\]\{([^}]+)\}:\s*\n\s*(.+)$',
    re.MULTILINE,
)


def _parse_put_chain(raw_text: str) -> list[dict]:
    """Parse schwab-mcp's compact get_option_chain text output into a list
    of put contract dicts: {strike, bid, ask, delta, open_interest}.

    The compact format pairs a per-strike header line
    '"STRIKE"[N,]{field1,field2,...}:' with a CSV value line directly below
    it — schwab-mcp's own display formatting, not raw Schwab JSON. Field
    order is read from each header rather than hardcoded, since it's
    regenerated per response.
    """
    contracts = []
    for match in _STRIKE_HEADER_RE.finditer(raw_text):
        strike_str, field_names_csv, value_line = match.groups()
        field_names = [f.strip() for f in field_names_csv.split(",")]
        values = next(csv.reader([value_line.strip()]))
        field_map = dict(zip(field_names, values))
        try:
            contracts.append({
                "strike": float(strike_str),
                "bid": float(field_map["bid"]),
                "ask": float(field_map["ask"]),
                "delta": float(field_map["delta"]),
                "open_interest": int(field_map["openInterest"]),
            })
        except (KeyError, ValueError):
            logger.warning("Skipping unparseable contract row: %r", value_line)
            continue
    return contracts


def _select_target_delta_contract(
    contracts: list[dict], target_delta: float = 0.20
) -> dict | None:
    """Return the put contract whose delta magnitude is closest to
    target_delta (mLabs' stated CSP range is 0.15-0.30 puts; 0.20 is the
    midpoint). Returns None if contracts is empty."""
    if not contracts:
        return None
    return min(contracts, key=lambda c: abs(abs(c["delta"]) - target_delta))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_schwab_options.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/schwab_options.py tests/test_algo_detective_schwab_options.py
git commit -m "feat(algo-detective): parse Schwab put chains and select target-delta contract"
```

---

### Task 4: Fetch via schwab-mcp + orchestrate the nightly snapshot

**Files:**
- Modify: `src/algo_detective/schwab_options.py`
- Modify: `tests/test_algo_detective_schwab_options.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `_parse_put_chain`, `_select_target_delta_contract` (Task 3); `upsert_options_rows` (Task 2); `_next_fridays` from `.options_chain` (existing, unmodified).
- Produces: `fetch_delta_snapshot(tickers: list[str], scan_date_str: str) -> int` — Step 8 of the nightly pipeline. Returns count of rows written.

**Real `mcp` SDK API (verified live 2026-07-19 against the actual installed version, `mcp==1.28.1`, already present transitively via `mcp-proxy`):**

```
streamablehttp_client(url: str, ...) -> async context manager yielding (read_stream, write_stream, get_session_id)
ClientSession(read_stream, write_stream) -> async context manager
ClientSession.initialize() -> awaitable
ClientSession.call_tool(name: str, arguments: dict) -> awaitable returning CallToolResult
CallToolResult has fields: meta, content, structuredContent, isError
TextContent (each item in .content) has fields: type, text, annotations, meta
```

- [ ] **Step 1: Write the failing test**

Add to `pyproject.toml`'s `dependencies` list (after the existing `"mcp-proxy>=0.12,<1",` line):

```toml
    "mcp>=1.28,<2",
```

Add to `tests/test_algo_detective_schwab_options.py`, extending the import line at the top:

```python
from unittest.mock import patch

from src.algo_detective.schwab_options import (
    _parse_put_chain,
    _select_target_delta_contract,
    fetch_delta_snapshot,
)
```

(add `from unittest.mock import patch` above the existing `from src.algo_detective.schwab_options import (...)` line, and add `fetch_delta_snapshot` to that import tuple)

Append to the end of `tests/test_algo_detective_schwab_options.py`:

```python
class TestFetchDeltaSnapshot:
    @patch("src.algo_detective.schwab_options.upsert_options_rows")
    @patch("src.algo_detective.schwab_options._fetch_chain_via_mcp")
    def test_writes_selected_contract_per_ticker(self, mock_fetch, mock_upsert):
        mock_fetch.return_value = _HOOD_CHAIN_FIXTURE
        mock_upsert.return_value = 1

        written = fetch_delta_snapshot(["HOOD"], "2026-07-19")

        assert written == 1
        rows = mock_upsert.call_args.args[0]
        assert rows == [{
            "date": "2026-07-19",
            "ticker": "HOOD",
            "delta": -0.223,
            "bid": 1.33,
            "ask": 1.41,
            "open_interest": 370,
        }]

    @patch("src.algo_detective.schwab_options.upsert_options_rows")
    @patch("src.algo_detective.schwab_options._fetch_chain_via_mcp")
    def test_one_ticker_failure_does_not_block_others(self, mock_fetch, mock_upsert):
        mock_fetch.side_effect = [RuntimeError("boom"), _HOOD_CHAIN_FIXTURE]
        mock_upsert.return_value = 1

        written = fetch_delta_snapshot(["BADTICKER", "HOOD"], "2026-07-19")

        assert written == 1
        rows = mock_upsert.call_args.args[0]
        assert len(rows) == 1
        assert rows[0]["ticker"] == "HOOD"

    @patch("src.algo_detective.schwab_options.upsert_options_rows")
    @patch("src.algo_detective.schwab_options._fetch_chain_via_mcp")
    def test_returns_zero_and_skips_upsert_when_no_contracts_selected(
        self, mock_fetch, mock_upsert
    ):
        mock_fetch.return_value = _EMPTY_CHAIN_FIXTURE

        written = fetch_delta_snapshot(["XXXX"], "2026-07-19")

        assert written == 0
        mock_upsert.assert_not_called()

    @patch("src.algo_detective.schwab_options._fetch_chain_via_mcp")
    def test_passes_next_two_fridays_as_the_date_window(self, mock_fetch):
        mock_fetch.return_value = _EMPTY_CHAIN_FIXTURE

        fetch_delta_snapshot(["HOOD"], "2026-07-19")  # a Sunday

        mock_fetch.assert_called_once_with("HOOD", "2026-07-24", "2026-07-31")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_schwab_options.py::TestFetchDeltaSnapshot -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_delta_snapshot'`

- [ ] **Step 3: Implement the MCP fetch boundary and orchestrator**

In `src/algo_detective/schwab_options.py`, add these imports at the top (after the existing `import re`):

```python
import asyncio
from datetime import date, timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .options_chain import _next_fridays
from .store import upsert_options_rows
```

Append to the end of the file:

```python
_SCHWAB_MCP_URL = "http://schwab-mcp:8002/mcp"


async def _fetch_chain_via_mcp_async(ticker: str, from_date_str: str, to_date_str: str) -> str:
    async with streamablehttp_client(_SCHWAB_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_option_chain",
                {
                    "symbol": ticker,
                    "contract_type": "PUT",
                    "from_date": from_date_str,
                    "to_date": to_date_str,
                },
            )
            if result.isError:
                raise RuntimeError(
                    f"schwab-mcp get_option_chain error for {ticker}: {result.content}"
                )
            return result.content[0].text


def _fetch_chain_via_mcp(ticker: str, from_date_str: str, to_date_str: str) -> str:
    """Sync boundary around the async MCP tool call — bridges this
    module's asyncio.to_thread-based calling convention (see main.py) to
    the MCP SDK's async-only client API. This is the network boundary;
    tests patch this function directly rather than simulating the MCP
    session handshake (no automated test safely exercises the real live
    schwab-mcp service — see the design spec's Testing section)."""
    return asyncio.run(_fetch_chain_via_mcp_async(ticker, from_date_str, to_date_str))


def fetch_delta_snapshot(tickers: list[str], scan_date_str: str) -> int:
    """Step 8 of the nightly pipeline: fetch real put delta/bid/ask/
    open_interest for tickers via Schwab (schwab-mcp), upsert into
    detective_options. Returns the number of rows written."""
    scan_date = date.fromisoformat(scan_date_str)
    fridays = _next_fridays(scan_date + timedelta(days=1), n=2)
    from_date_str, to_date_str = fridays[0].isoformat(), fridays[-1].isoformat()

    rows = []
    for ticker in tickers:
        try:
            raw = _fetch_chain_via_mcp(ticker, from_date_str, to_date_str)
            contracts = _parse_put_chain(raw)
        except Exception:
            logger.warning(
                "Failed to fetch option chain for %s on %s", ticker, scan_date_str, exc_info=True
            )
            continue

        selected = _select_target_delta_contract(contracts)
        if selected is None:
            logger.warning("No usable put contracts for %s on %s", ticker, scan_date_str)
            continue

        rows.append({
            "date": scan_date_str,
            "ticker": ticker,
            "delta": selected["delta"],
            "bid": selected["bid"],
            "ask": selected["ask"],
            "open_interest": selected["open_interest"],
        })

    if not rows:
        return 0
    written = upsert_options_rows(rows)
    logger.info("Delta snapshot %s: %d/%d tickers written", scan_date_str, written, len(tickers))
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_schwab_options.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/algo_detective/schwab_options.py tests/test_algo_detective_schwab_options.py
git commit -m "feat(algo-detective): fetch put chains via schwab-mcp and orchestrate delta snapshot"
```

---

### Task 5: Wire Step 8 into `main.py` + `schwab-mcp` compose dependency

**Files:**
- Modify: `src/main.py`
- Modify: `docker-compose.yml`
- Modify: `tests/test_main_pipeline_algo_detective_steps.py`

**Interfaces:**
- Consumes: `fetch_delta_snapshot` (Task 4).

**Context on the existing code being modified:** `main.py`'s `_run_algo_detective_steps(today)` currently has the options-snapshot step (labeled "Step 5/7") compute its own `_prime` ticker list inline via a fresh `get_all_features()` call, right before calling `fetch_snapshot_pcr`. The new Step 8 needs that *same* ticker list. Calling `get_all_features()` a second time from a second inline block would double a full-table scan over `detective_features` (134,575+ rows and growing) every night for no benefit — so this task hoists that ticker-list computation into its own non-fatal block, shared by both the existing options-snapshot step and the new Step 8. The other three existing tests (`test_step5_calls_ensure_tables_before_get_all_features`, `test_step6_runs_before_step7`, `test_step6_failure_does_not_block_step7`, `test_ensure_tables_failure_does_not_block_label_and_control_sync`) all mock `get_all_features` to return `[]`, so their assertions are unaffected by this restructuring — none of them exercise the narrow-universe-dependent steps, and `get_all_features` is still called exactly once in every one of them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_main_pipeline_algo_detective_steps.py`:

```python
@pytest.mark.asyncio
async def test_step8_runs_after_step5_with_narrow_universe_tickers():
    call_order = []
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch(
            "src.algo_detective.store.get_all_features",
            return_value=[
                {"ticker": "HOOD", "is_prime": 1},
                {"ticker": "AAPL", "is_prime": 0},
            ],
        ),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0),
        patch("src.algo_detective.control_sync.sync_control_universe", return_value=0),
        patch(
            "src.algo_detective.options_chain.fetch_snapshot_pcr",
            side_effect=lambda tickers, d: call_order.append(("options_snapshot", tickers)) or 0,
        ),
        patch(
            "src.algo_detective.schwab_options.fetch_delta_snapshot",
            side_effect=lambda tickers, d: call_order.append(("delta_snapshot", tickers)) or 0,
        ),
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())

        assert [name for name, _ in call_order] == ["options_snapshot", "delta_snapshot"]
        assert call_order[0][1] == ["HOOD"]
        assert call_order[1][1] == ["HOOD"]


@pytest.mark.asyncio
async def test_step8_failure_is_non_fatal():
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch(
            "src.algo_detective.store.get_all_features",
            return_value=[{"ticker": "HOOD", "is_prime": 1}],
        ),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0),
        patch("src.algo_detective.control_sync.sync_control_universe", return_value=0),
        patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0),
        patch(
            "src.algo_detective.schwab_options.fetch_delta_snapshot",
            side_effect=RuntimeError("boom"),
        ) as mock_delta,
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_delta.assert_called_once()


@pytest.mark.asyncio
async def test_step5_failure_does_not_block_step8():
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch(
            "src.algo_detective.store.get_all_features",
            return_value=[{"ticker": "HOOD", "is_prime": 1}],
        ),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0),
        patch("src.algo_detective.control_sync.sync_control_universe", return_value=0),
        patch(
            "src.algo_detective.options_chain.fetch_snapshot_pcr",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "src.algo_detective.schwab_options.fetch_delta_snapshot", return_value=0
        ) as mock_delta,
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_delta.assert_called_once()


@pytest.mark.asyncio
async def test_narrow_universe_lookup_failure_does_not_block_label_and_control_sync():
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch("src.algo_detective.store.get_all_features", side_effect=RuntimeError("boom")),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0) as mock_label,
        patch(
            "src.algo_detective.control_sync.sync_control_universe", return_value=0
        ) as mock_control,
        patch("src.algo_detective.options_chain.fetch_snapshot_pcr") as mock_snapshot,
        patch("src.algo_detective.schwab_options.fetch_delta_snapshot") as mock_delta,
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_label.assert_called_once()
        mock_control.assert_called_once()
        mock_snapshot.assert_not_called()  # narrow_universe stayed [] after the failure
        mock_delta.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_main_pipeline_algo_detective_steps.py::test_step8_runs_after_step5_with_narrow_universe_tickers -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo_detective.schwab_options'` inside the patch target resolution, or an assertion failure showing `call_order == []` (Step 8 doesn't exist yet)

- [ ] **Step 3: Restructure `_run_algo_detective_steps` and add Step 8**

In `src/main.py`, `_run_algo_detective_steps` currently reads (lines 316-359):

```python
async def _run_algo_detective_steps(today) -> None:
    """Steps 5-7 of the nightly pipeline: sync mLabs trade labels, sync
    control-universe features, then refresh live options IV snapshots for
    the (now up to date) prime-ticker whitelist. All three are non-fatal —
    a failure in one logs and lets the rest of the pipeline continue."""
    from .algo_detective.store import ensure_tables

    try:
        ensure_tables()
    except Exception as _exc:
        logger.warning("Algo-detective ensure_tables failed (non-fatal): %s", _exc)

    logger.info("Step 6/7: Syncing mLabs trade labels...")
    try:
        from .algo_detective.label_sync import sync_new_labels

        written = await asyncio.to_thread(sync_new_labels)
        logger.info("Label sync: %d new prime rows written", written)
    except Exception as _exc:
        logger.warning("Algo-detective label sync failed (non-fatal): %s", _exc)

    logger.info("Step 7/7: Syncing control-universe features...")
    try:
        from .algo_detective.control_sync import sync_control_universe

        written = await asyncio.to_thread(sync_control_universe, today.isoformat())
        logger.info("Control sync: %d rows written", written)
    except Exception as _exc:
        logger.warning("Algo-detective control sync failed (non-fatal): %s", _exc)

    logger.info("Step 5/7: Collecting algo-detective options snapshot...")
    try:
        from .algo_detective.options_chain import fetch_snapshot_pcr
        from .algo_detective.store import get_all_features as _get_detective_features

        _features = await asyncio.to_thread(_get_detective_features)
        _prime = sorted({f["ticker"] for f in _features if f["is_prime"] == 1})
        if _prime:
            stored = await asyncio.to_thread(fetch_snapshot_pcr, _prime, today.isoformat())
            logger.info(
                "Options snapshot: %d rows stored for %d prime tickers", stored, len(_prime)
            )
    except Exception as _exc:
        logger.warning("Algo-detective options snapshot failed (non-fatal): %s", _exc)
```

Replace the whole function with:

```python
async def _run_algo_detective_steps(today) -> None:
    """Steps 5-8 of the nightly pipeline: sync mLabs trade labels, sync
    control-universe features, look up the narrow (all-time-prime) ticker
    universe once for the two steps that need it, then refresh live options
    IV snapshots and real put delta for that whitelist. All steps are
    non-fatal — a failure in one logs and lets the rest of the pipeline
    continue."""
    from .algo_detective.store import ensure_tables

    try:
        ensure_tables()
    except Exception as _exc:
        logger.warning("Algo-detective ensure_tables failed (non-fatal): %s", _exc)

    logger.info("Step 6/8: Syncing mLabs trade labels...")
    try:
        from .algo_detective.label_sync import sync_new_labels

        written = await asyncio.to_thread(sync_new_labels)
        logger.info("Label sync: %d new prime rows written", written)
    except Exception as _exc:
        logger.warning("Algo-detective label sync failed (non-fatal): %s", _exc)

    logger.info("Step 7/8: Syncing control-universe features...")
    try:
        from .algo_detective.control_sync import sync_control_universe

        written = await asyncio.to_thread(sync_control_universe, today.isoformat())
        logger.info("Control sync: %d rows written", written)
    except Exception as _exc:
        logger.warning("Algo-detective control sync failed (non-fatal): %s", _exc)

    _narrow_universe: list[str] = []
    try:
        from .algo_detective.store import get_all_features as _get_detective_features

        _features = await asyncio.to_thread(_get_detective_features)
        _narrow_universe = sorted({f["ticker"] for f in _features if f["is_prime"] == 1})
    except Exception as _exc:
        logger.warning("Algo-detective narrow-universe lookup failed (non-fatal): %s", _exc)

    logger.info("Step 5/8: Collecting algo-detective options snapshot...")
    try:
        from .algo_detective.options_chain import fetch_snapshot_pcr

        if _narrow_universe:
            stored = await asyncio.to_thread(
                fetch_snapshot_pcr, _narrow_universe, today.isoformat()
            )
            logger.info(
                "Options snapshot: %d rows stored for %d prime tickers",
                stored,
                len(_narrow_universe),
            )
    except Exception as _exc:
        logger.warning("Algo-detective options snapshot failed (non-fatal): %s", _exc)

    logger.info("Step 8/8: Collecting Schwab put delta snapshot...")
    try:
        from .algo_detective.schwab_options import fetch_delta_snapshot

        if _narrow_universe:
            delta_written = await asyncio.to_thread(
                fetch_delta_snapshot, _narrow_universe, today.isoformat()
            )
            logger.info(
                "Delta snapshot: %d rows stored for %d narrow-universe tickers",
                delta_written,
                len(_narrow_universe),
            )
    except Exception as _exc:
        logger.warning("Algo-detective delta snapshot failed (non-fatal): %s", _exc)
```

In `docker-compose.yml`, the `pipeline` service currently reads:

```yaml
  pipeline:
    build:
      context: .
      target: pipeline
    container_name: market-intelligence-pipeline
    env_file: .env
    environment:
      REDIS_URL: redis://redis:6379/0
    volumes:
      - ./data:/app/data
      - ~/.claude:/root/.claude
      - ~/.claude.json:/root/.claude.json
    depends_on:
      redis:
        condition: service_healthy
    restart: "no"
    profiles:
      - pipeline
```

Replace the `depends_on` block with:

```yaml
    depends_on:
      redis:
        condition: service_healthy
      schwab-mcp:
        condition: service_healthy
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_main_pipeline_algo_detective_steps.py -v`
Expected: PASS (all 7 tests — the 4 pre-existing ones plus the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/main.py docker-compose.yml tests/test_main_pipeline_algo_detective_steps.py
git commit -m "feat(main): wire Schwab delta snapshot as pipeline Step 8, share narrow-universe lookup"
```

---

### Task 6: Manual verification against the real dev stack

No automated test can safely exercise the real live `schwab-mcp` service (per the design spec's Testing section). This task is a live, one-time verification — not a code change.

- [ ] **Step 1: Run the full test suite for regressions**

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -v`
Expected: PASS on all pre-existing tests plus every new test from Tasks 1-5; the same pre-existing, unrelated failures as before this plan (`test_algo_detective_options_chain`, `test_csp_scanner_integration` x2, `test_trading_calendar` x3) — no new failures.

- [ ] **Step 2: Rebuild the pipeline image**

Run: `docker compose build pipeline`
Expected: builds successfully with the new `mcp` dependency installed.

- [ ] **Step 3: Bring up `schwab-mcp` and confirm it's healthy**

Run: `docker compose up -d schwab-mcp`
Run: `docker compose ps schwab-mcp`
Expected: status `healthy` (per its existing healthcheck).

- [ ] **Step 4: Run a live delta snapshot against a handful of real narrow-universe tickers**

Run:
```bash
docker compose --profile pipeline run --rm pipeline python3 -c "
from src.algo_detective.schwab_options import fetch_delta_snapshot
from src.algo_detective.store import ensure_tables, get_options_index
import datetime
ensure_tables()
today = datetime.date.today().isoformat()
written = fetch_delta_snapshot(['HOOD', 'AAPL', 'NVDA'], today)
print(f'written: {written}')
idx = get_options_index()
for t in ['HOOD', 'AAPL', 'NVDA']:
    print(t, idx.get((today, t)))
"
```
Expected: `written: 3` (or fewer, if the market is closed and a ticker has no near-term liquid puts — check the logged warnings for why), and each printed row shows real `delta` (a negative float roughly in [-0.35, -0.10]), `bid`, `ask`, `open_interest` populated, with `best_iv`/`pcr_vol`/etc. either `None` (if no prior Alpaca snapshot exists for today) or preserved from an earlier run (confirming the `COALESCE` merge works against real data, not just the unit tests).

- [ ] **Step 5: Confirm no regressions in the full pipeline run**

Run: `docker compose --profile pipeline run --rm pipeline`
Expected: completes without error; log output includes `Step 8/8: Collecting Schwab put delta snapshot...` followed by a `Delta snapshot: N rows stored for M narrow-universe tickers` line, with no unhandled exception traceback.

---

## Post-Implementation

Update `docs/superpowers/specs/2026-07-19-algo-detective-schwab-delta-design.md`'s Background section is not required (the spec's job is done once this plan is implemented), but note in the SDD progress ledger (or equivalent) that this data is now available for the deferred gate-search project (Approach A: greedy stepwise gate search, with Approach B tree-based interaction discovery as an optional second pass) discussed during brainstorming, which was the original motivation for this work.
