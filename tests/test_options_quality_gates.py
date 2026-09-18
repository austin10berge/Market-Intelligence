"""Unit tests for the CSP scanner's quote-quality gates.

Audit 2026-09-18: 17 of 29 candidates had bid=0, IV=0 and delta=None, and 8
reported a premium above the ask. A direct Alpaca query confirmed those are
genuine one-sided markets — IV and delta are None *because* there is no bid,
not because the fetch failed. A contract with no bid cannot be sold.
"""

from __future__ import annotations

import pytest

from src.screener.options import _mid_price, _spread_pct, _usable_quote


def test_usable_quote_accepts_two_sided_market():
    assert _usable_quote(0.05, 0.06) is True


def test_usable_quote_rejects_missing_bid():
    # CART260925P00043000: bid=0, ask=0.42 — no one is bidding.
    assert _usable_quote(0.0, 0.42) is False


def test_usable_quote_rejects_missing_ask():
    assert _usable_quote(0.42, 0.0) is False


def test_usable_quote_rejects_empty_book():
    assert _usable_quote(0.0, 0.0) is False


def test_usable_quote_rejects_negative_values():
    assert _usable_quote(-0.01, 0.42) is False
    assert _usable_quote(0.42, -0.01) is False


def test_usable_quote_rejects_crossed_book():
    # A crossed book (bid > ask) is genuinely invalid. A locked book (bid == ask)
    # is a real, unambiguous price, but we still reject it as the conservative
    # choice rather than trade on a zero-spread quote.
    assert _usable_quote(0.50, 0.40) is False
    assert _usable_quote(0.40, 0.40) is False


def test_mid_price_is_the_midpoint():
    assert _mid_price(0.05, 0.06) == pytest.approx(0.055)
    assert _mid_price(3.52, 4.32) == pytest.approx(3.92)


def test_mid_price_never_exceeds_the_ask():
    # The old last-trade premium did: CART 43P quoted premium 0.52 vs ask 0.42.
    assert _mid_price(0.0, 0.42) <= 0.42


def test_spread_pct_is_midpoint_relative():
    # bid 0.05 / ask 0.06 -> 0.01 / 0.055 = 18.18%
    assert _spread_pct(0.05, 0.06) == pytest.approx(18.1818, rel=1e-3)


def test_spread_pct_on_a_tight_market_is_small():
    assert _spread_pct(3.86, 4.47) < 20.0


def test_spread_pct_on_a_wide_market_is_large():
    # CART260925P00050000: bid 3.53 / ask 5.82 -> a 49% spread.
    assert _spread_pct(3.53, 5.82) > 45.0
