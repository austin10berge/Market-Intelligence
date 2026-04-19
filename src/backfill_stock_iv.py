"""CLI entrypoint to backfill stock ATM IV history for IV Rank."""

from __future__ import annotations

import argparse
import logging

from .config import settings
from .screener.stocks import backfill_stock_iv_history


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill stock ATM IV history.")
    parser.add_argument(
        "--days",
        type=int,
        default=180,
        help="Number of recent trading days to backfill (default: 180)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    counts = backfill_stock_iv_history(trading_days=args.days)
    total = sum(counts.values())
    for symbol, count in sorted(counts.items()):
        print(f"{symbol}: {count}")
    print(f"Total backfilled rows: {total}")


if __name__ == "__main__":
    main()
