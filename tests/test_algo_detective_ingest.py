from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

import pytest

from src.algo_detective.ingest import (
    load_prime_tickers,
    get_unique_dates,
    get_prime_tickers_for_date,
    get_prime_pairs,
)

_SAMPLE_CSV = textwrap.dedent("""\
    date,ticker,expiration,strike,delta,premium,iv,return_pct,annual_yield_pct,pop_pct,spread_pct,cushion_pct,rsi,adx,collateral,mlabs_score
    2025-10-07,GE,2025-10-17,292.5,-0.25,2.26,36,0.77,31,79,9,3,64,26,29200,65.8
    2025-10-07,JPM,2025-10-10,305,-0.30,2.11,31,0.69,51,76,8,2,55,26,30500,61.4
    2025-10-08,GE,2025-10-17,292.5,-0.25,2.26,36,0.77,31,79,9,3,64,26,29200,65.8
    2025-10-07,GE,2025-10-17,292.5,-0.25,2.26,36,0.77,31,79,9,3,64,26,29200,65.8
""")


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "prime.csv"
    p.write_text(_SAMPLE_CSV)
    return p


def test_load_prime_tickers_count(csv_file):
    records = load_prime_tickers(csv_file)
    assert len(records) == 4


def test_load_prime_ticker_fields(csv_file):
    records = load_prime_tickers(csv_file)
    ge = records[0]
    assert ge.date == "2025-10-07"
    assert ge.ticker == "GE"
    assert ge.strike == 292.5
    assert ge.rsi == 64.0
    assert ge.adx == 26.0
    assert ge.mlabs_score == 65.8


def test_get_unique_dates(csv_file):
    records = load_prime_tickers(csv_file)
    dates = get_unique_dates(records)
    assert dates == ["2025-10-07", "2025-10-08"]


def test_get_prime_tickers_for_date_deduplicates(csv_file):
    records = load_prime_tickers(csv_file)
    tickers = get_prime_tickers_for_date(records, "2025-10-07")
    assert sorted(tickers) == ["GE", "JPM"]


def test_get_prime_pairs(csv_file):
    records = load_prime_tickers(csv_file)
    pairs = get_prime_pairs(records)
    assert ("2025-10-07", "GE") in pairs
    assert ("2025-10-08", "GE") in pairs
    assert ("2025-10-07", "JPM") in pairs


def test_load_skips_blank_rows(tmp_path):
    csv = tmp_path / "blank.csv"
    csv.write_text(
        "date,ticker,expiration,strike,delta,premium,iv,return_pct,"
        "annual_yield_pct,pop_pct,spread_pct,cushion_pct,rsi,adx,collateral,mlabs_score\n"
        ",,,,,,,,,,,,,,,\n"
        "2025-10-07,GE,2025-10-17,292.5,-0.25,2.26,36,0.77,31,79,9,3,64,26,29200,65.8\n"
    )
    records = load_prime_tickers(csv)
    assert len(records) == 1
