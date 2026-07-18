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
