# Digest trading-day gate + ET date fix

## Problem

The nightly Market Digest (NTFY + Discord) sent on Sunday night, a day the
market is closed. Separately, the date shown in the digest title/DB record
can be wrong because it's computed from the container's local clock (likely
UTC) instead of US/Eastern.

Both bugs share a root cause: `src/main.py::run_pipeline()` treats "today"
as whatever `date.today()` returns, with no timezone or trading-calendar
awareness anywhere in the pipeline. The cron trigger for `pipeline` lives on
the prod host (10.0.1.21 / firefly), which we have no SSH access to audit
(per `Market-Intelligence/CLAUDE.md`), so the fix must be defensive in the
application code rather than relying on the crontab being correct.

## Fix 1 — ET-aware "today"

`src/main.py:86` currently does:

```python
today = date.today()
```

This feeds every downstream use in the file: the log line, `signal_date`,
the LLM prompt's `date_str`, the NTFY/Discord title, `digest_date` stored to
the DB, the AI-analysis title, and the PCR snapshot date (lines 86–306).

Change it to derive today's date from US/Eastern, reusing the `ET`
zoneinfo constant already defined in `src/cache.py`:

```python
from .cache import ET
...
today = datetime.now(ET).date()
```

(`main.py` currently imports `date` from `datetime`; it will need
`datetime` too.) This is a single-line change at the source of truth — no
other call site in `main.py` needs to change since they all read from the
same `today` variable.

## Fix 2 — trading-day gate

Add a new `is_trading_day(d: date) -> bool` function in `src/cache.py`,
next to `market_is_open()`, using the `holidays` package:

```python
import holidays as holidays_lib

_NYSE_HOLIDAYS = holidays_lib.financial_holidays("NYSE")

def is_trading_day(d: date) -> bool:
    """Return True if `d` is a US equities trading day (Mon-Fri, non-NYSE-holiday)."""
    return d.weekday() < 5 and d not in _NYSE_HOLIDAYS
```

Add `holidays` to `Market-Intelligence/pyproject.toml` dependencies. (Other
`pyproject.toml` files under `.claude/worktrees/` are stale worktree copies,
not in scope.)

In `src/main.py::main()`, gate the scheduled path before any work happens.
The `--mode` flag already exists (`scheduled` / `on-demand`) but is
currently unused for control flow — wire it up:

```python
def main() -> None:
    ...
    args = parser.parse_args()

    today = datetime.now(ET).date()
    if args.mode == "scheduled" and not is_trading_day(today):
        logger.info(f"{today.isoformat()} is not a trading day — skipping scheduled run")
        return

    ...
    result = asyncio.run(run_pipeline(output_mode=args.output))
```

This means:
- `docker compose run --rm pipeline` (the cron entry point, defaults to
  `--mode scheduled`) exits immediately on weekends/holidays — no fetch, no
  LLM call, no notification.
- `--mode on-demand` (and the Discord bot / API's direct
  `run_pipeline(output_mode="on-demand")` call, which never goes through
  `main()` at all) is untouched — manual/on-demand runs still work any day,
  for testing.

## Out of scope

- Auditing or fixing the actual prod crontab entry — no SSH access; if the
  crontab is also wrong, that's a separate, optional cleanup the user can
  do manually.
- Early-close days (e.g. day after Thanksgiving, Christmas Eve some years)
  — `is_trading_day` is a binary open/closed check, not an hours check.
  Out of scope since the digest only cares whether to send at all.
- Changing `market_is_open()` in `cache.py` — it explicitly documents that
  it ignores holidays "for this use case" (live status display); that
  tradeoff is unrelated to and unaffected by this change.

## Testing

- Unit test `is_trading_day()` against a known weekday, a known weekend
  date, and a known NYSE holiday (e.g. 2026-07-04, 2026-12-25, 2026-01-01).
- Unit/integration test that `main()` with `--mode scheduled` short-circuits
  on a mocked non-trading-day date without calling `run_pipeline`.
