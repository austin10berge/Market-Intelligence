from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PrimeTicker:
    date: str
    ticker: str
    expiration: str
    strike: float
    delta: float
    premium: float
    iv: float
    return_pct: float
    annual_yield_pct: float
    pop_pct: float
    spread_pct: float
    cushion_pct: float
    rsi: float
    adx: float
    collateral: float
    mlabs_score: float


def load_prime_tickers(csv_path: str | Path) -> list[PrimeTicker]:
    records = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("date", "").strip() or not row.get("ticker", "").strip():
                continue
            try:
                records.append(
                    PrimeTicker(
                        date=row["date"].strip(),
                        ticker=row["ticker"].strip(),
                        expiration=row["expiration"].strip(),
                        strike=float(row["strike"]),
                        delta=float(row["delta"]),
                        premium=float(row["premium"]),
                        iv=float(row["iv"]),
                        return_pct=float(row["return_pct"]),
                        annual_yield_pct=float(row["annual_yield_pct"]),
                        pop_pct=float(row["pop_pct"]),
                        spread_pct=float(row["spread_pct"]),
                        cushion_pct=float(row["cushion_pct"]),
                        rsi=float(row["rsi"]),
                        adx=float(row["adx"]),
                        collateral=float(row["collateral"]),
                        mlabs_score=float(row["mlabs_score"]),
                    )
                )
            except (ValueError, KeyError):
                continue
    return records


def get_unique_dates(records: list[PrimeTicker]) -> list[str]:
    return sorted(set(r.date for r in records))


def get_prime_tickers_for_date(records: list[PrimeTicker], date: str) -> list[str]:
    seen: set[str] = set()
    result = []
    for r in records:
        if r.date == date and r.ticker not in seen:
            seen.add(r.ticker)
            result.append(r.ticker)
    return result


def get_prime_pairs(records: list[PrimeTicker]) -> set[tuple[str, str]]:
    return {(r.date, r.ticker) for r in records}
