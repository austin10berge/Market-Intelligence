# Algo Detective Signal P&L Backtest — Design Spec

**Date:** 2026-07-18
**Status:** Approved

---

## Overview

`validate.py` already scores a candidate GTPro gate (e.g. `adr20_pct_max=4.0`, `sma20_above_sma50`) as a *classifier*: precision/recall against `detective_features`, where `is_prime` marks tickers GTPro is confirmed to have traded. That answers "does this gate correctly flag the days he was active" — it never asks whether *trading* those flagged days would have made money.

This spec adds a companion tool, `src/algo_detective/signal_backtest.py`, that takes the same criteria-dict input and instead simulates a real cash-secured-put wheel trade on every historical date+ticker the gate fires, using GTPro's own documented exit rule (30/50/75% BTC ladder, ~5 DTE), and reports trade-level P&L plus out-of-sample degradation. It reuses MI's existing `src/backtester/` engine rather than building a new one — extended with three small, additive pieces (below), none of which change behavior for any strategy that doesn't use them.

**Question this answers:** "If I ran this gate as my own automated scanner and auto-traded every hit — good picks and false positives alike — would it have made money, and does that hold up out of sample?"

---

## Architecture

```
src/backtester/
├── conditions.py     (MODIFY — new "signal_dates" leaf condition type)
├── models.py          (MODIFY — signal_dates leaf model; profit_ladder field on ExitStrategy)
├── engine.py           (MODIFY — _check_exit gains ladder tiering; _open_position prefers
│                        a joined IV column over recomputed RV20 when present)
└── walk_forward.py     (UNCHANGED — fold-generation helpers reused, not modified)

src/algo_detective/
├── signal_backtest.py  (NEW — orchestration: event extraction, per-ticker backtest calls,
│                        trade pooling, OOS fold aggregation, CLI entry point)
└── validate.py          (UNCHANGED — get_signal_events() is new, sits alongside it, reuses
                          _apply_criteria from analyze.py exactly as validate.py does)
```

No existing call site of `run_backtest`/`run_walk_forward` changes behavior: the new leaf type and `profit_ladder` field are both opt-in.

---

## Components

### 1. `signal_dates` condition leaf (`conditions.py`, `models.py`)

A new leaf alongside `threshold`/`reference`/`crossover`/`pullback`/`consecutive`:

```python
{"type": "signal_dates", "dates": ["2026-06-18", "2026-06-25", ...]}
```

Evaluates true only when `df.index[bar_idx]` (formatted `YYYY-MM-DD`) is in the supplied set. This lets the existing per-bar loop in `run_backtest` open a position on exactly the gate-hit dates for a ticker, without re-deriving indicator logic live — the gate's firing dates are already known from `detective_features`, computed once by `algo_detective`.

### 2. `profit_ladder` on `ExitStrategy` (`models.py`, `engine.py::_check_exit`)

```python
class ProfitLadderTier(BaseModel):
    max_days_held: int
    take_profit_pct: float

class ExitStrategy(BaseModel):
    ...
    profit_ladder: list[ProfitLadderTier] | None = None
```

`_check_exit` checks tiers in order (first tier whose `max_days_held >= days_held` applies) before falling through to the existing flat `take_profit_pct` / `max_hold_days` / expiration logic. Default ladder used by `signal_backtest.py`, derived from the session-27 Reddit findings:

| max_days_held | take_profit_pct |
|---|---|
| 2 | 30 |
| 4 | 50 |
| 5 | 75 |

Existing option-price-based `take_profit_pct` semantics in `_close_position`/`_check_exit` already work correctly for short positions (target = `entry_price * (1 - pct/100)`, i.e. % of premium captured) — confirmed by reading the current implementation. The ladder is purely a time-tiering of that same mechanic; no change to the underlying P&L math.

### 3. IV-with-fallback join (`signal_backtest.py` preprocessing, `engine.py::_open_position` hook)

Live options IV (`detective_options.best_iv`) only exists from 2026-06-21 onward. Before calling `run_backtest`, `signal_backtest.py` joins `best_iv` onto the ticker's OHLCV df as an `iv_override` column wherever available. `_open_position`'s IV lookup prefers `iv_override` at the entry bar when not NaN, else falls back to the engine's existing `rv20` (already computed whenever `options.enabled`). This gives realistic premiums for recent trades and a realized-vol proxy for the multi-year history before live IV collection started — the tradeoff you already approved.

Each simulated `Trade` result should carry which IV source was used (`iv_source: "live" | "rv20_proxy"`), so a fold's stats can be split or at least annotated by proxy vs. real pricing rather than silently blended.

### 4. Orchestration (`signal_backtest.py`)

```python
def get_signal_events(criteria: dict, features: list[dict] | None = None) -> list[dict]:
    """Every (date, ticker) row where _apply_criteria(row, criteria) is True —
    prime AND control, unlike validate.py's TP-only subset."""

def run_signal_backtest(
    criteria: dict,
    target_delta: float = 0.25,
    target_dte: int = 5,
    ladder: list[ProfitLadderTier] = DEFAULT_GTPRO_LADDER,
    start_date: str | None = None,
    end_date: str | None = None,
) -> SignalBacktestResult:
    ...

def run_signal_walk_forward(
    criteria: dict,
    mode: WalkForwardMode = WalkForwardMode.ROLLING,
    in_sample_days: int = 756,
    out_of_sample_days: int = 252,
    **kwargs,
) -> SignalWalkForwardResult:
    ...
```

**`run_signal_backtest` flow:**

1. `get_signal_events(criteria)` — all firings, prime + control (per your call: a live scanner can't distinguish them, so neither should the validation).
2. Group events by ticker.
3. Per ticker: fetch full daily OHLCV from MI's market data store (same source `api/main.py` uses for existing `run_backtest` calls) plus a 20-bar lookback for `rv20` warm-up; join `iv_override`.
4. Build one `StrategyDefinition`: `entry={"type": "signal_dates", "dates": [...]}`, `direction=SHORT`, `options={enabled: True, type: "put", target_delta, target_dte}`, `exit={profit_ladder: ladder}`.
5. Call the existing, unmodified `run_backtest(request, df)` per ticker.
6. Pool all returned `Trade` objects across every ticker.
7. Compute win rate, profit factor, avg P&L/trade, and P&L distribution over the pooled trades (no shared-capital equity curve — per your call, this isn't simulating concurrent portfolio sizing).

**`run_signal_walk_forward` flow:** reuses `walk_forward.py`'s `_generate_folds` (date-window generation is ticker-agnostic — it just needs a total bar count and IS/OOS sizes) to define fold date ranges, filters `get_signal_events()`'s output into each fold's range, runs steps 2–7 above per fold, and reports `_compute_degradation`-style IS/OOS ratios for win rate, profit factor, and avg P&L — the same "is this robust or overfit" read `walk_forward.py` already gives for single-ticker strategies.

---

## Data flow / edge cases

- **IV proxy transition**: recent folds will be mostly `iv_source="live"`, older folds entirely `rv20_proxy`. A degradation ratio spanning that boundary could reflect the pricing-model switch rather than a real strategy problem — reported per-fold IV-source mix alongside stats so this is visible, not hidden.
- **Delta/DTE are tunable defaults, not discovered facts**: GTPro's criteria don't pin an exact strike delta. `target_delta=0.25` and `target_dte=5` are reasonable CSP-wheel defaults (5 DTE matches the median from his trade log); both are function parameters so different assumptions can be swept later without code changes.
- **One position at a time per ticker**: `run_backtest`'s existing pyramiding-disabled default (a new entry signal is ignored while a position is open) already matches wheel behavior — GTPro doesn't stack multiple CSPs on the same name concurrently.
- **No shared capital pool**: pooled trade-level stats don't model portfolio-level position sizing or concurrent-ticker capital constraints (his stated <10%/ticker, <25%/sector rules). Explicitly out of scope per your call — this validates signal quality, not portfolio deployment sizing.

---

## Testing

- Unit tests for the `signal_dates` leaf and `profit_ladder` tiering, in the style of `tests/test_backtester_pyramiding.py` — construct a small synthetic OHLCV df, assert the ladder tier boundaries fire at the right `days_held`, assert `signal_dates` only opens on the specified dates.
- A test for `get_signal_events()` against a small fixture of `detective_features` rows with a known criteria dict, asserting it returns both prime- and control-labeled firings (distinguishing it from `validate_criteria`'s TP-only subset).
- An integration-style test running `run_signal_backtest` end-to-end on a tiny fixture (2-3 tickers, a handful of bars) to confirm the full pipeline (event extraction → per-ticker backtest → pooling → stats) produces sane output without touching the real `detective.db`.

---

## Out of scope (explicitly deferred)

- Full portfolio-level equity curve with concurrent capital allocation/Sharpe across tickers.
- Sweeping/optimizing `target_delta`/`target_dte` — this spec fixes them as defaults; a future pass could grid-search them the same way the session-28/29 gate sweeps already grid-search RSI thresholds.
- Backtesting anything other than the CSP wheel side (no call-side/covered-call signals modeled here).
