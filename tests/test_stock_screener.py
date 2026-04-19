import math

import pandas as pd

from src.screener.stocks import (
    _calculate_iv_rank,
    _calculate_rv20,
    _extract_implied_volatility,
    _parse_occ_symbol,
    _quote_midpoint,
    _solve_implied_volatility,
)


def test_parse_occ_symbol_parses_adjusted_contracts():
    expiration, option_type, strike = _parse_occ_symbol("SOFI1260516C00012000")

    assert expiration.isoformat() == "2026-05-16"
    assert option_type == "C"
    assert strike == 12.0


def test_extract_implied_volatility_normalizes_decimal_values():
    snapshot = {"implied_volatility": 0.443}

    assert _extract_implied_volatility(snapshot) == 44.3


def test_quote_midpoint_uses_bid_ask():
    snapshot = {"latestQuote": {"bp": 1.2, "ap": 1.4}}

    assert math.isclose(_quote_midpoint(snapshot), 1.3)


def test_solve_implied_volatility_from_option_midpoint():
    iv = _solve_implied_volatility(
        option_price=1.3,
        spot=10.75,
        strike=11.0,
        time_to_expiry=35 / 365,
        option_type="C",
    )

    assert iv is not None
    assert 10 < iv < 250


def test_calculate_iv_rank_scales_between_history_low_and_high():
    iv_history = [{"atm_iv": 20.0 + idx} for idx in range(20)]
    iv_history[-1] = {"atm_iv": 60.0}

    iv_rank = _calculate_iv_rank(50.0, iv_history)

    assert iv_rank == 75.0


def test_calculate_iv_rank_requires_minimum_history():
    iv_history = [{"atm_iv": 20.0}, {"atm_iv": 40.0}, {"atm_iv": 60.0}]

    iv_rank = _calculate_iv_rank(50.0, iv_history)

    assert iv_rank is None


def test_calculate_rv20_returns_annualized_percent():
    closes = pd.Series([100, 101, 100.5, 102, 101.5, 103, 104, 103.5, 105, 106, 105.5,
                        107, 106.5, 108, 107.5, 109, 108.5, 110, 111, 110.5, 112, 113])
    hist = pd.DataFrame({"Close": closes})

    rv20 = _calculate_rv20(hist)

    assert rv20 is not None
    assert 0 < rv20 < 100
