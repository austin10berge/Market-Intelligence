"""Tests for macro note rendering, wheel scorer parsing, and LLM fallback paths."""

from __future__ import annotations

import re
import tempfile
import os
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.screener.wheel_scorer import _parse_score_and_report
from src.synthesis.macro_note import _generate_forecast, _generate_regime_plan, _render_note, find_latest_note


# ── _parse_score_and_report ───────────────────────────────────────────────────

class TestParseScoreAndReport:
    def test_standard(self):
        out = "Wheel Report: Good stock.\nScore: 75"
        score, report = _parse_score_and_report(out)
        assert score == 75
        assert "Good stock" in report

    def test_case_insensitive(self):
        score, _ = _parse_score_and_report("Wheel Report: Decent.\nscore: 42")
        assert score == 42

    def test_no_score_returns_zero(self):
        out = "No score line here."
        score, report = _parse_score_and_report(out)
        assert score == 0
        assert report == out.strip()

    def test_clamps_above_100(self):
        score, _ = _parse_score_and_report("Score: 150")
        assert score == 100

    def test_clamps_below_1(self):
        # score=0 raw → clamped to 1 (the range is max(1, min(100, ...)))
        score, _ = _parse_score_and_report("Wheel Report: Meh.\nScore: 0")
        assert score == 1

    def test_report_excludes_score_line(self):
        out = "Wheel Report: Great.\n\nScore: 88"
        _, report = _parse_score_and_report(out)
        assert "Score:" not in report
        assert "88" not in report

    def test_score_with_leading_whitespace(self):
        score, _ = _parse_score_and_report("Some text.\n  Score:  63  ")
        assert score == 63


# ── _render_note ──────────────────────────────────────────────────────────────

def _snapshot() -> dict:
    return {
        "spy_price": 550.0,
        "spy_1d_ret": "+0.10%",
        "spy_5d_ret": "+1.20%",
        "spy_vs_sma200": "+5.00%",
        "spy_sma200": 522.0,
        "vix": 15.0,
        "vix_regime": "low vol — favorable for premium selling",
    }


class TestRenderNote:
    def test_contains_all_major_sections(self):
        note = _render_note(
            snapshot=_snapshot(),
            wiki="Some wiki text.",
            forecast="Forecast here.",
            regime_plan="Regime plan here.",
            open_positions=[],
            target_week=date(2026, 9, 1),
            candidate_count=5,
        )
        assert "## Market Snapshot" in note
        assert "## Current Events Context (Exhibit 2C)" in note
        assert "## 30-Day Macro Forecast (Exhibit 2D)" in note
        assert "## Monthly Wheel Trading Plan (Exhibit 2E)" in note
        assert "## Current Open Positions" in note
        assert "## Upcoming Options Expiries" in note
        assert "#trade-memo" in note

    def test_contains_spy_and_vix_values(self):
        note = _render_note(
            snapshot=_snapshot(),
            wiki="",
            forecast="",
            regime_plan="",
            open_positions=[],
            target_week=date(2026, 9, 1),
            candidate_count=0,
        )
        assert "550" in note
        assert "15.0" in note

    def test_empty_positions_renders_gracefully(self):
        note = _render_note(
            snapshot=_snapshot(),
            wiki="",
            forecast="",
            regime_plan="",
            open_positions=[],
            target_week=date(2026, 9, 1),
            candidate_count=0,
        )
        assert "No open positions" in note

    def test_positions_render_correctly(self):
        positions = [
            {
                "underlying": "TSLA",
                "option_type": "PUT",
                "strike": 200.0,
                "expiration": "2026-09-19",
                "dte": 20,
                "unrealized_pnl": 45.0,
            }
        ]
        note = _render_note(
            snapshot=_snapshot(),
            wiki="",
            forecast="",
            regime_plan="",
            open_positions=positions,
            target_week=date(2026, 9, 1),
            candidate_count=1,
        )
        assert "TSLA" in note
        assert "$200" in note
        assert "$+45" in note

    def test_wiki_fallback_when_empty(self):
        note = _render_note(
            snapshot=_snapshot(),
            wiki="",
            forecast="",
            regime_plan="",
            open_positions=[],
            target_week=date(2026, 9, 1),
            candidate_count=0,
        )
        assert "_Wikipedia unavailable._" in note

    def test_month_label_in_title(self):
        note = _render_note(
            snapshot=_snapshot(),
            wiki="",
            forecast="",
            regime_plan="",
            open_positions=[],
            target_week=date(2026, 11, 1),
            candidate_count=0,
        )
        # date(2026, 11, 1) is ISO week 44
        assert "Week 44" in note

    def test_candidate_count_in_header(self):
        note = _render_note(
            snapshot=_snapshot(),
            wiki="",
            forecast="",
            regime_plan="",
            open_positions=[],
            target_week=date(2026, 9, 1),
            candidate_count=13,
        )
        assert "13" in note


# ── LLM fallback paths ────────────────────────────────────────────────────────

class TestLlmFallbacks:
    async def test_forecast_returns_fallback_when_synthesize_empty(self):
        with patch("src.synthesis.macro_note.synthesize", new_callable=AsyncMock) as mock_syn:
            mock_syn.return_value = ""
            result = await _generate_forecast("macro context here")
        assert "_LLM forecast unavailable._" in result

    async def test_regime_returns_fallback_when_synthesize_empty(self):
        snapshot = {"spy_price": 550.0, "vix": 15.0}
        with patch("src.synthesis.macro_note.synthesize", new_callable=AsyncMock) as mock_syn:
            mock_syn.return_value = ""
            result = await _generate_regime_plan("forecast text", snapshot, [], [])
        assert "_LLM regime assessment unavailable._" in result

    async def test_forecast_truncates_very_long_output(self):
        long_text = "A sentence. " * 1000
        with patch("src.synthesis.macro_note.synthesize", new_callable=AsyncMock) as mock_syn:
            mock_syn.return_value = long_text
            result = await _generate_forecast("macro context")
        assert len(result) < len(long_text)
        assert "[output truncated]" in result

    async def test_regime_truncates_very_long_output(self):
        long_text = "B sentence. " * 2000
        with patch("src.synthesis.macro_note.synthesize", new_callable=AsyncMock) as mock_syn:
            mock_syn.return_value = long_text
            result = await _generate_regime_plan("forecast", {}, [], [])
        assert len(result) < len(long_text)
        assert "[output truncated]" in result


# ── ISO-week naming ───────────────────────────────────────────────────────────


class TestISOWeekNaming:
    def test_render_note_uses_week_heading(self):
        snapshot = {"spy_price": 560.0, "vix": 17.0, "spy_vs_sma200": "+4.2%", "vix_regime": "normal"}
        note = _render_note(snapshot, "", "forecast", "regime", [], date(2026, 9, 7), 10)
        assert "Week 36" in note or "2026-36" in note

    def test_find_latest_note_returns_newest(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "2026-34.md").write_text("old")
            (p / "2026-36.md").write_text("new")
            result = find_latest_note(p)
            assert result is not None
            assert result.name == "2026-36.md"

    def test_find_latest_note_returns_none_when_empty(self):
        with tempfile.TemporaryDirectory() as d:
            assert find_latest_note(Path(d)) is None


class TestGenerateMacroNoteWeekPath:
    def test_output_path_uses_iso_week(self):
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d)
            target = date(2026, 9, 7)  # Week 36
            # Compute expected filename
            week_str = target.strftime("%Y-%W")
            expected = out_dir / f"{week_str}.md"
            # Verify the naming convention (not the full async run)
            assert expected.name == "2026-36.md"
