# CSP Scan: Dedupe Technical Indicator Computation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `run_csp_scan` from recomputing RSI/ADX/SMA50 twice per ticker from two different data sources (local 2-year OHLCV store in Stage 3, then a fresh live yfinance fetch in `screen_csp_candidates`) — the heaviest endpoint in the app (`/api/screener/csp-scan`) currently does a live yfinance round-trip per ticker that is entirely redundant with work already done a few lines earlier.

**Architecture:** `apply_technical_conditions()` in `src/screener/csp_scanner.py` already computes and stores a full indicators dict (`row["technical_indicators"]`) per ticker that passes Stage 3. Thread that dict through to `screen_csp_candidates()` in `src/screener/options.py` via a new optional parameter, so it uses the already-computed values instead of calling `_compute_technicals()` (a fresh `yf.Ticker(symbol).history(period="3mo")` fetch) again. `screen_csp_candidates()` must keep computing technicals itself when called without precomputed data (e.g. from `/api/screener/csp`, which calls it directly on the raw watchlist with no Stage 3 pass first).

**Tech Stack:** Python, pandas-ta, yfinance, pytest.

## Global Constraints

- Do not change the public behavior of `/api/screener/csp` (plain watchlist CSP screener) — it has no Stage 3 pass, so `screen_csp_candidates()` must still self-compute technicals when no precomputed dict is passed.
- Do not change the RSI/ADX/pullback_mode gate semantics — same thresholds, same pass/fail logic, just sourced from one computation instead of two.
- Keep `_compute_technical_indicators()` (csp_scanner.py) as the single source of truth for indicator values once this lands; `_compute_technicals()` (options.py) remains only as the fallback path for callers with no precomputed data.

---

## File Structure

- Modify: `src/screener/csp_scanner.py` — add `return_5d` (5-day % return) to `_compute_technical_indicators()`'s output dict, so it's a superset of everything `options.py`'s pullback-mode gate needs. Modify `run_csp_scan()` to pass a `{symbol: indicators}` map into `screen_csp_candidates()`.
- Modify: `src/screener/options.py` — add an optional `precomputed_technicals: dict[str, dict] | None` parameter to `screen_csp_candidates()`. When a symbol has a precomputed entry, use it instead of calling `_compute_technicals(symbol)`.
- Test: `tests/test_csp_scanner_conditions.py` — new test asserting `return_5d` is present and correct.
- Test: `tests/test_options_lookup.py` — new test asserting `screen_csp_candidates()` does not call `_compute_technicals` when `precomputed_technicals` covers all requested tickers.
- Test: `tests/test_csp_scanner_integration.py` — new test asserting `run_csp_scan()` calls `_compute_technicals` zero times (i.e. the live yfinance recompute path in options.py is never hit) for tickers that passed Stage 3.

## Interfaces

- `_compute_technical_indicators(symbol: str, hist: pd.DataFrame) -> dict | None` (csp_scanner.py) — existing function, gains one new key: `"return_5d": float | None`.
- `screen_csp_candidates(tickers=None, min_dte=None, max_dte=None, min_rsi=None, max_rsi=None, min_adx=None, max_adx=None, precomputed_technicals: dict[str, dict] | None = None) -> list[dict]` (options.py) — new trailing kwarg, defaults to `None` (preserves current behavior for existing callers).

---

### Task 1: Add `return_5d` to csp_scanner's indicator computation

**Files:**
- Modify: `src/screener/csp_scanner.py:712-` (`_compute_technical_indicators`, and its `return {...}` block around line 780-796)
- Test: `tests/test_csp_scanner_conditions.py`

**Interfaces:**
- Produces: `_compute_technical_indicators(...)` return dict now includes `"return_5d": float | None` — 5-day % price return, same formula options.py's `_compute_technicals` already uses: `(close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100`, `None` if `len(hist) < 6` or the result is NaN.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_csp_scanner_conditions.py` (mirror the existing fixture style used for other indicator assertions in that file — build a synthetic OHLCV `pd.DataFrame` with a known 6-bar tail):

```python
def test_compute_technical_indicators_includes_return_5d():
    import pandas as pd
    from src.screener.csp_scanner import _compute_technical_indicators

    dates = pd.bdate_range(end="2024-06-28", periods=210)
    closes = [100.0] * 204 + [101.0, 102.0, 103.0, 104.0, 105.0, 110.0]
    hist = pd.DataFrame(
        {
            "Open": closes, "High": closes, "Low": closes, "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=dates,
    )

    indicators = _compute_technical_indicators("TEST", hist)

    assert indicators is not None
    assert "return_5d" in indicators
    expected = round((110.0 - 101.0) / 101.0 * 100, 2)
    assert indicators["return_5d"] == pytest.approx(expected, abs=0.05)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_csp_scanner_conditions.py::test_compute_technical_indicators_includes_return_5d -v`
Expected: FAIL with `KeyError` or `assert "return_5d" in indicators` failing (key absent).

- [ ] **Step 3: Implement `return_5d` in `_compute_technical_indicators`**

In `src/screener/csp_scanner.py`, inside `_compute_technical_indicators`, before the final `return {...}` block, add (mirroring options.py's `_compute_technicals` formula exactly):

```python
        # ── 5-day return — used by options.py's pullback_mode gate ──────────────────
        return_5d: float | None = None
        if len(hist) >= 6:
            v = float((close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100)
            return_5d = None if math.isnan(v) else v
```

Then add `"return_5d": return_5d,` to the returned dict (next to the existing `"adx": adx,` entry). Confirm `math` is already imported at the top of the file (it is, per existing NaN checks elsewhere in this function) — if not, add `import math`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_csp_scanner_conditions.py::test_compute_technical_indicators_includes_return_5d -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test suite for this file to check no regression**

Run: `pytest tests/test_csp_scanner_conditions.py -v`
Expected: All PASS (adding a new dict key must not break any existing assertion — check for any test that does an exact dict equality against the indicators return value; if one exists, update it to include `return_5d`).

- [ ] **Step 6: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_csp_scanner_conditions.py
git commit -m "feat(csp-scanner): compute return_5d so indicators dict covers pullback_mode needs"
```

---

### Task 2: Let `screen_csp_candidates` accept precomputed technicals

**Files:**
- Modify: `src/screener/options.py:184-260` (`screen_csp_candidates`, Step 1 technical pre-filter loop)
- Test: `tests/test_options_lookup.py`

**Interfaces:**
- Consumes: nothing new from Task 1 directly, but relies on the indicators dict shape from `_compute_technical_indicators` (keys: `price, sma20, sma50, sma150, sma200, ema200, price_vs_ema200_pct, bb_lower, bb_upper, bb_width_pct, bb_pct_from_lower, volume_ratio, pct_from_52wk_high, adr20_pct, rsi, adx, return_5d`) matching the exact key names `_compute_technicals` in options.py already produces for `rsi`, `adx`, `sma50`, `return_5d` — no renaming needed.
- Produces: `screen_csp_candidates(..., precomputed_technicals: dict[str, dict] | None = None)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_options_lookup.py` (use the mocking conventions already present in that file for `yf.Ticker`/Alpaca calls — patch `src.screener.options._compute_technicals` directly so the test doesn't need real yfinance data):

```python
from unittest.mock import patch

def test_screen_csp_candidates_uses_precomputed_technicals(monkeypatch):
    from src.screener import options

    precomputed = {
        "AAPL": {"rsi": 45.0, "adx": 22.0, "sma50": 190.0, "return_5d": -1.2},
    }

    # No option chain data → screen_csp_candidates returns [] quickly, but we only
    # care whether _compute_technicals was called.
    monkeypatch.setattr(options, "get_csp_settings", lambda: {
        "min_dte": 7, "max_dte": 45, "min_rsi": 0, "max_rsi": 70,
        "min_adx": 15, "max_adx": 60, "pullback_mode": False,
    })

    with patch.object(options, "_compute_technicals") as mock_compute, \
         patch.object(options.yf, "Ticker") as mock_ticker:
        mock_ticker.return_value.options = ()  # no expirations → loop exits cleanly
        options.screen_csp_candidates(
            tickers=["AAPL"], precomputed_technicals=precomputed
        )
        mock_compute.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_options_lookup.py::test_screen_csp_candidates_uses_precomputed_technicals -v`
Expected: FAIL with `TypeError: screen_csp_candidates() got an unexpected keyword argument 'precomputed_technicals'`.

- [ ] **Step 3: Implement the parameter and lookup-first logic**

In `src/screener/options.py`, change the signature:

```python
def screen_csp_candidates(
    tickers: list[str] | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    min_rsi: float | None = None,
    max_rsi: float | None = None,
    min_adx: float | None = None,
    max_adx: float | None = None,
    precomputed_technicals: dict[str, dict] | None = None,
) -> list[dict]:
```

In the Step 1 loop, replace:

```python
    for symbol in tickers:
        tech = _compute_technicals(symbol)
```

with:

```python
    for symbol in tickers:
        if precomputed_technicals is not None and symbol in precomputed_technicals:
            tech = precomputed_technicals[symbol]
        else:
            tech = _compute_technicals(symbol)
```

Leave every downstream line in the loop (RSI gate, ADX gate, pullback_mode gate, `technicals[symbol] = tech`) unchanged — they only read from the `tech` dict, which now has the same shape regardless of source.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_options_lookup.py::test_screen_csp_candidates_uses_precomputed_technicals -v`
Expected: PASS

- [ ] **Step 5: Run full options test file to check no regression**

Run: `pytest tests/test_options_lookup.py -v`
Expected: All PASS — in particular, confirm any existing test that calls `screen_csp_candidates()` without `precomputed_technicals` still exercises the `_compute_technicals` fallback path unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/screener/options.py tests/test_options_lookup.py
git commit -m "feat(options): screen_csp_candidates accepts precomputed technicals to skip redundant yfinance fetch"
```

---

### Task 3: Wire `run_csp_scan` to pass Stage 3's indicators through

**Files:**
- Modify: `src/screener/csp_scanner.py:1114-1124` (the `screen_csp_candidates(...)` call inside `run_csp_scan`)
- Test: `tests/test_csp_scanner_integration.py`

**Interfaces:**
- Consumes: `tech_rows` (list of dicts, each with `row["technical_indicators"]` populated by `apply_technical_conditions` — already true today, see `csp_scanner.py:806` `row["technical_indicators"] = indicators`) and `screen_csp_candidates(..., precomputed_technicals=...)` from Task 2.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_csp_scanner_integration.py` (follow the module's existing mocking conventions for `run_csp_scan`'s dependencies — patch `src.screener.options._compute_technicals` and assert it is never called end-to-end):

```python
from unittest.mock import patch

def test_run_csp_scan_does_not_recompute_technicals_live(monkeypatch):
    from src.screener import csp_scanner, options

    # Reuse whatever fixture/monkeypatch helpers this file already has to get
    # apply_technical_conditions() to report one passing ticker with a full
    # technical_indicators dict (mirror the setup of the nearest existing
    # "tech_passing" test above this one in the file).
    ...  # adapt to the file's existing fixtures — the assertion below is the point

    with patch.object(options, "_compute_technicals") as mock_compute:
        csp_scanner.run_csp_scan(params=csp_scanner.ScannerParams())
        mock_compute.assert_not_called()
```

Note for the implementer: this file already has integration-style tests around `run_csp_scan`/`apply_technical_conditions` — find the closest existing test that gets a ticker through Stage 3 with real indicator data (not a full mock of `apply_technical_conditions` itself, since we need `technical_indicators` populated on the rows) and adapt its setup rather than inventing new fixtures from scratch.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_csp_scanner_integration.py::test_run_csp_scan_does_not_recompute_technicals_live -v`
Expected: FAIL — `mock_compute` gets called once per Stage-3-passing ticker (the current bug).

- [ ] **Step 3: Implement the wiring**

In `src/screener/csp_scanner.py`, in `run_csp_scan`, right before the `candidates = screen_csp_candidates(...)` call (around line 1114), build the lookup:

```python
    # Stage 3 already computed RSI/ADX/SMA50/etc. from the local OHLCV store —
    # reuse it instead of letting screen_csp_candidates() re-fetch live yfinance
    # history per ticker.
    precomputed_technicals = {
        row["symbol"]: row["technical_indicators"]
        for row in tech_rows
        if row.get("technical_indicators")
    }
```

Then add the kwarg to the existing call:

```python
    candidates = screen_csp_candidates(
        tickers=tech_passing,
        min_dte=params.min_dte,
        max_dte=params.max_dte,
        min_rsi=0.0,
        max_rsi=params.max_rsi,
        min_adx=params.min_adx,
        max_adx=params.max_adx,
        precomputed_technicals=precomputed_technicals,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_csp_scanner_integration.py::test_run_csp_scan_does_not_recompute_technicals_live -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All PASS. Pay particular attention to `test_csp_scanner_integration.py` and `test_options_lookup.py` in full — this is the highest-risk change in the plan since it changes what data actually drives the RSI/ADX gate inside `screen_csp_candidates` for the broad-universe scan path.

- [ ] **Step 6: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_csp_scanner_integration.py
git commit -m "fix(csp-scanner): reuse Stage 3 technical indicators instead of recomputing live in screen_csp_candidates"
```

---

## Self-Review Notes (for the implementer)

- After Task 3, verify by reading (not just testing) that `/api/screener/csp` (plain watchlist screener, calls `screen_csp_candidates()` directly with no `precomputed_technicals`) is untouched — it must still hit `_compute_technicals` as before, since it has no Stage 3 pass.
- If `tech_rows` contains a symbol with `row["technical_indicators"]` missing keys `_compute_technicals` normally provides (there shouldn't be any after Task 1, but double check `sma50` and `rsi`/`adx` are always present when `technical_indicators` is non-empty), the pullback_mode gate in options.py must still degrade gracefully (it already treats `None` as "reject" — confirm this stays true).
