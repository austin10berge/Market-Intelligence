from __future__ import annotations

import os
import tempfile
from datetime import date
from unittest.mock import patch

import httpx
import pytest
import respx

from src.algo_detective.options_chain import (
    _build_chain_symbols,
    _fetch_snapshots_batch,
    _make_occ,
    _next_fridays,
    _option_type_from_occ,
    _round_to_increment,
    _strike_increment,
    fetch_snapshot_pcr,
)
from src.algo_detective.store import get_options_index
from src.config import settings

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db():
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


class TestOccSymbolHelpers:
    def test_make_occ_put(self):
        occ = _make_occ("AAPL", date(2026, 6, 19), 190.0, "P")
        assert occ == "AAPL260619P00190000"

    def test_make_occ_call(self):
        occ = _make_occ("nvda", date(2026, 6, 19), 120.5, "C")
        assert occ == "NVDA260619C00120500"

    def test_strike_increment_tiers(self):
        assert _strike_increment(250) == 5.0
        assert _strike_increment(100) == 2.5
        assert _strike_increment(30) == 1.0
        assert _strike_increment(10) == 0.5

    def test_round_to_increment(self):
        assert _round_to_increment(101.3, 2.5) == 102.5
        assert _round_to_increment(3.3, 0.5) == 3.5

    def test_next_fridays_returns_n_fridays_on_or_after(self):
        # 2026-06-15 is a Monday
        fridays = _next_fridays(date(2026, 6, 15), n=2)
        assert fridays == [date(2026, 6, 19), date(2026, 6, 26)]

    def test_next_fridays_starting_on_friday_includes_it(self):
        fridays = _next_fridays(date(2026, 6, 19), n=1)
        assert fridays == [date(2026, 6, 19)]

    def test_option_type_from_occ_put(self):
        assert _option_type_from_occ("AAPL260619P00190000") == "P"

    def test_option_type_from_occ_call(self):
        assert _option_type_from_occ("NVDA260619C00120500") == "C"

    def test_option_type_from_occ_unrecognized_returns_question_mark(self):
        assert _option_type_from_occ("GARBAGE") == "?"


class TestBuildChainSymbols:
    def test_covers_puts_and_calls_for_two_expirations(self):
        chain = _build_chain_symbols("AAPL", 190.0, date(2026, 6, 15))
        puts = [occ for occ, t in chain.items() if t == "P"]
        calls = [occ for occ, t in chain.items() if t == "C"]
        assert puts
        assert calls
        # Two Friday expirations means each OCC date tag should appear at least once each
        exp_tags = {occ[4:10] for occ in chain}
        assert len(exp_tags) == 2

    def test_all_strikes_are_positive(self):
        chain = _build_chain_symbols("AAPL", 190.0, date(2026, 6, 15))
        for occ in chain:
            strike_int = int(occ[-8:])
            assert strike_int > 0


class TestFetchSnapshotsBatch:
    @respx.mock
    def test_aggregates_put_call_volume_and_oi_into_pcr(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "AAPL": {
                            "AAPL260619P00190000": {
                                "dailyBar": {"v": 300},
                                "openInterest": 1000,
                                "impliedVolatility": 0.40,
                            },
                            "AAPL260619C00195000": {
                                "dailyBar": {"v": 100},
                                "openInterest": 500,
                            },
                        }
                    }
                },
            )
        )
        result = _fetch_snapshots_batch(["AAPL"])
        assert result["AAPL"]["pcr_vol"] == 3.0
        assert result["AAPL"]["pcr_oi"] == 2.0
        assert result["AAPL"]["best_iv"] == 0.4

    @respx.mock
    def test_missing_call_volume_yields_none_pcr(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "MSFT": {
                            "MSFT260619P00300000": {
                                "dailyBar": {"v": 50},
                                "openInterest": 200,
                            },
                        }
                    }
                },
            )
        )
        result = _fetch_snapshots_batch(["MSFT"])
        assert result["MSFT"]["pcr_vol"] is None
        assert result["MSFT"]["pcr_oi"] is None

    @respx.mock
    def test_network_error_returns_empty_dict(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots").mock(
            side_effect=httpx.ConnectError("boom")
        )
        assert _fetch_snapshots_batch(["AAPL"]) == {}

    def test_empty_symbol_list_short_circuits(self):
        assert _fetch_snapshots_batch([]) == {}


class TestFetchSnapshotPcr:
    @respx.mock
    def test_stores_rows_for_each_ticker(self):
        respx.get(f"{settings.alpaca_data_url}/v1beta1/options/snapshots").mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": {
                        "AAPL": {
                            "AAPL260619P00190000": {
                                "dailyBar": {"v": 300},
                                "openInterest": 1000,
                                "impliedVolatility": 0.40,
                            },
                            "AAPL260619C00195000": {
                                "dailyBar": {"v": 100},
                                "openInterest": 500,
                            },
                        }
                    }
                },
            )
        )
        stored = fetch_snapshot_pcr(["AAPL", "TSLA"], "2026-06-18")
        assert stored == 2

        index = get_options_index()
        assert index[("2026-06-18", "AAPL")]["pcr_vol"] == 3.0
        # Ticker with no snapshot data still gets a row (all-None), not skipped
        assert index[("2026-06-18", "TSLA")]["best_iv"] is None
