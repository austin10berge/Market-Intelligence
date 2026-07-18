# Algo Detective Signal P&L Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `src/algo_detective/signal_backtest.py`, a repeatable tool that simulates real CSP wheel trades (via the existing `src/backtester/` engine) on every historical date+ticker where a candidate GTPro gate criteria fires, and reports pooled trade-level P&L plus out-of-sample degradation — answering "would trading this gate have made money," not just the precision/recall `validate.py` already answers.

**Architecture:** Three small, additive extensions to `src/backtester/` (a new `signal_dates` condition leaf, a `profit_ladder` time-tiered take-profit exit, and an `iv_override` column hook for real-vs-proxy IV) let the existing single-ticker `run_backtest` engine be driven by known signal dates instead of live indicator evaluation. A new orchestration module in `src/algo_detective/` extracts signal events, calls the engine once per ticker, pools the resulting trades, and reuses the backtester's existing walk-forward fold logic for IS/OOS reporting.

**Tech Stack:** Python 3.12, pandas, Pydantic (existing `src/backtester/models.py` conventions), pytest.

**Spec:** `docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md`

## Global Constraints

- Reuse the existing `src/backtester/` engine — no new backtest engine. All changes to it must be additive/backward-compatible (existing strategies without the new fields behave identically).
- Signal events = **all** gate firings, prime + control labeled tickers (not just true positives) — a live scanner can't distinguish them.
- Aggregation = **pooled trade-level stats only** (win rate, profit factor, avg P&L, P&L distribution) — no shared-capital portfolio equity curve.
- IV source: real `detective_options.best_iv` where available (dates ≥ `2026-06-21`), else the engine's existing RV20 proxy.
- `target_delta=0.25` and `target_dte=5` are tunable defaults, not discovered facts from GTPro's criteria.
- Python 3.12, no local virtualenv — run tests via `docker compose run --rm test python3 -m pytest tests/... `. A `PostToolUse` hook auto-runs ruff on every edited `.py` file — no manual format step.
- All new modules use `from __future__ import annotations` per existing repo convention.

---

## File Structure

```
src/backtester/
├── conditions.py     (MODIFY — new "signal_dates" leaf condition type)
├── models.py          (MODIFY — SignalDatesCondition, ProfitLadderTier, ExitStrategy.profit_ladder)
└── engine.py           (MODIFY — _check_exit/_close_position ladder tiering via new
                          _resolve_ladder_tier helper; _open_position prefers iv_override column)

src/algo_detective/
├── signal_events.py    (NEW — get_signal_events(): every gate-hit date+ticker, prime+control)
└── signal_backtest.py  (NEW — orchestration: per-ticker backtest calls, trade pooling,
                          walk-forward fold aggregation, CLI entry point)

tests/
├── test_backtester_signal_dates.py       (NEW — Task 1)
├── test_backtester_profit_ladder.py      (NEW — Task 2)
├── test_backtester_iv_override.py        (NEW — Task 3)
├── test_algo_detective_signal_events.py  (NEW — Task 4)
├── test_algo_detective_signal_backtest.py (NEW — Tasks 5, 6, 8)
└── test_algo_detective_signal_walk_forward.py (NEW — Task 7)
```

---

### Task 1: `signal_dates` condition leaf

**Files:**
- Modify: `src/backtester/conditions.py`
- Modify: `src/backtester/models.py`
- Test: `tests/test_backtester_signal_dates.py`

**Interfaces:**
- Produces: a new leaf condition type usable anywhere `evaluate_condition_tree`/`StrategyDefinition.entry` accepts a condition dict: `{"type": "signal_dates", "dates": ["2026-01-02", ...]}` — true only on bars whose date is in the set.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtester_signal_dates.py`:

```python
"""Tests for the signal_dates condition leaf in src/backtester/conditions.py.

Lets a backtest open a position on exactly the historical dates a gate
criteria fired for a ticker, rather than re-evaluating indicator logic
live — see docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

import pandas as pd

from src.backtester.conditions import evaluate_condition_tree


def _make_df(start: str = "2024-01-02", periods: int = 5) -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=periods, freq="B")
    return pd.DataFrame(
        {
            "Open": [100.0] * periods,
            "High": [101.0] * periods,
            "Low": [99.0] * periods,
            "Close": [100.0] * periods,
            "Volume": [1_000_000] * periods,
        },
        index=dates,
    )


class TestSignalDatesCondition:
    def test_true_on_matching_date(self):
        df = _make_df()
        tree = {"type": "signal_dates", "dates": ["2024-01-03"]}
        assert evaluate_condition_tree(tree, df, 1) is True  # bar_idx 1 == 2024-01-03

    def test_false_on_non_matching_date(self):
        df = _make_df()
        tree = {"type": "signal_dates", "dates": ["2024-01-03"]}
        assert evaluate_condition_tree(tree, df, 0) is False  # bar_idx 0 == 2024-01-02

    def test_false_when_dates_list_empty(self):
        df = _make_df()
        tree = {"type": "signal_dates", "dates": []}
        assert evaluate_condition_tree(tree, df, 0) is False

    def test_matches_multiple_dates_across_bars(self):
        df = _make_df(periods=5)
        tree = {"type": "signal_dates", "dates": ["2024-01-02", "2024-01-04"]}
        results = [evaluate_condition_tree(tree, df, i) for i in range(5)]
        assert results == [True, False, True, False, False]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_signal_dates.py -v`
Expected: FAIL — `Unknown condition type 'signal_dates'` (returns `False` from `_evaluate_leaf`'s else branch), so `test_true_on_matching_date` and `test_matches_multiple_dates_across_bars` fail their assertions.

- [ ] **Step 3: Implement the leaf**

In `src/backtester/conditions.py`, add a new evaluator function near the other `_eval_*` functions (after `_eval_consecutive`):

```python
def _eval_signal_dates(cond: dict, df: pd.DataFrame, bar_idx: int) -> bool:
    """True when the current bar's date is in the supplied date set —
    used to replay known historical gate-hit dates instead of
    re-evaluating live indicator logic."""
    dates = set(cond.get("dates", []))
    bar_date = df.index[bar_idx].strftime("%Y-%m-%d")
    return bar_date in dates
```

In `_evaluate_leaf`, add a branch (in the `if/elif` chain that dispatches on `cond_type`, right after the `consecutive` branch and before the `else`):

```python
        elif cond_type == "consecutive":
            return _eval_consecutive(cond, df, bar_idx)
        elif cond_type == "signal_dates":
            return _eval_signal_dates(cond, df, bar_idx)
        else:
```

In `src/backtester/models.py`, add a matching Pydantic model after `ConsecutiveCondition` (for documentation parity with the other leaf types — none of these are actually parsed at runtime, the evaluator works off raw dicts, but every other leaf type has one):

```python
class SignalDatesCondition(BaseModel):
    """True only on bars whose date is in the supplied set (e.g. known
    algo_detective gate-fire dates for a ticker)."""
    type: str = "signal_dates"
    dates: list[str]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_signal_dates.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/backtester/conditions.py src/backtester/models.py tests/test_backtester_signal_dates.py
git commit -m "feat(backtester): add signal_dates condition leaf"
```

---

### Task 2: `profit_ladder` time-tiered take-profit exit

**Files:**
- Modify: `src/backtester/models.py`
- Modify: `src/backtester/engine.py`
- Test: `tests/test_backtester_profit_ladder.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `ProfitLadderTier(max_days_held: int, take_profit_pct: float)`; `ExitStrategy.profit_ladder: list[ProfitLadderTier] | None`; a `_resolve_ladder_tier(exit_config, days_held) -> ProfitLadderTier | None` helper in `engine.py` used by both `_check_exit` and `_close_position`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtester_profit_ladder.py`:

```python
"""Tests for the profit_ladder time-tiered take-profit exit in
src/backtester/engine.py, modeling GTPro's 30/50/75%-by-day BTC rule.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.

Uses plain (non-option) short equity positions so the ladder's own
tiering logic is tested independently of options pricing.
"""
from __future__ import annotations

import pandas as pd

from src.backtester.engine import run_backtest
from src.backtester.models import (
    BacktestRequest,
    Direction,
    ExitStrategy,
    ProfitLadderTier,
    StrategyDefinition,
)


def _make_df(closes: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.02 for c in closes],
            "Low": [c * 0.98 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=dates,
    )


def _entry_on_first_bar() -> dict:
    return {
        "operator": "AND",
        "conditions": [
            {"type": "threshold", "indicator": {"name": "CLOSE", "params": {}}, "comparator": "gt", "value": 0.0},
        ],
    }


LADDER = [
    ProfitLadderTier(max_days_held=2, take_profit_pct=30.0),
    ProfitLadderTier(max_days_held=4, take_profit_pct=50.0),
    ProfitLadderTier(max_days_held=5, take_profit_pct=75.0),
]


def _run(closes: list[float], exit_strategy: ExitStrategy) -> list:
    strategy = StrategyDefinition(entry=_entry_on_first_bar(), direction=Direction.SHORT, exit=exit_strategy)
    request = BacktestRequest(strategy=strategy, ticker="TEST")
    result = run_backtest(request, _make_df(closes))
    return result.trades


class TestProfitLadder:
    def test_exits_at_early_tier_when_price_crashes_immediately(self):
        """Short position: a sharp price drop right after entry should
        hit the day-2 tier's 30% target within 2 bars, not ride further."""
        closes = [100.0, 65.0, 65.0, 65.0, 65.0, 65.0]
        trades = _run(closes, ExitStrategy(profit_ladder=LADDER))
        assert len(trades) == 1
        assert trades[0].exit_reason == "take_profit"
        assert trades[0].bars_held <= 2

    def test_does_not_exit_before_any_tier_threshold_reached(self):
        """Price never drops enough for any tier -> rides to end of data."""
        closes = [100.0] * 6
        trades = _run(closes, ExitStrategy(profit_ladder=LADDER))
        assert trades[0].exit_reason == "end_of_data"

    def test_fill_price_uses_matched_tier_pct_not_flat_take_profit(self):
        """When both profit_ladder and a flat take_profit_pct are set,
        the ladder's tier percentage must govern the fill, not the flat one."""
        closes = [100.0, 65.0, 65.0, 65.0, 65.0, 65.0]
        trades = _run(closes, ExitStrategy(take_profit_pct=99.0, profit_ladder=LADDER))
        trade = trades[0]
        assert trade.exit_reason == "take_profit"
        expected_fill = round(trade.entry_price * (1 - 30.0 / 100.0), 4)
        assert trade.exit_price == expected_fill
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_profit_ladder.py -v`
Expected: FAIL — `ProfitLadderTier` and `ExitStrategy.profit_ladder` don't exist yet (ImportError / `TypeError: unexpected keyword argument 'profit_ladder'`).

- [ ] **Step 3: Implement the ladder**

In `src/backtester/models.py`, add after `PyramidingExitMode`/before `ExitStrategy` (or directly above `ExitStrategy` — anywhere before its use):

```python
class ProfitLadderTier(BaseModel):
    """One tier of a time-based profit-taking ladder: BTC at
    take_profit_pct once the position has been held for at least
    max_days_held bars. Tiers should be ordered ascending by
    max_days_held."""
    max_days_held: int
    take_profit_pct: float
```

Modify `ExitStrategy` (currently at `src/backtester/models.py:113-121`) to add the new field:

```python
class ExitStrategy(BaseModel):
    """All exit conditions for a strategy."""
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    max_hold_days: int | None = None
    # Indicator-based exit conditions (same tree structure as entry)
    conditions: dict[str, Any] | None = None
    pyramiding_exit_mode: PyramidingExitMode = PyramidingExitMode.SELL_ALL
    # Time-tiered take-profit schedule (e.g. GTPro's 30/50/75% BTC rule).
    # The first tier whose max_days_held covers the current days_held
    # applies instead of the flat take_profit_pct above.
    profit_ladder: list[ProfitLadderTier] | None = None
```

In `src/backtester/engine.py`, add a shared helper right before `_check_exit` (around line 471):

```python
def _resolve_ladder_tier(exit_config, days_held: int) -> "ProfitLadderTier | None":
    """Return the first profit_ladder tier covering days_held, or None
    if no ladder is configured or none apply yet. Shared by _check_exit
    (decides whether to exit) and _close_position (picks the fill price)
    so both always agree on which tier fired."""
    if not exit_config.profit_ladder:
        return None
    return next(
        (t for t in exit_config.profit_ladder if t.max_days_held >= days_held),
        None,
    )
```

Modify `_check_exit`'s take-profit block (currently `if exit_config.take_profit_pct is not None:` around line 491) to check the ladder first:

```python
    if exit_config.profit_ladder:
        tier = _resolve_ladder_tier(exit_config, bar_idx - pos.entry_bar_idx)
        if tier is not None:
            if pos.direction == "long":
                target = pos.entry_price * (1 + tier.take_profit_pct / 100.0)
                if high >= target:
                    return "take_profit"
            else:
                target = pos.entry_price * (1 - tier.take_profit_pct / 100.0)
                if low <= target:
                    return "take_profit"
    elif exit_config.take_profit_pct is not None:
        if pos.direction == "long":
            target = pos.entry_price * (1 + exit_config.take_profit_pct / 100.0)
            if high >= target:
                return "take_profit"
        else:
            target = pos.entry_price * (1 - exit_config.take_profit_pct / 100.0)
            if low <= target:
                return "take_profit"
```

Modify `_close_position`'s take-profit fill-price block (currently `if reason == "take_profit" and request.strategy.exit.take_profit_pct is not None:` around line 404) so the fill price matches whichever tier actually fired:

```python
    if reason == "take_profit":
        days_held = bar_idx - pos.entry_bar_idx
        tier = _resolve_ladder_tier(request.strategy.exit, days_held)
        effective_pct = tier.take_profit_pct if tier is not None else request.strategy.exit.take_profit_pct
        if effective_pct is not None:
            if pos.direction == "long":
                fill_price = pos.entry_price * (1 + effective_pct / 100.0)
            else:
                fill_price = pos.entry_price * (1 - effective_pct / 100.0)
    elif reason == "stop_loss" and request.strategy.exit.stop_loss_pct is not None:
```

(Keep the rest of the `elif` chain — `stop_loss`, `trailing_stop`, and the final `else` slippage branch — unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_profit_ladder.py -v`
Expected: PASS (3 passed)

Also re-run the pre-existing suite to confirm no regression:
Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_pyramiding.py -v`
Expected: PASS (all previously passing tests still pass — `profit_ladder` defaults to `None`, so the `elif exit_config.take_profit_pct is not None:` / `elif reason == "stop_loss"...` branches are unchanged for any strategy that doesn't set it).

- [ ] **Step 5: Commit**

```bash
git add src/backtester/models.py src/backtester/engine.py tests/test_backtester_profit_ladder.py
git commit -m "feat(backtester): add time-tiered profit_ladder exit"
```

---

### Task 3: `iv_override` column hook for real-vs-proxy IV

**Files:**
- Modify: `src/backtester/engine.py`
- Test: `tests/test_backtester_iv_override.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `_open_position` prefers a per-bar `df["iv_override"]` value over the recomputed `rv20` at the entry bar, when not NaN, before falling back to the existing `0.30` default. No signature changes — purely a df-column convention any caller can opt into.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtester_iv_override.py`:

```python
"""Tests for the iv_override column hook in _open_position
(src/backtester/engine.py) — lets a caller-supplied real IV (e.g.
algo_detective's joined detective_options.best_iv) take precedence
over the recomputed realized-vol proxy at entry.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

import pandas as pd

from src.backtester.engine import run_backtest
from src.backtester.models import BacktestRequest, Direction, OptionsConfig, StrategyDefinition


def _entry_when_close_above(threshold: float) -> dict:
    return {
        "operator": "AND",
        "conditions": [
            {"type": "threshold", "indicator": {"name": "CLOSE", "params": {}}, "comparator": "gt", "value": threshold},
        ],
    }


def _options_strategy(entry: dict) -> StrategyDefinition:
    return StrategyDefinition(
        entry=entry,
        direction=Direction.SHORT,
        options=OptionsConfig(enabled=True, type="put", target_delta=0.25, target_dte=5),
    )


class TestIvOverride:
    def test_uses_iv_override_at_entry_when_present(self):
        closes = [100.0] * 25
        dates = pd.date_range("2024-01-02", periods=25, freq="B")
        df = pd.DataFrame(
            {
                "Open": closes, "High": [c * 1.01 for c in closes],
                "Low": [c * 0.99 for c in closes], "Close": closes,
                "Volume": [1_000_000] * 25,
                "iv_override": [0.80] + [None] * 24,
            },
            index=dates,
        )
        request = BacktestRequest(strategy=_options_strategy(_entry_when_close_above(0.0)), ticker="TEST")
        result = run_backtest(request, df)
        assert result.trades[0].option_iv_entry == 0.80

    def test_falls_back_to_default_when_neither_override_nor_rv20_present(self):
        """Entry at bar 0, before the rv20 rolling window has filled in
        (needs 20 bars) -> falls back to the pre-existing 0.30 default,
        same as before this change."""
        closes = [100.0] * 25
        dates = pd.date_range("2024-01-02", periods=25, freq="B")
        df = pd.DataFrame(
            {
                "Open": closes, "High": [c * 1.01 for c in closes],
                "Low": [c * 0.99 for c in closes], "Close": closes,
                "Volume": [1_000_000] * 25,
                "iv_override": [None] * 25,
            },
            index=dates,
        )
        request = BacktestRequest(strategy=_options_strategy(_entry_when_close_above(0.0)), ticker="TEST")
        result = run_backtest(request, df)
        assert result.trades[0].option_iv_entry == 0.30

    def test_prefers_iv_override_over_computed_rv20_when_both_present(self):
        """Entry after bar 20 so rv20 is a real (non-NaN) number driven by
        a sharp price jump -> a small override (0.05, floored to 0.10)
        must still win over whatever large value rv20 computed to."""
        closes = [100.0] * 20 + [200.0] * 5
        dates = pd.date_range("2024-01-02", periods=25, freq="B")
        df = pd.DataFrame(
            {
                "Open": closes, "High": [c * 1.01 for c in closes],
                "Low": [c * 0.99 for c in closes], "Close": closes,
                "Volume": [1_000_000] * 25,
                "iv_override": [None] * 20 + [0.05] * 5,
            },
            index=dates,
        )
        request = BacktestRequest(strategy=_options_strategy(_entry_when_close_above(150.0)), ticker="TEST")
        result = run_backtest(request, df)
        assert result.trades[0].option_iv_entry == 0.10  # 0.05 floored, not rv20's much larger value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_iv_override.py -v`
Expected: FAIL on `test_uses_iv_override_at_entry_when_present` and `test_prefers_iv_override_over_computed_rv20_when_both_present` (both would currently get `rv20`-or-default IV, not `0.80`/`0.10`); `test_falls_back_to_default_when_neither_override_nor_rv20_present` already passes today (no regression there).

- [ ] **Step 3: Implement the hook**

In `src/backtester/engine.py::_open_position`, replace the current IV lookup:

```python
        iv = float(df["rv20"].iloc[bar_idx]) if "rv20" in df and not pd.isna(df["rv20"].iloc[bar_idx]) else 0.30
        iv = max(iv, 0.10) # Floor IV at 10%
```

with:

```python
        if "iv_override" in df and not pd.isna(df["iv_override"].iloc[bar_idx]):
            iv = float(df["iv_override"].iloc[bar_idx])
        elif "rv20" in df and not pd.isna(df["rv20"].iloc[bar_idx]):
            iv = float(df["rv20"].iloc[bar_idx])
        else:
            iv = 0.30
        iv = max(iv, 0.10)  # Floor IV at 10%
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_iv_override.py -v`
Expected: PASS (3 passed)

Also re-run existing options-related tests for regressions:
Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_pyramiding.py tests/test_backtester_profit_ladder.py tests/test_backtester_signal_dates.py -v`
Expected: PASS (all still passing — no strategy without an `iv_override` column is affected)

- [ ] **Step 5: Commit**

```bash
git add src/backtester/engine.py tests/test_backtester_iv_override.py
git commit -m "feat(backtester): prefer iv_override column over rv20 proxy at entry"
```

---

### Task 4: `get_signal_events` — all gate firings, prime + control

**Files:**
- Create: `src/algo_detective/signal_events.py`
- Test: `tests/test_algo_detective_signal_events.py`

**Interfaces:**
- Consumes: `_apply_criteria(row: dict, criteria: dict) -> bool` from `src/algo_detective/analyze.py`; `get_all_features() -> list[dict]` and `get_options_index() -> dict[tuple[str, str], dict]` from `src/algo_detective/store.py`.
- Produces: `get_signal_events(criteria: dict, features: list[dict] | None = None, join_options: bool = False) -> list[dict]` — each item `{"date": str, "ticker": str, "is_prime": int}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_signal_events.py`:

```python
"""Tests for get_signal_events in src/algo_detective/signal_events.py.

Unlike validate.py's validate_criteria() (which scores precision/recall
against the prime subset only), get_signal_events returns every firing
— prime AND control-labeled — since a live scanner can't tell them
apart at fire time. See
docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

from src.algo_detective.signal_events import get_signal_events


def _row(date: str, ticker: str, is_prime: int, **kwargs) -> dict:
    return {"date": date, "ticker": ticker, "is_prime": is_prime, **kwargs}


class TestGetSignalEvents:
    def test_returns_events_from_both_prime_and_control(self):
        features = [
            _row("2026-01-02", "AAPL", 1, adr20_pct=2.0),
            _row("2026-01-02", "MSFT", 0, adr20_pct=2.0),  # control, also fires
            _row("2026-01-02", "TSLA", 0, adr20_pct=8.0),  # control, doesn't fire
        ]
        events = get_signal_events({"adr20_pct_max": 4.0}, features=features)
        tickers = {e["ticker"] for e in events}
        assert tickers == {"AAPL", "MSFT"}

    def test_preserves_is_prime_label_for_downstream_reporting(self):
        features = [_row("2026-01-02", "MSFT", 0, adr20_pct=2.0)]
        events = get_signal_events({"adr20_pct_max": 4.0}, features=features)
        assert events[0]["is_prime"] == 0

    def test_empty_when_nothing_fires(self):
        features = [_row("2026-01-02", "TSLA", 1, adr20_pct=8.0)]
        events = get_signal_events({"adr20_pct_max": 4.0}, features=features)
        assert events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo_detective.signal_events'`

- [ ] **Step 3: Implement `get_signal_events`**

Create `src/algo_detective/signal_events.py`:

```python
"""Extract every historical (date, ticker) event where a candidate GTPro
gate criteria fires — both prime- and control-labeled tickers, since a
live scanner can't distinguish a true positive from a false positive at
fire time.

Unlike validate.py's validate_criteria() (which scores precision/recall
using only the prime subset as ground truth), this returns the full set
of firings for downstream trade simulation in signal_backtest.py. See
docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

from .analyze import _apply_criteria
from .store import get_all_features, get_options_index


def get_signal_events(
    criteria: dict,
    features: list[dict] | None = None,
    join_options: bool = False,
) -> list[dict]:
    """Return every {date, ticker, is_prime} row where criteria fires.

    Args:
        join_options: If True (or criteria contains 'options_iv_min'),
            merge detective_options data (best_iv) into each row before
            evaluation, mirroring validate_criteria's join behavior.
    """
    if features is None:
        features = get_all_features()

    needs_options = join_options or "options_iv_min" in criteria
    if needs_options:
        options_idx = get_options_index()
        enriched = []
        for f in features:
            opt = options_idx.get((f["date"], f["ticker"]), {})
            enriched.append({**f, "best_iv": opt.get("best_iv")})
        features = enriched

    return [
        {"date": f["date"], "ticker": f["ticker"], "is_prime": f["is_prime"]}
        for f in features
        if _apply_criteria(f, criteria)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_events.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/signal_events.py tests/test_algo_detective_signal_events.py
git commit -m "feat(algo-detective): add get_signal_events for all gate firings"
```

---

### Task 5: `compute_pooled_trade_stats`

**Files:**
- Create: `src/algo_detective/signal_backtest.py` (this task only adds this one function + module docstring/imports; later tasks extend the same file)
- Test: `tests/test_algo_detective_signal_backtest.py` (this task only adds the `TestComputePooledTradeStats` class; later tasks add more classes to the same file)

**Interfaces:**
- Consumes: nothing (pure function; accepts backtester `Trade` objects or plain dicts with `pnl`/`pnl_pct` keys).
- Produces: `compute_pooled_trade_stats(trades: list) -> dict` with keys `total_trades, wins, losses, win_rate_pct, profit_factor, avg_pnl, avg_pnl_pct, total_pnl`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_signal_backtest.py`:

```python
"""Tests for src/algo_detective/signal_backtest.py — simulates real CSP
wheel trades on every historical gate hit and pools trade-level P&L.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

from src.algo_detective.signal_backtest import compute_pooled_trade_stats


def _trade(pnl: float, pnl_pct: float) -> dict:
    return {"pnl": pnl, "pnl_pct": pnl_pct}


class TestComputePooledTradeStats:
    def test_empty_trades_returns_zeroed_stats(self):
        stats = compute_pooled_trade_stats([])
        assert stats["total_trades"] == 0
        assert stats["profit_factor"] is None

    def test_computes_win_rate_and_pnl_totals(self):
        trades = [_trade(100.0, 10.0), _trade(-50.0, -5.0), _trade(200.0, 20.0)]
        stats = compute_pooled_trade_stats(trades)
        assert stats["total_trades"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["win_rate_pct"] == round(2 / 3 * 100.0, 2)
        assert stats["total_pnl"] == 250.0
        assert stats["avg_pnl"] == round(250.0 / 3, 2)

    def test_profit_factor_is_gross_profit_over_gross_loss(self):
        trades = [_trade(100.0, 10.0), _trade(-25.0, -2.5)]
        stats = compute_pooled_trade_stats(trades)
        assert stats["profit_factor"] == 4.0

    def test_profit_factor_none_when_no_losses(self):
        trades = [_trade(100.0, 10.0)]
        stats = compute_pooled_trade_stats(trades)
        assert stats["profit_factor"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_backtest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo_detective.signal_backtest'`

- [ ] **Step 3: Implement `compute_pooled_trade_stats`**

Create `src/algo_detective/signal_backtest.py`:

```python
"""Simulate real CSP wheel trades on every historical gate hit for a
candidate algo_detective criteria dict, using the existing backtester
engine (src/backtester/) — validates trade-level P&L, not just
precision/recall. See
docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def compute_pooled_trade_stats(trades: list) -> dict:
    """Aggregate P&L stats across trades pooled from many independent
    per-ticker backtests — no shared equity curve or capital allocation.

    Accepts backtester Trade objects (attribute access) or plain dicts
    with the same keys (pnl/pnl_pct), so tests can use either.
    """
    def _get(t, key):
        return getattr(t, key) if hasattr(t, key) else t[key]

    total = len(trades)
    if total == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "profit_factor": None, "avg_pnl": 0.0, "avg_pnl_pct": 0.0,
            "total_pnl": 0.0,
        }

    pnls = [_get(t, "pnl") for t in trades]
    pnl_pcts = [_get(t, "pnl_pct") for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / total * 100.0, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "avg_pnl": round(sum(pnls) / total, 2),
        "avg_pnl_pct": round(sum(pnl_pcts) / total, 2),
        "total_pnl": round(sum(pnls), 2),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_backtest.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/signal_backtest.py tests/test_algo_detective_signal_backtest.py
git commit -m "feat(algo-detective): add compute_pooled_trade_stats"
```

---

### Task 6: `run_signal_backtest` orchestration

**Files:**
- Modify: `src/algo_detective/signal_backtest.py`
- Modify: `tests/test_algo_detective_signal_backtest.py` (add a new test class)

**Interfaces:**
- Consumes: `get_signal_events` (Task 4); `compute_pooled_trade_stats` (Task 5); the `signal_dates` leaf (Task 1); `ExitStrategy.profit_ladder` (Task 2); the `iv_override` column convention (Task 3); `get_historical_data(symbol: str, start_date=None, end_date=None) -> pd.DataFrame` from `src/backtester/data_provider.py`; `get_options_index()` from `src/algo_detective/store.py`; `run_backtest(request, df) -> BacktestResult` from `src/backtester/engine.py`; `BacktestRequest`, `StrategyDefinition`, `ExitStrategy`, `OptionsConfig`, `ProfitLadderTier` from `src/backtester/models.py`.
- Produces: `DEFAULT_GTPRO_LADDER: list[ProfitLadderTier]`; `run_signal_backtest(criteria: dict, target_delta: float = 0.25, target_dte: int = 5, ladder: list[ProfitLadderTier] | None = None, events: list[dict] | None = None) -> dict` with keys `criteria, stats, trades, tickers_skipped`.

- [ ] **Step 1: Write the failing test**

In `tests/test_algo_detective_signal_backtest.py`, replace the file's existing import block (currently just `from src.algo_detective.signal_backtest import compute_pooled_trade_stats`, directly below `from __future__ import annotations`) with:

```python
from unittest.mock import patch

import pandas as pd
import pytest

from src.algo_detective.signal_backtest import compute_pooled_trade_stats, run_signal_backtest
```

Then append the following to the end of the same file (after the existing `TestComputePooledTradeStats` class):

```python
def _make_ohlcv(periods: int = 30) -> pd.DataFrame:
    closes = [100.0] * periods
    dates = pd.date_range(start="2026-01-02", periods=periods, freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * periods,
        },
        index=dates,
    )


@pytest.fixture
def _patched_data_sources():
    fixture = _make_ohlcv()
    with patch("src.algo_detective.signal_backtest.get_historical_data") as mock_hist, \
         patch("src.algo_detective.signal_backtest.get_options_index") as mock_opts:
        mock_hist.side_effect = lambda symbol, **kwargs: fixture.copy()
        mock_opts.return_value = {}
        yield


class TestRunSignalBacktest:
    def test_produces_one_pooled_trade_per_ticker_with_a_signal(self, _patched_data_sources):
        events = [
            {"date": "2026-01-02", "ticker": "AAPL", "is_prime": 1},
            {"date": "2026-01-02", "ticker": "MSFT", "is_prime": 0},
        ]
        result = run_signal_backtest({"adr20_pct_max": 4.0}, events=events)
        assert result["stats"]["total_trades"] == 2
        assert result["tickers_skipped"] == []

    def test_trades_are_short_put_positions(self, _patched_data_sources):
        events = [{"date": "2026-01-02", "ticker": "AAPL", "is_prime": 1}]
        result = run_signal_backtest({"adr20_pct_max": 4.0}, events=events)
        trade = result["trades"][0]
        assert trade.direction == "short"
        assert trade.is_option is True
        assert trade.option_type == "put"

    def test_stats_dict_has_expected_keys(self, _patched_data_sources):
        events = [{"date": "2026-01-02", "ticker": "AAPL", "is_prime": 1}]
        result = run_signal_backtest({"adr20_pct_max": 4.0}, events=events)
        assert set(result["stats"]) == {
            "total_trades", "wins", "losses", "win_rate_pct",
            "profit_factor", "avg_pnl", "avg_pnl_pct", "total_pnl",
        }

    def test_skips_ticker_with_no_available_price_data(self):
        events = [{"date": "2026-01-02", "ticker": "UNKNOWN", "is_prime": 1}]
        with patch("src.algo_detective.signal_backtest.get_historical_data") as mock_hist, \
             patch("src.algo_detective.signal_backtest.get_options_index") as mock_opts:
            mock_hist.return_value = pd.DataFrame()
            mock_opts.return_value = {}
            result = run_signal_backtest({"adr20_pct_max": 4.0}, events=events)
        assert result["tickers_skipped"] == ["UNKNOWN"]
        assert result["stats"]["total_trades"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_backtest.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_signal_backtest'`

- [ ] **Step 3: Implement `run_signal_backtest`**

Add to `src/algo_detective/signal_backtest.py` (after the existing imports/logger setup, before `compute_pooled_trade_stats`):

```python
from collections import defaultdict

import pandas as pd

from ..backtester.data_provider import get_historical_data
from ..backtester.engine import run_backtest
from ..backtester.models import (
    BacktestRequest,
    ExitStrategy,
    OptionsConfig,
    ProfitLadderTier,
    StrategyDefinition,
)
from .signal_events import get_signal_events
from .store import get_options_index

DEFAULT_GTPRO_LADDER = [
    ProfitLadderTier(max_days_held=2, take_profit_pct=30.0),
    ProfitLadderTier(max_days_held=4, take_profit_pct=50.0),
    ProfitLadderTier(max_days_held=5, take_profit_pct=75.0),
]
```

Add the orchestration functions to `src/algo_detective/signal_backtest.py` (after `compute_pooled_trade_stats`):

```python
def _build_ticker_df(ticker: str, options_idx: dict) -> pd.DataFrame | None:
    """Fetch full OHLCV history for a ticker and join real IV where
    available, leaving other bars for the engine's own RV20 proxy."""
    df = get_historical_data(symbol=ticker, start_date=None, end_date=None)
    if df.empty:
        return None
    df["iv_override"] = [
        options_idx.get((d.strftime("%Y-%m-%d"), ticker), {}).get("best_iv")
        for d in df.index
    ]
    return df


def _build_strategy(
    dates: list[str], target_delta: float, target_dte: int, ladder: list[ProfitLadderTier]
) -> StrategyDefinition:
    return StrategyDefinition(
        entry={"type": "signal_dates", "dates": dates},
        direction="short",
        options=OptionsConfig(enabled=True, type="put", target_delta=target_delta, target_dte=target_dte),
        exit=ExitStrategy(profit_ladder=ladder),
    )


def run_signal_backtest(
    criteria: dict,
    target_delta: float = 0.25,
    target_dte: int = 5,
    ladder: list[ProfitLadderTier] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """Simulate a CSP wheel trade on every historical (date, ticker) gate
    hit. Returns pooled trade-level stats — see compute_pooled_trade_stats.
    """
    ladder = ladder if ladder is not None else DEFAULT_GTPRO_LADDER
    events = events if events is not None else get_signal_events(criteria)

    by_ticker: dict[str, list[str]] = defaultdict(list)
    for e in events:
        by_ticker[e["ticker"]].append(e["date"])

    options_idx = get_options_index()
    all_trades = []
    tickers_skipped = []
    for ticker, dates in by_ticker.items():
        df = _build_ticker_df(ticker, options_idx)
        if df is None:
            logger.warning("No OHLCV data for %s, skipping %d signal event(s)", ticker, len(dates))
            tickers_skipped.append(ticker)
            continue
        strategy = _build_strategy(sorted(set(dates)), target_delta, target_dte, ladder)
        request = BacktestRequest(strategy=strategy, ticker=ticker)
        result = run_backtest(request, df)
        all_trades.extend(result.trades)

    return {
        "criteria": criteria,
        "stats": compute_pooled_trade_stats(all_trades),
        "trades": all_trades,
        "tickers_skipped": tickers_skipped,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_backtest.py -v`
Expected: PASS (8 passed — 4 from Task 5 + 4 new)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/signal_backtest.py tests/test_algo_detective_signal_backtest.py
git commit -m "feat(algo-detective): add run_signal_backtest orchestration"
```

---

### Task 7: `run_signal_walk_forward` — IS/OOS fold aggregation

**Files:**
- Modify: `src/algo_detective/signal_backtest.py`
- Create: `tests/test_algo_detective_signal_walk_forward.py`

**Interfaces:**
- Consumes: `run_signal_backtest`, `get_signal_events`, `DEFAULT_GTPRO_LADDER` (Task 6); `_generate_folds(total_bars: int, is_bars: int, oos_bars: int, mode: WalkForwardMode) -> list[tuple[int,int,int,int]]` from `src/backtester/walk_forward.py`; `WalkForwardMode` from `src/backtester/models.py`.
- Produces: `run_signal_walk_forward(criteria: dict, mode: WalkForwardMode = WalkForwardMode.ROLLING, in_sample_days: int = 756, out_of_sample_days: int = 252, target_delta: float = 0.25, target_dte: int = 5, ladder: list[ProfitLadderTier] | None = None, events: list[dict] | None = None) -> dict` with key `folds: list[dict]`, each fold having `fold_number, is_start, is_end, oos_start, oos_end, is_stats, oos_stats, degradation`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_signal_walk_forward.py`:

```python
"""Tests for run_signal_walk_forward in src/algo_detective/signal_backtest.py
— verifies IS/OOS fold generation and degradation-ratio reporting over a
pooled signal event set, reusing the backtester's existing fold logic.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.algo_detective.signal_backtest import run_signal_walk_forward


def _make_ohlcv(periods: int = 400) -> pd.DataFrame:
    closes = [100.0] * periods
    dates = pd.date_range(start="2024-01-02", periods=periods, freq="B")
    return pd.DataFrame(
        {
            "Open": closes, "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes], "Close": closes,
            "Volume": [1_000_000] * periods,
        },
        index=dates,
    )


@pytest.fixture(autouse=True)
def _patch_data_sources():
    fixture = _make_ohlcv()
    with patch("src.algo_detective.signal_backtest.get_historical_data") as mock_hist, \
         patch("src.algo_detective.signal_backtest.get_options_index") as mock_opts:
        mock_hist.side_effect = lambda symbol, **kwargs: fixture.copy()
        mock_opts.return_value = {}
        yield


def _events_across_dates(dates: list[str], ticker: str = "AAPL") -> list[dict]:
    return [{"date": d, "ticker": ticker, "is_prime": 1} for d in dates]


class TestRunSignalWalkForward:
    def test_returns_no_folds_when_events_empty(self):
        result = run_signal_walk_forward({"adr20_pct_max": 4.0}, events=[])
        assert result["folds"] == []

    def test_generates_at_least_one_fold_with_enough_signal_dates(self):
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=300)]
        events = _events_across_dates(dates)
        result = run_signal_walk_forward(
            {"adr20_pct_max": 4.0}, in_sample_days=200, out_of_sample_days=50, events=events,
        )
        assert len(result["folds"]) >= 1
        fold = result["folds"][0]
        assert set(fold) == {
            "fold_number", "is_start", "is_end", "oos_start", "oos_end",
            "is_stats", "oos_stats", "degradation",
        }

    def test_no_folds_when_not_enough_signal_dates_for_one_fold(self):
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=10)]
        events = _events_across_dates(dates)
        result = run_signal_walk_forward(
            {"adr20_pct_max": 4.0}, in_sample_days=200, out_of_sample_days=50, events=events,
        )
        assert result["folds"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_walk_forward.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_signal_walk_forward'`

- [ ] **Step 3: Implement `run_signal_walk_forward`**

Add to the imports at the top of `src/algo_detective/signal_backtest.py`:

```python
from ..backtester.models import WalkForwardMode
from ..backtester.walk_forward import _generate_folds
```

Add to `src/algo_detective/signal_backtest.py` (after `run_signal_backtest`):

```python
def _compute_pooled_degradation(is_stats: dict, oos_stats: dict) -> dict:
    """OOS/IS ratios for the pooled trade metrics — values near 1.0 mean
    the gate's P&L held up out of sample; values << 1.0 suggest overfit."""
    metrics = ["win_rate_pct", "profit_factor", "avg_pnl", "avg_pnl_pct"]
    ratios = {}
    for m in metrics:
        is_val, oos_val = is_stats.get(m), oos_stats.get(m)
        if is_val in (None, 0) or oos_val is None:
            ratios[m] = None
        else:
            ratios[m] = round(oos_val / is_val, 3)
    return ratios


def run_signal_walk_forward(
    criteria: dict,
    mode: WalkForwardMode = WalkForwardMode.ROLLING,
    in_sample_days: int = 756,
    out_of_sample_days: int = 252,
    target_delta: float = 0.25,
    target_dte: int = 5,
    ladder: list[ProfitLadderTier] | None = None,
    events: list[dict] | None = None,
) -> dict:
    """Split the signal event set into IS/OOS folds by calendar date
    (reusing the backtester's existing fold-generation logic) and report
    IS-vs-OOS degradation for the pooled trade stats in each fold."""
    ladder = ladder if ladder is not None else DEFAULT_GTPRO_LADDER
    events = events if events is not None else get_signal_events(criteria)

    all_dates = sorted({e["date"] for e in events})
    if not all_dates:
        return {"criteria": criteria, "folds": []}

    folds_idx = _generate_folds(
        total_bars=len(all_dates),
        is_bars=in_sample_days,
        oos_bars=out_of_sample_days,
        mode=mode,
    )

    folds = []
    for fold_num, (is_start, is_end, oos_start, oos_end) in enumerate(folds_idx, 1):
        is_dates = set(all_dates[is_start:is_end])
        oos_dates = set(all_dates[oos_start:oos_end])

        is_events = [e for e in events if e["date"] in is_dates]
        oos_events = [e for e in events if e["date"] in oos_dates]

        is_result = run_signal_backtest(criteria, target_delta, target_dte, ladder, events=is_events)
        oos_result = run_signal_backtest(criteria, target_delta, target_dte, ladder, events=oos_events)

        folds.append({
            "fold_number": fold_num,
            "is_start": all_dates[is_start], "is_end": all_dates[is_end - 1],
            "oos_start": all_dates[oos_start], "oos_end": all_dates[oos_end - 1],
            "is_stats": is_result["stats"],
            "oos_stats": oos_result["stats"],
            "degradation": _compute_pooled_degradation(is_result["stats"], oos_result["stats"]),
        })

    return {"criteria": criteria, "folds": folds}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_walk_forward.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/signal_backtest.py tests/test_algo_detective_signal_walk_forward.py
git commit -m "feat(algo-detective): add run_signal_walk_forward IS/OOS aggregation"
```

---

### Task 8: CLI entry point and report printers

**Files:**
- Modify: `src/algo_detective/signal_backtest.py`
- Create: `tests/test_algo_detective_signal_backtest_cli.py`

**Interfaces:**
- Consumes: `run_signal_backtest`, `run_signal_walk_forward` (Tasks 6, 7).
- Produces: `print_signal_backtest_report(result: dict) -> None`; `print_signal_walk_forward_report(result: dict) -> None`; a `python -m src.algo_detective.signal_backtest --criteria '...' [--mode backtest|walk-forward]` CLI, mirroring `validate.py`'s existing `--criteria` flag.

- [ ] **Step 1: Write the failing test**

Create `tests/test_algo_detective_signal_backtest_cli.py`:

```python
"""Tests for the CLI report printers in src/algo_detective/signal_backtest.py."""
from __future__ import annotations

from src.algo_detective.signal_backtest import (
    print_signal_backtest_report,
    print_signal_walk_forward_report,
)


class TestReportPrinters:
    def test_print_signal_backtest_report_smoke(self, capsys):
        result = {
            "criteria": {"adr20_pct_max": 4.0},
            "stats": {
                "total_trades": 10, "wins": 6, "losses": 4, "win_rate_pct": 60.0,
                "profit_factor": 1.8, "avg_pnl": 42.0, "avg_pnl_pct": 12.0, "total_pnl": 420.0,
            },
            "trades": [],
            "tickers_skipped": ["ZZZZ"],
        }
        print_signal_backtest_report(result)
        captured = capsys.readouterr()
        assert "Trades simulated : 10" in captured.out
        assert "ZZZZ" in captured.out

    def test_print_signal_walk_forward_report_smoke(self, capsys):
        result = {
            "criteria": {"adr20_pct_max": 4.0},
            "folds": [{
                "fold_number": 1, "is_start": "2024-01-02", "is_end": "2024-06-01",
                "oos_start": "2024-06-02", "oos_end": "2024-12-01",
                "is_stats": {"win_rate_pct": 60.0, "profit_factor": 1.8},
                "oos_stats": {"win_rate_pct": 55.0, "profit_factor": 1.5},
                "degradation": {"win_rate_pct": 0.917},
            }],
        }
        print_signal_walk_forward_report(result)
        captured = capsys.readouterr()
        assert "Fold 1" in captured.out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_backtest_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'print_signal_backtest_report'`

- [ ] **Step 3: Implement the CLI and report printers**

Add `import argparse`, `import json`, and `from pathlib import Path` to the top of `src/algo_detective/signal_backtest.py` (alongside the existing `import logging`).

Add to the end of `src/algo_detective/signal_backtest.py`:

```python
def print_signal_backtest_report(result: dict) -> None:
    stats = result["stats"]
    print(f"\n{'='*60}")
    print(f"Criteria: {json.dumps(result['criteria'], indent=2)}")
    print(f"\nTrades simulated : {stats['total_trades']}")
    print(f"Win rate         : {stats['win_rate_pct']}%  ({stats['wins']}W / {stats['losses']}L)")
    print(f"Profit factor    : {stats['profit_factor']}")
    print(f"Avg P&L / trade  : ${stats['avg_pnl']}")
    print(f"Total P&L        : ${stats['total_pnl']}")
    if result["tickers_skipped"]:
        print(f"\nSkipped (no price data): {', '.join(result['tickers_skipped'])}")
    print()


def print_signal_walk_forward_report(result: dict) -> None:
    print(f"\n{'='*60}")
    print(f"Criteria: {json.dumps(result['criteria'], indent=2)}")
    for fold in result["folds"]:
        print(f"\nFold {fold['fold_number']}: IS {fold['is_start']}..{fold['is_end']} "
              f"/ OOS {fold['oos_start']}..{fold['oos_end']}")
        print(f"  IS  win_rate={fold['is_stats']['win_rate_pct']}%  "
              f"profit_factor={fold['is_stats']['profit_factor']}")
        print(f"  OOS win_rate={fold['oos_stats']['win_rate_pct']}%  "
              f"profit_factor={fold['oos_stats']['profit_factor']}")
        print(f"  Degradation: {fold['degradation']}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate CSP wheel trades on every historical gate hit")
    parser.add_argument("--criteria", required=True, help="JSON string or path to .json file")
    parser.add_argument("--mode", choices=["backtest", "walk-forward"], default="backtest")
    parser.add_argument("--target-delta", type=float, default=0.25)
    parser.add_argument("--target-dte", type=int, default=5)
    args = parser.parse_args()

    criteria_input = args.criteria.strip()
    if criteria_input.endswith(".json") and Path(criteria_input).exists():
        criteria = json.loads(Path(criteria_input).read_text())
    else:
        criteria = json.loads(criteria_input)

    if args.mode == "walk-forward":
        wf_result = run_signal_walk_forward(criteria, target_delta=args.target_delta, target_dte=args.target_dte)
        print_signal_walk_forward_report(wf_result)
    else:
        bt_result = run_signal_backtest(criteria, target_delta=args.target_delta, target_dte=args.target_dte)
        print_signal_backtest_report(bt_result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm test python3 -m pytest tests/test_algo_detective_signal_backtest_cli.py -v`
Expected: PASS (2 passed)

Run the full new-file test suite together to confirm nothing broke across tasks:
Run: `docker compose run --rm test python3 -m pytest tests/test_backtester_signal_dates.py tests/test_backtester_profit_ladder.py tests/test_backtester_iv_override.py tests/test_algo_detective_signal_events.py tests/test_algo_detective_signal_backtest.py tests/test_algo_detective_signal_walk_forward.py tests/test_algo_detective_signal_backtest_cli.py -v`
Expected: PASS (all tests across all 7 new test files)

- [ ] **Step 5: Commit**

```bash
git add src/algo_detective/signal_backtest.py tests/test_algo_detective_signal_backtest_cli.py
git commit -m "feat(algo-detective): add signal_backtest CLI and report printers"
```

---

## Post-Implementation

Run the full existing suite once more to confirm no regressions anywhere else in the repo:

Run: `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -v`
Expected: PASS (all tests, including the 7 new files above)

Try it against real data (requires the dev stack up, see `CLAUDE.md`):

```bash
docker compose run --rm pipeline python3 -m src.algo_detective.signal_backtest \
  --criteria '{"adr20_pct_max": 4.0}' --mode backtest
```
