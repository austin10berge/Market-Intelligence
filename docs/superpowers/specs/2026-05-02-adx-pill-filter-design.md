# ADX Min/Max Pill Filter — CSP Universe Scanner

**Date:** 2026-05-02
**Status:** Approved

## Summary

Add `min_adx` and `max_adx` as two pill-style filters to the CSP Universe Scanner, operating in Stage 3 (technical conditions pre-filter) alongside the existing RSI pill. ADX(14) is computed from the local OHLCV store's High/Low/Close columns using pandas-ta. Defaults to 15/50 — opinionated, matching the existing watchlist screener's Stage 4 ADX gate.

---

## Backend — `src/screener/csp_scanner.py`

### 1. `_compute_technical_indicators()`

Add ADX(14) computation after the existing RSI block:

```python
adx: float | None = None
if len(hist) >= 28:  # ADX needs 2× period to warm up
    adx_df = ta.adx(hist["High"], hist["Low"], hist["Close"], length=14)
    if adx_df is not None and not adx_df.empty:
        adx_col = [c for c in adx_df.columns if c.upper().startswith("ADX")]
        if adx_col:
            adx = round(float(adx_df[adx_col[0]].iloc[-1]), 2)
```

Add `"adx"` to the returned dict. The local OHLCV store already stores High/Low/Close so no store schema changes are needed.

### 2. `ScannerParams`

Add two new fields with opinionated defaults:

```python
min_adx: float = 15.0
max_adx: float = 50.0
```

Wire through `from_query()` with new optional kwargs `min_adx: float | None` and `max_adx: float | None`, defaulting to `DEFAULT_MIN_ADX` / `DEFAULT_MAX_ADX` when `None`.

Add two new module-level constants:

```python
DEFAULT_MIN_ADX = 15.0
DEFAULT_MAX_ADX = 50.0
```

### 3. `apply_technical_conditions()`

Add ADX gate alongside the existing RSI gate. Filter is active when `min_adx > 0 or max_adx < 100`:

```python
adx_filter_active = params_min_adx > 0 or params_max_adx < 100

adx_passed = True
if adx_filter_active:
    adx_val = indicators.get("adx")
    adx_passed = adx_val is not None and params_min_adx <= adx_val <= params_max_adx
```

Signature change: `apply_technical_conditions(vol_rows, conditions, max_rsi, min_adx, max_adx)` — add `min_adx: float = 15.0` and `max_adx: float = 50.0` after the existing `max_rsi` parameter. The caller in `run_csp_scan()` passes `params.min_adx` and `params.max_adx`. Failed tickers log `adx_range({min}-{max})` in debug output alongside existing RSI failure logging.

---

## API — `src/api/main.py`

Add `min_adx: float | None = None` and `max_adx: float | None = None` query parameters to both:
- `GET /api/screener/csp-scan`
- `DELETE /api/screener/csp-scan`

Pass them through to `ScannerParams.from_query()`. Both endpoints already forward all `ScannerParams` fields — this is a mechanical addition following the existing pattern.

---

## Frontend — `src/web/scanner.js`

### State initialization

Add to `_state.params`:

```js
adx_min: 15,
adx_max: 50,
```

### `PARAM_CONFIG`

Add two new entries after the existing `rsi_max` entry:

```js
{ key: 'adx_min', label: 'ADX ≥', suffix: '', min: 0, max: 100, step: 1, decimals: 0 },
{ key: 'adx_max', label: 'ADX ≤', suffix: '', min: 0, max: 100, step: 1, decimals: 0 },
```

### `_buildQueryString()`

Add `min_adx` and `max_adx` to the `URLSearchParams` object:

```js
min_adx: p.adx_min,
max_adx: p.adx_max,
```

---

## Constraints & Invariants

- **ADX warmup:** Requires ≥ 28 bars (2 × 14-period). Tickers with insufficient history return `adx: null` and are excluded when the ADX filter is active — same fail-safe behaviour as other indicators.
- **No store schema change:** `universe_daily_ohlcv` already stores High/Low/Close.
- **No cache invalidation needed:** `ScannerParams.cache_key_suffix()` hashes all fields including the new ones, so adding `min_adx`/`max_adx` automatically produces new cache keys — no existing cached results are corrupted.
- **Stage 4 ADX gate is unchanged:** The existing ADX gate in `screen_csp_candidates()` (watchlist screener) still runs independently. The new Stage 3 pill is additive.
- **"Disabled" state:** Pills at 0/100 are effectively no-ops. Users can manually set these values to bypass the filter.

---

## What is NOT in scope

- Displaying the computed ADX value per ticker in the results table (the value is used for filtering only).
- Modifying the Stage 4 ADX gate defaults or `get_csp_settings()`.
- Any changes to the backtester ADX logic.
