# Value Screen Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `gross_margin` and `interest_coverage` as two new optional filters on the CSP scanner, plus a one-click "Reddit value screen" preset that sets all 4 gates (interest coverage >4.0x, gross margin >40%, revenue growth >10%, PEG <1.5x) at once.

**Architecture:** Extend the existing fundamentals pipeline end-to-end — new SQLite columns → new yfinance fetch fields in the nightly refresh job → new threshold checks in the scanner's fundamental filter → new query params on the API → new filter controls + preset button on both the legacy and v2 scanner UIs.

**Tech Stack:** Python 3.12 / FastAPI / SQLite (`src/market_data/store.py`) / yfinance / vanilla JS frontend. Run tests via `docker compose run --rm test python3 -m pytest ...` — there is no local virtualenv.

## Global Constraints

- Interest coverage ratio > 4.0x, gross margin > 40%, revenue growth > 10%, PEG < 1.5x are the post's exact threshold values (see spec).
- Debt-free companies (no `InterestExpense` reported) must store `interest_coverage` as `None`, not an invented "infinite" sentinel — the existing "missing data → gate skipped" convention already makes them pass the solvency gate correctly.
- `gross_margin` comes from the same lightweight `yf.Ticker().info` call already used for every other fundamental — no new network call.
- `interest_coverage` requires a second, heavier per-ticker call (`get_income_stmt`) — must not run unthrottled across the ~500-600 ticker universe.
- Out of scope: the 14-day sandbox timer, the AI qualitative reasoning layer.
- Follow existing code conventions exactly (see each task's "mirrors" reference) — this is an additive feature in a codebase with established patterns, not a redesign.

---

## Task 1: Store schema — `gross_margin` / `interest_coverage` columns

**Files:**
- Modify: `src/market_data/store.py:44-83` (DDL + `_NEW_FUNDAMENTAL_COLUMNS`), `store.py:301-398` (`bulk_upsert_fundamentals`, `get_all_fundamentals`, `get_fundamentals_for_tickers`)
- Test: `tests/test_market_data_store.py`

**Interfaces:**
- Produces: `universe_fundamentals` table gains nullable `gross_margin REAL` and `interest_coverage REAL` columns. `bulk_upsert_fundamentals(rows)` accepts optional `gross_margin`/`interest_coverage` keys in each row dict. `get_all_fundamentals()` / `get_fundamentals_for_tickers()` return dicts including these two keys (present, `None` if never written).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_market_data_store.py`, inside `class TestFundamentalsNewColumns` (the existing class covering `fcf`/`debt_to_equity`/etc — see lines 232-269):

```python
    def test_gross_margin_and_interest_coverage_upsert_and_read(self):
        ensure_tables()
        rows = [{
            "symbol": "VALCOL",
            "market_cap_b": 50.0,
            "price": 100.0,
            "beta": 1.0,
            "iv_pct": 25.0,
            "gross_margin": 0.486,
            "interest_coverage": 4.97,
        }]
        count = bulk_upsert_fundamentals(rows)
        assert count == 1

        result = get_fundamentals_for_tickers(["VALCOL"])
        assert len(result) == 1
        r = result[0]
        assert r["gross_margin"] == pytest.approx(0.486)
        assert r["interest_coverage"] == pytest.approx(4.97)

    def test_gross_margin_and_interest_coverage_default_to_none_when_omitted(self):
        ensure_tables()
        rows = [{"symbol": "VALCOL2", "market_cap_b": 10.0, "price": 50.0, "beta": 1.0, "iv_pct": None}]
        bulk_upsert_fundamentals(rows)

        result = get_fundamentals_for_tickers(["VALCOL2"])
        r = result[0]
        assert r["gross_margin"] is None
        assert r["interest_coverage"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_market_data_store.py -k gross_margin_and_interest_coverage -v`
Expected: FAIL — `KeyError: 'gross_margin'` (column/key doesn't exist yet).

- [ ] **Step 3: Add the columns to the schema and migration list**

In `src/market_data/store.py`, the `_DDL` `CREATE TABLE IF NOT EXISTS universe_fundamentals (...)` block (~lines 46-61): add two lines right after `peg_ratio REAL,`:

```sql
    peg_ratio       REAL,
    gross_margin       REAL,
    interest_coverage  REAL,
```

In `_NEW_FUNDAMENTAL_COLUMNS` (~lines 74-83), add two entries after `"peg_ratio REAL",`:

```python
    "peg_ratio REAL",
    "gross_margin REAL",
    "interest_coverage REAL",
```

- [ ] **Step 4: Wire the columns through upsert and both read functions**

In `bulk_upsert_fundamentals` (~lines 301-362): add `r.get("gross_margin")` and `r.get("interest_coverage")` to the `params` tuple (right after `r.get("peg_ratio"),`), add `gross_margin, interest_coverage` to both the `INSERT INTO ... (...)` column list and the `VALUES (...)` placeholder list (two more `?`), and add to the `ON CONFLICT ... DO UPDATE SET` block:

```python
                gross_margin       = excluded.gross_margin,
                interest_coverage  = excluded.interest_coverage,
```

In `get_all_fundamentals` (~lines 367-379) and `get_fundamentals_for_tickers` (~lines 382-398): add `gross_margin, interest_coverage` to both `SELECT` column lists, right after `peg_ratio`.

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_market_data_store.py -v`
Expected: PASS (including all pre-existing tests in the file — the migration is additive).

- [ ] **Step 6: Commit**

```bash
git add src/market_data/store.py tests/test_market_data_store.py
git commit -m "feat: add gross_margin/interest_coverage columns to universe_fundamentals"
```

---

## Task 2: Fetch layer — populate `gross_margin` / `interest_coverage` in the nightly refresh

**Files:**
- Modify: `src/market_data/refresh.py:69-115` (`_fetch_fundamentals_batch`)
- Test: `tests/test_market_data_refresh.py`

**Interfaces:**
- Consumes: `store.bulk_upsert_fundamentals` from Task 1 (accepts `gross_margin`/`interest_coverage` keys).
- Produces: each row dict returned by `_fetch_fundamentals_batch` gains `gross_margin: float | None` and `interest_coverage: float | None` keys.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_market_data_refresh.py`, a new helper and test class after `TestFetchFundamentalsBatch` (~line 236):

```python
def _make_income_stmt_df(ebit: float | None, interest_expense: float | None) -> pd.DataFrame:
    """Mimic yf.Ticker().get_income_stmt(freq='yearly') — one column (latest period),
    indexed by line-item name. Omits a row entirely when its value is None, matching
    how yfinance omits line items a company doesn't report (e.g. InterestExpense for
    debt-free companies)."""
    data = {}
    if ebit is not None:
        data["EBIT"] = ebit
    if interest_expense is not None:
        data["InterestExpense"] = interest_expense
    return pd.DataFrame({"2025-12-31": pd.Series(data)})


class TestFetchFundamentalsBatchGrossMarginAndInterestCoverage:
    def _mock_ticker(self, info: dict, income_stmt: pd.DataFrame | None = None) -> MagicMock:
        mock_ticker = MagicMock()
        mock_ticker.info = info
        mock_ticker.get_income_stmt.return_value = (
            income_stmt if income_stmt is not None else pd.DataFrame()
        )
        return mock_ticker

    def test_gross_margin_read_from_info(self):
        info = {**_make_ticker_info("AAPL"), "grossMargins": 0.48653}
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(33.81e9, 6.80e9))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["gross_margin"] == pytest.approx(0.48653)

    def test_gross_margin_missing_is_none(self):
        info = _make_ticker_info("AAPL")  # no grossMargins key
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(33.81e9, 6.80e9))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["gross_margin"] is None

    def test_interest_coverage_computed_from_ebit_and_interest_expense(self):
        info = _make_ticker_info("T")
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(33.811e9, 6.804e9))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["T"])

        assert rows[0]["interest_coverage"] == pytest.approx(4.969, abs=0.01)

    def test_interest_coverage_none_when_interest_expense_missing(self):
        """Debt-free companies (e.g. AAPL) report no InterestExpense line at all."""
        info = _make_ticker_info("AAPL")
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(120e9, None))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["interest_coverage"] is None

    def test_interest_coverage_none_when_interest_expense_is_zero(self):
        info = _make_ticker_info("AAPL")
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(120e9, 0.0))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["interest_coverage"] is None

    def test_interest_coverage_none_when_income_stmt_raises(self):
        """A failure fetching the income statement must not drop the whole ticker."""
        mock_ticker = self._mock_ticker(_make_ticker_info("AAPL"))
        mock_ticker.get_income_stmt.side_effect = RuntimeError("network error")
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["interest_coverage"] is None
        assert rows[0]["symbol"] == "AAPL"  # rest of the row still populated
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_market_data_refresh.py -k GrossMarginAndInterestCoverage -v`
Expected: FAIL — `KeyError: 'gross_margin'` / `'interest_coverage'`.

- [ ] **Step 3: Implement the fetch logic**

In `src/market_data/refresh.py`, add a new helper function right before `_fetch_fundamentals_batch` (~line 69):

```python
def _fetch_interest_coverage(ticker: "yf.Ticker") -> float | None:
    """EBIT / |InterestExpense| from the latest annual income statement.

    Returns None if the statement is unavailable, EBIT/InterestExpense are
    missing, or InterestExpense is 0 (near-debt-free companies commonly
    report no InterestExpense line at all — treated as "gate skipped",
    not "infinite coverage").
    """
    try:
        stmt = ticker.get_income_stmt(freq="yearly")
        if stmt is None or stmt.empty:
            return None
        if "EBIT" not in stmt.index or "InterestExpense" not in stmt.index:
            return None
        ebit = _to_float(stmt.loc["EBIT"].iloc[0])
        interest_expense = _to_float(stmt.loc["InterestExpense"].iloc[0])
        if ebit is None or interest_expense is None or interest_expense == 0:
            return None
        return ebit / abs(interest_expense)
    except Exception:
        return None
```

Then in `_fetch_fundamentals_batch` (~lines 69-115), change the loop body to reuse a single `Ticker` instance for both calls, add the `grossMargins` field read, and call the new helper. Replace:

```python
        try:
            info = yf.Ticker(symbol).info
            if info.get("quoteType", "").upper() != "EQUITY":
                continue
```

with:

```python
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            if info.get("quoteType", "").upper() != "EQUITY":
                continue
```

Then, right after the existing `peg_ratio = _to_float(info.get("trailingPegRatio"))` line (~line 93), add:

```python
            gross_margin = _to_float(info.get("grossMargins"))
            interest_coverage = _fetch_interest_coverage(ticker)
            time.sleep(_INCOME_STMT_SLEEP_S)  # second, heavier network call — extra throttle
```

And in the `rows.append({...})` dict (~lines 96-110), add two entries right after `"peg_ratio": round(peg_ratio, 2) if peg_ratio is not None else None,`:

```python
                "gross_margin": round(gross_margin, 4) if gross_margin is not None else None,
                "interest_coverage": round(interest_coverage, 2) if interest_coverage is not None else None,
```

Add the new sleep constant next to the existing throttle constants (~line 48):

```python
_INCOME_STMT_SLEEP_S = 0.3  # extra throttle after the heavier get_income_stmt() call
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_market_data_refresh.py -v`
Expected: PASS (all tests in the file, including pre-existing ones — `time.sleep` is real but tests run fast since `_INCOME_STMT_SLEEP_S` is only 0.3s per ticker and test batches are tiny).

- [ ] **Step 5: Commit**

```bash
git add src/market_data/refresh.py tests/test_market_data_refresh.py
git commit -m "feat: fetch gross_margin and interest_coverage in nightly fundamentals refresh"
```

---

## Task 3: Filter layer — `min_gross_margin` / `min_interest_coverage` gates on the scanner

**Files:**
- Modify: `src/screener/csp_scanner.py:153-264` (`ScannerParams`), `csp_scanner.py:485-582` (`_fundamental_filter_from_store`)
- Test: `tests/test_fundamental_filter.py`

**Interfaces:**
- Consumes: `row.get("gross_margin")` / `row.get("interest_coverage")` from store rows (Task 1's schema).
- Produces: `ScannerParams.min_gross_margin: float | None`, `ScannerParams.min_interest_coverage: float | None` (both default `None`), threaded through `ScannerParams.from_query(...)`. `_fundamental_filter_from_store` output rows gain `gross_margin`/`interest_coverage` keys.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_fundamental_filter.py`: extend the `_row()` helper (~lines 10-23) is left alone (defaults must keep passing all gates, and the two new gates default to `None`/off — no base value needed). Add two new test classes after `TestDividendYieldGate` (~line 107 onward, matching the exact style of `TestRevenueGrowthGate`):

```python
class TestGrossMarginGate:
    def test_high_margin_passes(self):
        result = _run([_row(symbol="A", gross_margin=0.55)], min_gross_margin=0.40)
        assert "A" in result

    def test_low_margin_fails(self):
        result = _run([_row(symbol="A", gross_margin=0.25)], min_gross_margin=0.40)
        assert "A" not in result

    def test_none_margin_passes(self):
        result = _run([_row(symbol="A", gross_margin=None)], min_gross_margin=0.40)
        assert "A" in result

    def test_gate_disabled_by_default(self):
        result = _run([_row(symbol="A", gross_margin=0.01)])
        assert "A" in result


class TestInterestCoverageGate:
    def test_high_coverage_passes(self):
        result = _run([_row(symbol="A", interest_coverage=5.0)], min_interest_coverage=4.0)
        assert "A" in result

    def test_low_coverage_fails(self):
        result = _run([_row(symbol="A", interest_coverage=2.0)], min_interest_coverage=4.0)
        assert "A" not in result

    def test_none_coverage_passes(self):
        """Debt-free companies store None (no InterestExpense line) — gate is skipped,
        which is the correct outcome (no debt = trivially solvent)."""
        result = _run([_row(symbol="A", interest_coverage=None)], min_interest_coverage=4.0)
        assert "A" in result

    def test_gate_disabled_by_default(self):
        result = _run([_row(symbol="A", interest_coverage=0.1)])
        assert "A" in result


class TestFromQueryGrossMarginAndInterestCoverage:
    def test_from_query_threads_new_params(self):
        params = ScannerParams.from_query(min_gross_margin=0.40, min_interest_coverage=4.0)
        assert params.min_gross_margin == pytest.approx(0.40)
        assert params.min_interest_coverage == pytest.approx(4.0)

    def test_from_query_defaults_to_none(self):
        params = ScannerParams.from_query()
        assert params.min_gross_margin is None
        assert params.min_interest_coverage is None
```

(`tests/test_fundamental_filter.py` already has `import pytest` at the top — no import changes needed.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm test python3 -m pytest tests/test_fundamental_filter.py -k "GrossMargin or InterestCoverage" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'min_gross_margin'`.

- [ ] **Step 3: Add the two new params to `ScannerParams`**

In `src/screener/csp_scanner.py`, in the `ScannerParams` dataclass (~line 175, right after `max_peg_ratio: float | None = None`):

```python
    max_peg_ratio:           float | None = None
    min_gross_margin:        float | None = None
    min_interest_coverage:   float | None = None
```

In `from_query` (~line 215, param list, right after `max_peg_ratio: float | None = None,`):

```python
        max_peg_ratio: float | None = None,
        min_gross_margin: float | None = None,
        min_interest_coverage: float | None = None,
```

And in the `return cls(...)` block (~line 253, right after `max_peg_ratio = max_peg_ratio,`):

```python
            max_peg_ratio        = max_peg_ratio,
            min_gross_margin      = min_gross_margin,
            min_interest_coverage = min_interest_coverage,
```

- [ ] **Step 4: Add the gate checks and output fields to `_fundamental_filter_from_store`**

In `_fundamental_filter_from_store` (~line 556-560, right after the existing PEG ratio gate block):

```python
        # PEG ratio gate
        peg_ratio = row.get("peg_ratio")
        if params.max_peg_ratio is not None and peg_ratio is not None:
            if peg_ratio > params.max_peg_ratio:
                continue

        # Gross margin gate
        gross_margin = row.get("gross_margin")
        if params.min_gross_margin is not None and gross_margin is not None:
            if gross_margin < params.min_gross_margin:
                continue

        # Interest coverage gate
        interest_coverage = row.get("interest_coverage")
        if params.min_interest_coverage is not None and interest_coverage is not None:
            if interest_coverage < params.min_interest_coverage:
                continue
```

And in the `fundamental_rows.append({...})` output dict (~lines 569-579, right after `"peg_ratio": row.get("peg_ratio"),`):

```python
            "peg_ratio":    row.get("peg_ratio"),
            "gross_margin":      row.get("gross_margin"),
            "interest_coverage": row.get("interest_coverage"),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose run --rm test python3 -m pytest tests/test_fundamental_filter.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 6: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_fundamental_filter.py
git commit -m "feat: add min_gross_margin/min_interest_coverage scanner filter gates"
```

---

## Task 4: API layer — expose the two new query params

**Files:**
- Modify: `src/api/main.py:414-519` (`get_csp_scan_candidates`), `src/api/main.py:521-579` (`invalidate_csp_scan_cache`)

**Interfaces:**
- Consumes: `ScannerParams.from_query(min_gross_margin=..., min_interest_coverage=...)` from Task 3.
- Produces: `GET /api/screener/csp-scan?min_gross_margin=0.4&min_interest_coverage=4.0` and the matching `DELETE` cache-bust endpoint both accept the two new params.

- [ ] **Step 1: Add the two new `Query` params to both endpoints**

In `src/api/main.py`, in `get_csp_scan_candidates` (~line 433, right after `max_peg_ratio: float | None = Query(default=None),`):

```python
    max_peg_ratio:           float | None = Query(default=None),
    min_gross_margin:        float | None = Query(default=None),
    min_interest_coverage:   float | None = Query(default=None),
```

And in the `ScannerParams.from_query(...)` call inside that function (~line 470, right after `max_peg_ratio=max_peg_ratio,`):

```python
        max_peg_ratio=max_peg_ratio,
        min_gross_margin=min_gross_margin,
        min_interest_coverage=min_interest_coverage,
```

Repeat the identical two edits in `invalidate_csp_scan_cache` (~line 540 for the `Query` param, ~line 566 for the `from_query(...)` call) — this endpoint must accept the exact same param set so cache invalidation can target the right cache key.

- [ ] **Step 2: Verify manually against the running dev API**

This codebase has no `TestClient`-based API test suite (verified — every other scanner test exercises `_fundamental_filter_from_store` / `ScannerParams` directly, not the HTTP layer). Verify by hand against dev:

```bash
curl -s "https://dev-mi.austin10berge.com/api/screener/csp-scan?min_gross_margin=0.40&min_interest_coverage=4.0&max_peg_ratio=1.5&min_revenue_growth=0.10" | head -c 500
```

Expected: a JSON response (not a 422 validation error) — confirms FastAPI accepted the new query params and routed them through without raising.

- [ ] **Step 3: Commit**

```bash
git add src/api/main.py
git commit -m "feat: expose min_gross_margin/min_interest_coverage on the CSP scan API"
```

---

## Task 5: Legacy frontend (`scanner.js` / `scanner.html`) — filters + preset

**Files:**
- Modify: `src/web/scanner.js:38-478` (state, `PARAM_CONFIG`, restore/persist, `_buildQueryString`), `src/web/scanner.html:392-401` (preset button placement)

**Interfaces:**
- Consumes: the API params from Task 4.
- Produces: three new editable param badges (`min_gross_margin`, `min_interest_coverage`, and `max_peg_ratio` — see note below) and a "Reddit Value Screen" preset button that sets all four gate values and runs the scan.

**Note on scope:** while reading the existing code, `max_peg_ratio` was found to already exist on the backend (`ScannerParams`, API) but was never wired into either frontend — it's not in `PARAM_CONFIG`, not in `_buildQueryString`, not restored/persisted. Without fixing this, the preset button could not actually apply the PEG gate. This task fixes that gap alongside adding the two brand-new fields, since the preset's correctness depends on it.

- [ ] **Step 1: Add the three params to `_state.params` defaults**

In `src/web/scanner.js`, in `_state.params` (~line 53-58, right after `min_dividend_yield: null,`):

```js
        min_dividend_yield:  null,
        max_peg_ratio:        null,
        min_gross_margin:     null,
        min_interest_coverage: null,
```

- [ ] **Step 2: Add restore handling**

In `_restoreParams()` (~line 105, right after the `min_dividend_yield` restore line):

```js
        if (typeof saved.min_dividend_yield  === 'number' || saved.min_dividend_yield  === null) p.min_dividend_yield  = saved.min_dividend_yield;
        if (typeof saved.max_peg_ratio         === 'number' || saved.max_peg_ratio         === null) p.max_peg_ratio         = saved.max_peg_ratio;
        if (typeof saved.min_gross_margin      === 'number' || saved.min_gross_margin      === null) p.min_gross_margin      = saved.min_gross_margin;
        if (typeof saved.min_interest_coverage === 'number' || saved.min_interest_coverage === null) p.min_interest_coverage = saved.min_interest_coverage;
```

(`_persistParams()` needs no change — it spreads `_state.params` wholesale, so the new keys are saved automatically.)

- [ ] **Step 3: Add the three badges to `PARAM_CONFIG`**

In `PARAM_CONFIG` (~line 143, right after the `min_dividend_yield` entry):

```js
    { key: 'min_dividend_yield',  label: 'Div >',  suffix: '%',  min: 0,    max: 20,   step: 0.1, decimals: 1, scale: 100, nullable: true },
    { key: 'max_peg_ratio',        label: 'PEG <',        suffix: '',  min: 0, max: 10, step: 0.1, decimals: 1, nullable: true },
    { key: 'min_gross_margin',     label: 'Gross Mgn >',  suffix: '%', min: 0, max: 100, step: 1, decimals: 0, scale: 100, nullable: true },
    { key: 'min_interest_coverage', label: 'Int Cov >',   suffix: 'x', min: 0, max: 50, step: 0.5, decimals: 1, nullable: true },
```

- [ ] **Step 4: Add activation defaults for the nullable-badge "click to enable" flow**

In `enableNullableBadge`'s `activationDefaults` map (~lines 202-208, right after `min_dividend_yield: 0.01,`):

```js
        min_dividend_yield:  0.01,
        max_peg_ratio:        1.5,
        min_gross_margin:     0.40,
        min_interest_coverage: 4.0,
```

- [ ] **Step 5: Add the three params to `_buildQueryString`**

In `_buildQueryString()` (~line 474, right after the `min_dividend_yield` line):

```js
    if (p.min_dividend_yield  !== null && p.min_dividend_yield  !== undefined) qs.set('min_dividend_yield',  p.min_dividend_yield);
    if (p.max_peg_ratio         !== null && p.max_peg_ratio         !== undefined) qs.set('max_peg_ratio',         p.max_peg_ratio);
    if (p.min_gross_margin      !== null && p.min_gross_margin      !== undefined) qs.set('min_gross_margin',      p.min_gross_margin);
    if (p.min_interest_coverage !== null && p.min_interest_coverage !== undefined) qs.set('min_interest_coverage', p.min_interest_coverage);
```

- [ ] **Step 6: Add the preset function**

Add a new function right after `toggleWatchlistUniverse()` (~line 225):

```js
function applyValueScreenPreset() {
    _state.params.min_interest_coverage = 4.0;
    _state.params.min_gross_margin      = 0.40;
    _state.params.min_revenue_growth    = 0.10;
    _state.params.max_peg_ratio         = 1.5;
    renderParamBadges();
    _persistParams();
    startScan();
}
```

- [ ] **Step 7: Add the preset button to the HTML**

In `src/web/scanner.html` (~lines 392-397), right after the watchlist-universe toggle button's closing `</div>`:

```html
            <!-- Universe scope toggle -->
            <div class="universe-toggle-area">
                <button id="btn-watchlist-universe" class="btn-universe-toggle" onclick="toggleWatchlistUniverse()">
                    S&amp;P 500 / NASDAQ 100 only
                </button>
            </div>

            <!-- Value-screen preset -->
            <div class="universe-toggle-area">
                <button id="btn-value-screen-preset" class="btn-universe-toggle" onclick="applyValueScreenPreset()">
                    Reddit Value Screen
                </button>
            </div>
```

- [ ] **Step 8: Verify in the browser**

Use the Playwright MCP against dev (per this repo's `CLAUDE.md` — the frontend is JS-rendered, `curl`/`WebFetch` won't show it):

1. `mcp__playwright__browser_navigate` to `https://dev-mi.austin10berge.com/scanner.html`
2. `mcp__playwright__browser_snapshot` to confirm the "Reddit Value Screen" button and the three new param badges (`PEG <`, `Gross Mgn >`, `Int Cov >`, initially showing "off") are present
3. Click the "Reddit Value Screen" button
4. `mcp__playwright__browser_snapshot` again — confirm the four badges now read `PEG <1.5`, `Gross Mgn >40%`, `Int Cov >4.0x`, `Rev >10%`, and that a scan has started (status text changes from the initial prompt)

- [ ] **Step 9: Commit**

```bash
git add src/web/scanner.js src/web/scanner.html
git commit -m "feat: add gross margin / interest coverage filters + value-screen preset (legacy scanner UI)"
```

---

## Task 6: v2 frontend (`v2/scanner.js`) — filters + preset

**Files:**
- Modify: `src/web/v2/scanner.js:12-605, 712-734`

**Interfaces:**
- Consumes: the API params from Task 4.
- Produces: three new fields in the v2 filter sheet's "Fundamentals" section, and `ScannerView.applyValueScreenPreset()` exposed for a new preset button.

**Note:** same `max_peg_ratio` gap noted in Task 5 also applies here — it's fixed as part of this task.

- [ ] **Step 1: Add the three params to `DEFAULT_PARAMS`**

In `src/web/v2/scanner.js`, `DEFAULT_PARAMS` (~line 18, right after `max_forward_pe: null, max_dividend_yield: null,`):

```js
        max_forward_pe: null, max_dividend_yield: null,
        max_peg_ratio: null, min_gross_margin: null, min_interest_coverage: null,
```

- [ ] **Step 2: Insert the three fields into `PARAM_CONFIG`'s Fundamentals section**

`PARAM_CONFIG` is sliced by fixed index elsewhere in this file (`slice(10, 17)` for Fundamentals, `slice(17)` for Technical Numeric — see `_sheetHtml()`), so the three new fundamentals fields must be inserted at the *end* of the Fundamentals block (current indices 10-16), not appended to the whole array. In `PARAM_CONFIG` (~line 64, right after the `max_dividend_yield` entry, i.e. immediately before the `// Technical numeric` comment):

```js
        { key: 'max_dividend_yield',  label: 'Div Yield <', suffix: '%',  min: 0,    max: 20,   step: 0.1,  decimals: 1, nullable: true },
        { key: 'max_peg_ratio',           label: 'PEG <',          suffix: '',  min: 0,   max: 10,  step: 0.1,  decimals: 1, nullable: true },
        { key: 'min_gross_margin',        label: 'Gross Margin >', suffix: '%', min: 0,   max: 100, step: 1,    decimals: 0, scale: 100, nullable: true },
        { key: 'min_interest_coverage',   label: 'Int Coverage >', suffix: 'x', min: 0,   max: 50,  step: 0.5,  decimals: 1, nullable: true },
        // Technical numeric — indices 20–26 (nullable; rendered in Technical Numeric section)
```

Delete the old `// Technical numeric — indices 17–23 ...` comment line (replaced by the one above) and update the two slice call sites:

`_sheetHtml()` (~lines 474-475):
```js
        const fundFields     = PARAM_CONFIG.slice(10, 20).map(_nullableField).join('');
        const techNumFields  = PARAM_CONFIG.slice(20).map(_nullableField).join('');
```

(`_readSheetIntoParams()`'s `PARAM_CONFIG.slice(10)` at ~line 570 needs **no change** — it already reads everything from index 10 onward regardless of how many fundamentals fields exist.)

- [ ] **Step 3: Add restore handling**

In `_restoreParams()` (~line 100, right after the `max_dividend_yield` restore line):

```js
            if (typeof saved.max_dividend_yield  === 'number' || saved.max_dividend_yield  === null) p.max_dividend_yield  = saved.max_dividend_yield;
            if (typeof saved.max_peg_ratio         === 'number' || saved.max_peg_ratio         === null) p.max_peg_ratio         = saved.max_peg_ratio;
            if (typeof saved.min_gross_margin      === 'number' || saved.min_gross_margin      === null) p.min_gross_margin      = saved.min_gross_margin;
            if (typeof saved.min_interest_coverage === 'number' || saved.min_interest_coverage === null) p.min_interest_coverage = saved.min_interest_coverage;
```

- [ ] **Step 4: Add the three params to `_buildQueryString`**

In `_buildQueryString()` (~line 173, right after the `max_dividend_yield` line):

```js
        if (p.max_dividend_yield  !== null && p.max_dividend_yield  !== undefined) qs.set('max_dividend_yield',  p.max_dividend_yield);
        if (p.max_peg_ratio         !== null && p.max_peg_ratio         !== undefined) qs.set('max_peg_ratio',         p.max_peg_ratio);
        if (p.min_gross_margin      !== null && p.min_gross_margin      !== undefined) qs.set('min_gross_margin',      p.min_gross_margin);
        if (p.min_interest_coverage !== null && p.min_interest_coverage !== undefined) qs.set('min_interest_coverage', p.min_interest_coverage);
```

- [ ] **Step 5: Add the preset function and expose it**

Add a new function right after `applyAndScan()` (~line 605):

```js
    // ── Value-screen preset ─────────────────────────────────────────────────────

    function applyValueScreenPreset() {
        const p = _state.params;
        p.min_interest_coverage = 4.0;
        p.min_gross_margin      = 0.40;
        p.min_revenue_growth    = 0.10;
        p.max_peg_ratio         = 1.5;
        _persistParams();
        closeFilterSheet();
        _renderHeader();
        _renderActiveParams();
        runScan();
    }
```

Add it to the exposed API (~line 733, in `window.ScannerView = {...}`):

```js
        openFilterSheet, closeFilterSheet, applyAndScan, forceRescan, applyValueScreenPreset,
```

- [ ] **Step 6: Add a preset button to the filter sheet footer**

In `_sheetHtml()`'s `scn-sheet-footer` (~lines 529-532):

```js
            <div class="scn-sheet-footer">
                <button class="scn-sheet-apply" onclick="ScannerView.applyAndScan()">Apply &amp; Scan</button>
                <button class="scn-sheet-rescan" onclick="ScannerView.forceRescan()">Force Rescan</button>
                <button class="scn-sheet-rescan" onclick="ScannerView.applyValueScreenPreset()">Reddit Value Screen</button>
            </div>
```

- [ ] **Step 7: Verify in the browser**

The v2 UI is a single-page app (`src/web/v2/index.html` + `app.js`'s `switchTab()`) — there's no separate URL for the scanner, it's a tab. Using the Playwright MCP against dev:

1. `mcp__playwright__browser_navigate` to `https://dev-mi.austin10berge.com/v2/` (or `/v2/index.html` — confirm which resolves by checking the response; both map to the same `index.html` in this nginx-served static setup)
2. `mcp__playwright__browser_snapshot`, find and click the "Scanner" nav item (`switchTab('scanner')`) if it isn't already the active tab
3. Open the filter sheet (the button that calls `ScannerView.openFilterSheet()`), `mcp__playwright__browser_snapshot` — confirm the "Fundamentals" section now shows `PEG <`, `Gross Margin >`, `Int Coverage >` fields alongside the pre-existing ones, and the "Technical Numeric" section is unchanged (still shows `RV20 <`, `BB Width >`, etc. — confirms the slice-index fix didn't misplace any field)
4. Click "Reddit Value Screen"
5. `mcp__playwright__browser_snapshot` — confirm the active-params summary reflects the 4 preset values and a scan is running

- [ ] **Step 8: Commit**

```bash
git add src/web/v2/scanner.js
git commit -m "feat: add gross margin / interest coverage filters + value-screen preset (v2 scanner UI)"
```

---

## Task 7: End-to-end integration test

**Files:**
- Modify: `tests/test_csp_scanner_integration.py`

**Interfaces:**
- Consumes: everything from Tasks 1-3 (store schema, filter gates, `ScannerParams`).

- [ ] **Step 1: Write the failing test**

Find the existing pattern in `tests/test_csp_scanner_integration.py` for seeding the local store and running a scan end-to-end (it already covers `revenue_growth`/`peg_ratio` per the earlier grep — follow its exact seeding/assertion style). Add a test that seeds two tickers — one passing all 4 value-screen gates, one failing on gross margin — and runs `apply_fundamental_filter` (or whatever the file's existing end-to-end entry point is) with the preset's exact threshold values (`min_interest_coverage=4.0, min_gross_margin=0.40, min_revenue_growth=0.10, max_peg_ratio=1.5`), asserting the passing ticker is included and the failing one is excluded.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_csp_scanner_integration.py -k value_screen -v`
Expected: FAIL (test doesn't exist yet / gates not wired).

- [ ] **Step 3: Confirm it passes with no further implementation**

By this point Tasks 1-3 already implemented everything this test needs — this step should require no new production code, only the test itself.

Run: `docker compose run --rm test python3 -m pytest tests/test_csp_scanner_integration.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 4: Run the full test suite**

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py`
Expected: PASS (per this repo's `CLAUDE.md`, `test_stock_screener.py` is excluded from the full-suite run).

- [ ] **Step 5: Commit**

```bash
git add tests/test_csp_scanner_integration.py
git commit -m "test: add end-to-end value-screen gate coverage"
```
