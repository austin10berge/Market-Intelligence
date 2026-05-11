# ADX Pill Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `min_adx` / `max_adx` pill filters to the CSP Universe Scanner, operating as a Stage 3 pre-filter alongside the existing RSI pill.

**Architecture:** ADX(14) is computed inside `_compute_technical_indicators()` using the High/Low/Close columns already stored in the local OHLCV store. `ScannerParams` gains two new fields (`min_adx`, `max_adx`) wired through the API layer and rendered as two clickable pill badges in the scanner UI.

**Tech Stack:** Python / pandas-ta (ADX), FastAPI query params, vanilla JS pill badge pattern (already in `scanner.js`).

---

## File Map

| File | Change |
|------|--------|
| `src/screener/csp_scanner.py` | Add ADX to `_compute_technical_indicators`; add `min_adx`/`max_adx` to `ScannerParams` + `from_query`; update `apply_technical_conditions` signature + ADX gate; update `run_csp_scan` call |
| `src/api/main.py` | Add `min_adx`/`max_adx` query params to GET + DELETE `/api/screener/csp-scan` |
| `src/web/scanner.js` | Add `adx_min`/`adx_max` to `_state.params`, `PARAM_CONFIG`, `_buildQueryString` |
| `tests/test_csp_scanner_conditions.py` | Add tests for ADX indicator computation, `ScannerParams` defaults, and ADX gate behaviour |

---

## Task 1: ADX computation in `_compute_technical_indicators`

**Files:**
- Modify: `src/screener/csp_scanner.py` (`_compute_technical_indicators` function, ~line 464)
- Test: `tests/test_csp_scanner_conditions.py` (`TestComputeTechnicalIndicators` class)

- [ ] **Step 1.1: Write the failing tests**

Add to the `TestComputeTechnicalIndicators` class in `tests/test_csp_scanner_conditions.py`:

```python
def test_contains_adx_key(self):
    hist = _make_hist(n=250)
    result = _compute_technical_indicators("TEST", hist)
    assert result is not None
    assert "adx" in result

def test_adx_is_float_for_sufficient_history(self):
    hist = _make_hist(n=250)
    result = _compute_technical_indicators("TEST", hist)
    assert result is not None
    assert isinstance(result["adx"], float)

def test_adx_in_valid_range(self):
    """ADX is always 0–100."""
    hist = _make_hist(n=250)
    result = _compute_technical_indicators("TEST", hist)
    assert result is not None
    assert result["adx"] is not None
    assert 0.0 <= result["adx"] <= 100.0
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```bash
pytest tests/test_csp_scanner_conditions.py::TestComputeTechnicalIndicators::test_contains_adx_key tests/test_csp_scanner_conditions.py::TestComputeTechnicalIndicators::test_adx_is_float_for_sufficient_history tests/test_csp_scanner_conditions.py::TestComputeTechnicalIndicators::test_adx_in_valid_range -v
```

Expected: FAIL — `AssertionError: 'adx' not in result` (key absent)

- [ ] **Step 1.3: Add ADX computation to `_compute_technical_indicators`**

In `src/screener/csp_scanner.py`, find `_compute_technical_indicators`. After the RSI block (around line 499–503) and before the `return` dict, add:

```python
        # ADX(14) — needs High/Low/Close; 2× period (28 bars) to warm up
        adx: float | None = None
        if len(hist) >= 28:
            adx_df = ta.adx(hist["High"], hist["Low"], hist["Close"], length=14)
            if adx_df is not None and not adx_df.empty:
                adx_col = [c for c in adx_df.columns if c.upper().startswith("ADX")]
                if adx_col:
                    adx = round(float(adx_df[adx_col[0]].iloc[-1]), 2)
```

Then add `"adx": adx` to the returned dict so it reads:

```python
        return {
            "price":               round(last_price, 2),
            "sma20":               round(sma20, 2)  if sma20  is not None else None,
            "sma50":               round(sma50, 2)  if sma50  is not None else None,
            "sma200":              round(sma200, 2) if sma200 is not None else None,
            "bb_lower":            round(bb_lower, 2) if bb_lower is not None else None,
            "bb_pct_from_lower":   bb_pct_from_lower,
            "rsi":                 round(rsi, 2) if rsi is not None else None,
            "adx":                 adx,
        }
```

- [ ] **Step 1.4: Run the new tests**

```bash
pytest tests/test_csp_scanner_conditions.py::TestComputeTechnicalIndicators -v
```

Expected: all pass.

- [ ] **Step 1.5: Run the full test suite to check for regressions**

```bash
pytest tests/test_csp_scanner_conditions.py -v
```

Expected: all pass.

- [ ] **Step 1.6: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_csp_scanner_conditions.py
git commit -m "feat(scanner): compute ADX(14) in Stage 3 technical indicators"
```

---

## Task 2: Add `min_adx` / `max_adx` to `ScannerParams`

**Files:**
- Modify: `src/screener/csp_scanner.py` (constants block ~line 43; `ScannerParams` class ~line 126)
- Test: `tests/test_csp_scanner_conditions.py` (new `TestScannerParamsAdx` class)

- [ ] **Step 2.1: Write the failing tests**

Add a new class at the end of `tests/test_csp_scanner_conditions.py`:

```python
class TestScannerParamsAdx:
    def test_default_min_adx(self):
        assert ScannerParams().min_adx == 15.0

    def test_default_max_adx(self):
        assert ScannerParams().max_adx == 50.0

    def test_from_query_uses_defaults_when_none(self):
        p = ScannerParams.from_query()
        assert p.min_adx == 15.0
        assert p.max_adx == 50.0

    def test_from_query_accepts_min_adx(self):
        p = ScannerParams.from_query(min_adx=20.0)
        assert p.min_adx == 20.0

    def test_from_query_accepts_max_adx(self):
        p = ScannerParams.from_query(max_adx=40.0)
        assert p.max_adx == 40.0

    def test_cache_key_changes_with_adx_params(self):
        p1 = ScannerParams(min_adx=15.0, max_adx=50.0)
        p2 = ScannerParams(min_adx=20.0, max_adx=50.0)
        assert p1.cache_key_suffix() != p2.cache_key_suffix()
```

- [ ] **Step 2.2: Run tests to confirm they fail**

```bash
pytest tests/test_csp_scanner_conditions.py::TestScannerParamsAdx -v
```

Expected: FAIL — `AttributeError: ScannerParams has no field min_adx`

- [ ] **Step 2.3: Add constants and fields**

In `src/screener/csp_scanner.py`, add two constants after the existing defaults block (around line 51):

```python
DEFAULT_MIN_ADX          = 15.0
DEFAULT_MAX_ADX          = 50.0
```

In the `ScannerParams` dataclass, add two fields after `max_rsi`:

```python
    min_adx:          float = DEFAULT_MIN_ADX
    max_adx:          float = DEFAULT_MAX_ADX
```

In `from_query`, add two new parameters after `max_rsi`:

```python
        min_adx: float | None = None,
        max_adx: float | None = None,
```

And add them to the `return cls(...)` call:

```python
            min_adx          = min_adx  if min_adx  is not None else DEFAULT_MIN_ADX,
            max_adx          = max_adx  if max_adx  is not None else DEFAULT_MAX_ADX,
```

- [ ] **Step 2.4: Run the new tests**

```bash
pytest tests/test_csp_scanner_conditions.py::TestScannerParamsAdx -v
```

Expected: all pass.

- [ ] **Step 2.5: Run the full test suite**

```bash
pytest tests/test_csp_scanner_conditions.py -v
```

Expected: all pass.

- [ ] **Step 2.6: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_csp_scanner_conditions.py
git commit -m "feat(scanner): add min_adx/max_adx to ScannerParams with defaults 15/50"
```

---

## Task 3: ADX gate in `apply_technical_conditions`

**Files:**
- Modify: `src/screener/csp_scanner.py` (`apply_technical_conditions` ~line 560; `run_csp_scan` call ~line 693)
- Test: `tests/test_csp_scanner_conditions.py` (new `TestAdxGate` class)

- [ ] **Step 3.1: Write the failing tests**

Add a new class at the end of `tests/test_csp_scanner_conditions.py`:

```python
class TestAdxGate:
    """ADX min/max gate in apply_technical_conditions."""

    def _fake_indicators_with_adx(self, adx: float | None) -> dict:
        return {
            "price": 100.0, "sma20": 95.0, "sma50": 90.0, "sma200": 80.0,
            "bb_lower": 85.0, "bb_pct_from_lower": 15.0, "rsi": 40.0,
            "adx": adx,
        }

    def test_adx_gate_inactive_at_zero_to_100(self):
        """min_adx=0, max_adx=100 never filters anything — pass-through."""
        rows = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        tickers, _ = apply_technical_conditions(rows, conditions=[], min_adx=0.0, max_adx=100.0)
        assert tickers == ["AAPL", "MSFT"]

    def test_adx_gate_passes_ticker_in_range(self):
        from unittest.mock import patch, MagicMock
        fake_ind = self._fake_indicators_with_adx(25.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch("src.screener.csp_scanner._compute_technical_indicators", return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, conditions=[], min_adx=15.0, max_adx=50.0)
        assert "TEST" in tickers

    def test_adx_gate_blocks_ticker_below_min(self):
        from unittest.mock import patch, MagicMock
        fake_ind = self._fake_indicators_with_adx(10.0)  # below min_adx=15
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch("src.screener.csp_scanner._compute_technical_indicators", return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, conditions=[], min_adx=15.0, max_adx=50.0)
        assert "TEST" not in tickers

    def test_adx_gate_blocks_ticker_above_max(self):
        from unittest.mock import patch, MagicMock
        fake_ind = self._fake_indicators_with_adx(60.0)  # above max_adx=50
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch("src.screener.csp_scanner._compute_technical_indicators", return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, conditions=[], min_adx=15.0, max_adx=50.0)
        assert "TEST" not in tickers

    def test_adx_gate_passes_at_exact_min(self):
        """Boundary: ADX == min_adx should pass (inclusive)."""
        from unittest.mock import patch, MagicMock
        fake_ind = self._fake_indicators_with_adx(15.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch("src.screener.csp_scanner._compute_technical_indicators", return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, conditions=[], min_adx=15.0, max_adx=50.0)
        assert "TEST" in tickers

    def test_adx_gate_passes_at_exact_max(self):
        """Boundary: ADX == max_adx should pass (inclusive)."""
        from unittest.mock import patch, MagicMock
        fake_ind = self._fake_indicators_with_adx(50.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch("src.screener.csp_scanner._compute_technical_indicators", return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, conditions=[], min_adx=15.0, max_adx=50.0)
        assert "TEST" in tickers

    def test_adx_gate_blocks_when_adx_none(self):
        """If ADX cannot be computed, the ticker is dropped when filter is active."""
        from unittest.mock import patch, MagicMock
        fake_ind = self._fake_indicators_with_adx(None)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch("src.screener.csp_scanner._compute_technical_indicators", return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, conditions=[], min_adx=15.0, max_adx=50.0)
        assert "TEST" not in tickers
```

- [ ] **Step 3.2: Run tests to confirm they fail**

```bash
pytest tests/test_csp_scanner_conditions.py::TestAdxGate -v
```

Expected: FAIL — `TypeError: apply_technical_conditions() got unexpected keyword argument 'min_adx'`

- [ ] **Step 3.3: Update `apply_technical_conditions` signature and gate logic**

Replace the function signature in `src/screener/csp_scanner.py` from:

```python
def apply_technical_conditions(
    vol_rows: list[dict],
    conditions: list[str],
    max_rsi: float = 100.0,
) -> tuple[list[str], list[dict]]:
```

to:

```python
def apply_technical_conditions(
    vol_rows: list[dict],
    conditions: list[str],
    max_rsi: float = 100.0,
    min_adx: float = 0.0,
    max_adx: float = 100.0,
) -> tuple[list[str], list[dict]]:
```

Replace the early-return guard from:

```python
    rsi_filter_active = max_rsi < 100.0
    if not conditions and not rsi_filter_active:
        # No conditions active — pass everyone through
        tickers = [r["symbol"] for r in vol_rows]
        for row in vol_rows:
            row["technical_conditions"] = {}
        return tickers, vol_rows
```

to:

```python
    rsi_filter_active = max_rsi < 100.0
    adx_filter_active = min_adx > 0 or max_adx < 100.0
    if not conditions and not rsi_filter_active and not adx_filter_active:
        # No conditions active — pass everyone through
        tickers = [r["symbol"] for r in vol_rows]
        for row in vol_rows:
            row["technical_conditions"] = {}
        return tickers, vol_rows
```

Update the logger line from:

```python
    logger.info("Technical conditions filter: %d tickers, %d active conditions: %s",
                len(vol_rows), len(conditions), conditions)
```

to:

```python
    logger.info(
        "Technical conditions filter: %d tickers, %d conditions: %s, RSI<%.0f, ADX %.0f-%.0f",
        len(vol_rows), len(conditions), conditions, max_rsi, min_adx, max_adx,
    )
```

After the existing RSI gate block (which ends with `logger.debug("Conditions failed for %s: %s", ...)`), add the ADX gate. The full updated gate logic block inside the per-ticker loop (replacing the section from `all_passed, results = ...` through the end of the if/else) reads:

```python
        all_passed, results = _check_conditions(indicators, conditions)

        rsi_passed = True
        if rsi_filter_active:
            rsi_val = indicators.get("rsi")
            rsi_passed = rsi_val is not None and rsi_val < max_rsi

        adx_passed = True
        if adx_filter_active:
            adx_val = indicators.get("adx")
            adx_passed = adx_val is not None and min_adx <= adx_val <= max_adx

        row["technical_indicators"] = indicators
        row["technical_conditions"] = results

        if all_passed and rsi_passed and adx_passed:
            passing_tickers.append(symbol)
            passing_rows.append(row)
        else:
            failed = [k for k, v in results.items() if not v]
            if not rsi_passed:
                failed.append(f"rsi_max({max_rsi})")
            if not adx_passed:
                failed.append(f"adx_range({min_adx}-{max_adx})")
            logger.debug("Conditions failed for %s: %s", symbol, failed)
```

- [ ] **Step 3.4: Update the `run_csp_scan` call to pass new params**

In `run_csp_scan`, find the call to `apply_technical_conditions` (around line 693):

```python
    tech_passing, tech_rows = apply_technical_conditions(vol_rows, params.conditions, params.max_rsi)
```

Replace with:

```python
    tech_passing, tech_rows = apply_technical_conditions(
        vol_rows, params.conditions, params.max_rsi, params.min_adx, params.max_adx
    )
```

- [ ] **Step 3.5: Run the new ADX gate tests**

```bash
pytest tests/test_csp_scanner_conditions.py::TestAdxGate -v
```

Expected: all pass.

- [ ] **Step 3.6: Run the full test suite**

```bash
pytest tests/test_csp_scanner_conditions.py -v
```

Expected: all pass — existing RSI and pass-through tests must still pass.

- [ ] **Step 3.7: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_csp_scanner_conditions.py
git commit -m "feat(scanner): add ADX min/max gate to Stage 3 technical conditions filter"
```

---

## Task 4: API endpoint — expose `min_adx` / `max_adx` as query params

**Files:**
- Modify: `src/api/main.py` (GET `/api/screener/csp-scan` ~line 304; DELETE ~line 374)

- [ ] **Step 4.1: Add `min_adx` and `max_adx` to the GET endpoint**

In `src/api/main.py`, find the `get_csp_scan_candidates` function signature and add the two new params after `max_rsi`:

```python
async def get_csp_scan_candidates(
    min_cap:    float | None = None,
    max_price:  float | None = None,
    min_beta:   float | None = None,
    max_beta:   float | None = None,
    min_vol:    float | None = None,
    max_rsi:    float | None = None,
    min_adx:    float | None = None,
    max_adx:    float | None = None,
    min_dte:    int   | None = None,
    max_dte:    int   | None = None,
    conditions: str   | None = None,
):
```

Then update the `ScannerParams.from_query(...)` call inside the function body to include the two new params:

```python
    params = ScannerParams.from_query(
        min_cap=min_cap,
        max_price=max_price,
        min_beta=min_beta,
        max_beta=max_beta,
        min_vol=min_vol,
        max_rsi=max_rsi,
        min_adx=min_adx,
        max_adx=max_adx,
        min_dte=min_dte,
        max_dte=max_dte,
        conditions=conditions,
    )
```

- [ ] **Step 4.2: Add `min_adx` and `max_adx` to the DELETE endpoint**

Find the `invalidate_csp_scan_cache` function and apply the same two changes (signature + `from_query` call):

```python
async def invalidate_csp_scan_cache(
    min_cap:    float | None = None,
    max_price:  float | None = None,
    min_beta:   float | None = None,
    max_beta:   float | None = None,
    min_vol:    float | None = None,
    max_rsi:    float | None = None,
    min_adx:    float | None = None,
    max_adx:    float | None = None,
    min_dte:    int   | None = None,
    max_dte:    int   | None = None,
    conditions: str   | None = None,
):
```

```python
    params = ScannerParams.from_query(
        min_cap=min_cap, max_price=max_price,
        min_beta=min_beta, max_beta=max_beta,
        min_vol=min_vol, max_rsi=max_rsi,
        min_adx=min_adx, max_adx=max_adx,
        min_dte=min_dte, max_dte=max_dte,
        conditions=conditions,
    )
```

- [ ] **Step 4.3: Run the full test suite**

```bash
pytest -v
```

Expected: all pass.

- [ ] **Step 4.4: Commit**

```bash
git add src/api/main.py
git commit -m "feat(api): expose min_adx/max_adx query params on CSP scan endpoints"
```

---

## Task 5: Frontend — add ADX pill badges

**Files:**
- Modify: `src/web/scanner.js`

- [ ] **Step 5.1: Add ADX state**

In `src/web/scanner.js`, find `_state.params` (around line 23). Add two new keys after `rsi_max`:

```js
        rsi_max:   50,
        adx_min:   15,
        adx_max:   50,
        dte_min:   3,
```

- [ ] **Step 5.2: Add ADX pill config**

In `PARAM_CONFIG` (around line 57), add two entries after the `rsi_max` entry:

```js
    { key: 'rsi_max',   label: 'RSI <',      suffix: '',   min: 10,  max: 100,  step: 1,   decimals: 0 },
    { key: 'adx_min',   label: 'ADX ≥',      suffix: '',   min: 0,   max: 100,  step: 1,   decimals: 0 },
    { key: 'adx_max',   label: 'ADX ≤',      suffix: '',   min: 0,   max: 100,  step: 1,   decimals: 0 },
    { key: 'dte_min',   label: 'DTE ≥',       suffix: 'd',  min: 1,   max: 90,   step: 1,   decimals: 0 },
```

- [ ] **Step 5.3: Add ADX to query string builder**

In `_buildQueryString()` (around line 258), add `min_adx` and `max_adx` to the `URLSearchParams` object:

```js
    const qs = new URLSearchParams({
        min_cap:   p.min_cap,
        max_price: p.max_price,
        min_beta:  p.min_beta,
        max_beta:  p.max_beta,
        min_vol:   p.min_vol,
        max_rsi:   p.rsi_max,
        min_adx:   p.adx_min,
        max_adx:   p.adx_max,
        min_dte:   p.dte_min,
        max_dte:   p.dte_max,
    });
```

- [ ] **Step 5.4: Verify the pills render in the browser**

Start the dev server (or use Docker):

```bash
docker compose up api dashboard
```

Open the scanner page (`http://localhost` or the configured URL). Confirm:
- Two new pill badges appear: `ADX ≥ 15` and `ADX ≤ 50`
- Clicking a badge opens the inline number input
- Changing a value and pressing Enter updates the badge display
- Running a scan includes `min_adx` and `max_adx` in the network request (check DevTools → Network tab)
- Force-rescan (DELETE) also sends the ADX params

- [ ] **Step 5.5: Commit**

```bash
git add src/web/scanner.js
git commit -m "feat(ui): add ADX min/max pill badges to CSP universe scanner"
```

---

## Self-Review Checklist

- [x] Spec §1 (`_compute_technical_indicators` ADX) → Task 1
- [x] Spec §2 (`ScannerParams` fields + defaults + `from_query`) → Task 2
- [x] Spec §3 (`apply_technical_conditions` signature + gate + caller update) → Task 3
- [x] Spec §API → Task 4
- [x] Spec §Frontend → Task 5
- [x] Spec constraint: "no store schema change" — satisfied; High/Low/Close already in `universe_daily_ohlcv`
- [x] Spec constraint: "cache key changes with new params" — satisfied; `cache_key_suffix()` hashes all `asdict(self)` fields
- [x] Spec constraint: "Stage 4 ADX gate unchanged" — satisfied; `screen_csp_candidates` not touched
- [x] Type consistency: `min_adx: float` / `max_adx: float` used consistently across all tasks
- [x] No placeholders
