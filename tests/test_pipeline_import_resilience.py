"""Regression test: src.main (and anything importing it, e.g. src.api.main)
must not crash at import time if the optional algo-detective options-chain
module is unavailable — that step is meant to be non-fatal at runtime, not a
hard startup dependency. See docs/handoffs for the 821c076 prod incident this
guards against.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def algo_detective_options_chain_unavailable(monkeypatch):
    """Simulate `src.algo_detective.options_chain` not being importable,
    as it would be if the module were never shipped (e.g. missing from a
    deployment)."""
    monkeypatch.setitem(sys.modules, "src.algo_detective.options_chain", None)
    monkeypatch.delitem(sys.modules, "src.main", raising=False)
    yield
    monkeypatch.delitem(sys.modules, "src.main", raising=False)


def test_main_importable_without_algo_detective_options_chain(
    algo_detective_options_chain_unavailable,
):
    import src.main  # noqa: F401 — importability is the assertion
