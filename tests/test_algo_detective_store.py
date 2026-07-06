from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    with patch("src.algo_detective.store.settings") as mock:
        mock.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


from src.algo_detective.store import (
    ensure_tables,
    get_computed_pairs,
    upsert_feature_rows_bulk,
    upsert_macro_row,
    get_all_features,
    get_macro_for_date,
    get_feature_counts,
    upsert_options_rows,
    get_options_index,
    get_computed_options_pairs,
)


def _make_feature_row(date="2025-10-07", ticker="GE", is_prime=1):
    return {
        "date": date,
        "ticker": ticker,
        "is_prime": is_prime,
        "close_price": 295.0,
        "volume": 1000000,
        "rsi": 64.0,
        "adx": 26.0,
        "ema20": 290.0, "ema50": 280.0, "ema150": 265.0, "ema200": 260.0,
        "sma20": 291.0, "sma50": 281.0, "sma150": 266.0, "sma200": 261.0,
        "price_vs_ema20_pct": 1.72, "price_vs_ema50_pct": 5.36,
        "price_vs_ema150_pct": 11.32, "price_vs_ema200_pct": 13.46,
        "price_vs_sma20_pct": 1.37, "price_vs_sma50_pct": 4.98,
        "price_vs_sma150_pct": 10.90, "price_vs_sma200_pct": 13.03,
        "price_above_ema20": 1, "price_above_ema50": 1,
        "price_above_ema150": 1, "price_above_ema200": 1,
        "price_above_sma20": 1, "price_above_sma50": 1,
        "price_above_sma150": 1, "price_above_sma200": 1,
        "ema20_above_ema50": 1, "ema50_above_ema150": 1,
        "ema50_above_ema200": 1, "ema150_above_ema200": 1,
        "sma20_above_sma50": 1, "sma50_above_sma150": 1,
        "sma50_above_sma200": 1, "sma150_above_sma200": 1,
        "bb_upper": 305.0, "bb_middle": 291.0, "bb_lower": 277.0,
        "bb_pct_b": 0.72, "bb_width_pct": 9.62,
        "price_above_bb_middle": 1, "price_above_bb_upper": 0, "price_below_bb_lower": 0,
        "rv20": 0.32, "atr_pct": 1.1, "volume_ratio": 1.3,
        "roc20": 4.5, "macd_histogram": 1.2, "pct_from_52wk_high": 2.1,
        "sector": "Industrials",
        "computed_at": "2026-06-18T00:00:00+00:00",
    }


def test_ensure_tables_creates_tables():
    ensure_tables()
    import sqlite3
    conn = sqlite3.connect(_tmp_db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "detective_features" in tables
    assert "detective_macro" in tables


def test_get_computed_pairs_empty():
    ensure_tables()
    assert get_computed_pairs() == set()


def test_upsert_and_retrieve_feature_row():
    ensure_tables()
    row = _make_feature_row()
    count = upsert_feature_rows_bulk([row])
    assert count == 1
    pairs = get_computed_pairs()
    assert ("2025-10-07", "GE") in pairs


def test_upsert_idempotent():
    ensure_tables()
    row = _make_feature_row(ticker="MSFT")
    upsert_feature_rows_bulk([row])
    upsert_feature_rows_bulk([row])  # second insert should not raise
    features = get_all_features()
    msft_rows = [f for f in features if f["ticker"] == "MSFT"]
    assert len(msft_rows) == 1


def test_get_feature_counts():
    ensure_tables()
    rows = [
        _make_feature_row(ticker="JPM", is_prime=1),
        _make_feature_row(ticker="BAC", is_prime=0),
        _make_feature_row(ticker="WFC", is_prime=0),
    ]
    upsert_feature_rows_bulk(rows)
    counts = get_feature_counts()
    assert counts["prime"] >= 1
    assert counts["control"] >= 2
    assert counts["total"] == counts["prime"] + counts["control"]


def test_upsert_and_retrieve_macro_row():
    ensure_tables()
    macro = {
        "date": "2025-10-07",
        "vix_score": 18.5,
        "vix_direction": "neutral",
        "market_posture": "Bullish",
        "composite_score": 0.45,
        "fear_greed_score": 62.0,
        "spy_above_ema50": 1,
        "spy_above_ema200": 1,
        "spy_rsi": 61.0,
        "top_sectors": '["Technology", "Financials"]',
    }
    upsert_macro_row(macro)
    result = get_macro_for_date("2025-10-07")
    assert result is not None
    assert result["market_posture"] == "Bullish"
    assert result["spy_above_ema50"] == 1


def test_get_macro_for_missing_date_returns_none():
    ensure_tables()
    assert get_macro_for_date("1990-01-01") is None


def _make_options_row(date="2026-06-18", ticker="NVDA", **overrides):
    row = {
        "date": date,
        "ticker": ticker,
        "best_iv": 0.42,
        "best_volume": 1500,
        "occ_symbol": f"{ticker}260619P00120000",
        "pcr_vol": 1.15,
        "pcr_oi": 0.95,
    }
    row.update(overrides)
    return row


def test_ensure_tables_creates_detective_options():
    ensure_tables()
    conn = sqlite3.connect(_tmp_db_path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    assert "detective_options" in {r[0] for r in rows}


def test_get_options_index_empty():
    ensure_tables()
    assert get_options_index() == {}


def test_get_computed_options_pairs_empty():
    ensure_tables()
    assert get_computed_options_pairs() == set()


def test_upsert_and_retrieve_options_row():
    ensure_tables()
    row = _make_options_row(ticker="AAPL")
    count = upsert_options_rows([row])
    assert count == 1

    index = get_options_index()
    stored = index[("2026-06-18", "AAPL")]
    assert stored["best_iv"] == 0.42
    assert stored["pcr_vol"] == 1.15
    assert stored["pcr_oi"] == 0.95
    assert ("2026-06-18", "AAPL") in get_computed_options_pairs()


def test_upsert_options_rows_empty_list_is_noop():
    ensure_tables()
    assert upsert_options_rows([]) == 0


def test_upsert_options_coalesces_null_pcr_without_clobbering_existing():
    """A later upsert with pcr fields unset (e.g. a backfill row) must not
    erase pcr values a previous snapshot already stored — this is the whole
    point of the COALESCE in the ON CONFLICT clause."""
    ensure_tables()
    ticker = "MSFT"
    upsert_options_rows([_make_options_row(ticker=ticker, pcr_vol=1.2, pcr_oi=0.8)])

    # Second write (e.g. IV-only update) omits pcr fields — should preserve them
    upsert_options_rows([_make_options_row(ticker=ticker, best_iv=0.55, pcr_vol=None, pcr_oi=None)])

    stored = get_options_index()[("2026-06-18", ticker)]
    assert stored["best_iv"] == 0.55
    assert stored["pcr_vol"] == 1.2
    assert stored["pcr_oi"] == 0.8


def test_upsert_options_rows_idempotent():
    ensure_tables()
    row = _make_options_row(ticker="GOOG")
    upsert_options_rows([row])
    upsert_options_rows([row])
    matching = [
        pair for pair in get_computed_options_pairs() if pair == ("2026-06-18", "GOOG")
    ]
    assert len(matching) == 1
