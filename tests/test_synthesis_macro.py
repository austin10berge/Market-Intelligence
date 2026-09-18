"""Tests for macro note rendering, wheel scorer parsing, and LLM fallback paths."""

from __future__ import annotations

import re
import tempfile
import os
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.screener.wheel_scorer import _parse_score_and_report
from src.synthesis.macro_note import (
    _DISCOVERY_DELIMITER,
    _PLAN_DELIMITER,
    _generate_forecast_and_plan,
    _render_note,
    find_latest_note,
    generate_macro_note,
)


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

class TestGenerateForecastAndPlan:
    async def _run(self, llm_output: str) -> tuple[str, str, str]:
        with patch("src.synthesis.macro_note.synthesize", new_callable=AsyncMock) as mock_syn:
            mock_syn.return_value = llm_output
            return await _generate_forecast_and_plan("macro context", {}, [], [])

    async def test_all_fallbacks_when_synthesize_empty(self):
        forecast, plan, discovery = await self._run("")
        assert "_LLM forecast unavailable._" in forecast
        assert "_LLM regime assessment unavailable._" in plan
        assert "_LLM discovery unavailable._" in discovery

    async def test_splits_on_both_delimiters(self):
        out = (
            f"Forecast body\n{_PLAN_DELIMITER}\nPlan body\n"
            f"{_DISCOVERY_DELIMITER}\n| NOW | ServiceNow |"
        )
        forecast, plan, discovery = await self._run(out)
        assert forecast == "Forecast body"
        assert plan == "Plan body"
        assert discovery == "| NOW | ServiceNow |"

    async def test_missing_discovery_delimiter_keeps_forecast_and_plan(self):
        forecast, plan, discovery = await self._run(f"Forecast\n{_PLAN_DELIMITER}\nPlan")
        assert forecast == "Forecast"
        assert plan == "Plan"
        assert "_LLM discovery unavailable._" in discovery

    async def test_missing_plan_delimiter_splits_on_regime_heading(self):
        forecast, plan, _ = await self._run("Forecast text\n\n**Regime:** sideways\nPlan text")
        assert forecast == "Forecast text"
        assert plan.startswith("**Regime:** sideways")

    async def test_unsplittable_output_goes_to_plan(self):
        forecast, plan, _ = await self._run("One block with no markers")
        assert "_LLM forecast unavailable._" in forecast
        assert plan == "One block with no markers"

    async def test_truncates_very_long_sections(self):
        long_forecast = "A sentence. " * 1000
        long_plan = "B sentence. " * 2000
        forecast, plan, _ = await self._run(f"{long_forecast}{_PLAN_DELIMITER}{long_plan}")
        assert len(forecast) < len(long_forecast)
        assert "[output truncated]" in forecast
        assert len(plan) < len(long_plan)
        assert "[output truncated]" in plan


class TestRenderDiscovery:
    _args = ({"spy_price": 550.0, "vix": 15.0}, "", "forecast", "plan", [], date(2026, 9, 7), 5)

    def test_discovery_section_rendered(self):
        note = _render_note(*self._args, discovery_text="| NOW | ServiceNow | fits |")
        assert "Exhibit 2F" in note
        assert "| NOW | ServiceNow | fits |" in note

    def test_discovery_section_hidden_for_fallback(self):
        note = _render_note(*self._args, discovery_text="_LLM discovery unavailable._")
        assert "Exhibit 2F" not in note

    def test_discovery_section_kept_when_a_row_says_unavailable(self):
        """Only the fallback sentinel hides the section — not the word in real content."""
        text = "| NOW | ServiceNow | fits | Est. drawdown unavailable |"
        note = _render_note(*self._args, discovery_text=text)
        assert "Exhibit 2F" in note
        assert text in note


# ── ISO-week naming ───────────────────────────────────────────────────────────


class TestISOWeekNaming:
    def test_render_note_uses_week_heading(self):
        snapshot = {"spy_price": 560.0, "vix": 17.0, "spy_vs_sma200": "+4.2%", "vix_regime": "normal"}
        note = _render_note(snapshot, "", "forecast", "regime", [], date(2026, 9, 7), 10)
        # date(2026, 9, 7) is ISO week 37
        assert "Week 37" in note or "2026-37" in note

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
    async def test_output_path_uses_iso_week(self):
        """generate_macro_note must write a file named YYYY-WW.md using ISO week number."""
        _snapshot_data = {
            "spy_price": 550.0,
            "spy_1d_ret": "+0.10%",
            "spy_5d_ret": "+1.20%",
            "spy_vs_sma200": "+5.00%",
            "spy_sma200": 522.0,
            "vix": 15.0,
            "vix_regime": "low vol",
        }
        with tempfile.TemporaryDirectory() as d:
            out_dir = Path(d)
            target = date(2026, 9, 7)  # ISO week 37
            with (
                patch("src.synthesis.macro_note.run_csp_scan", return_value={"candidates": []}),
                patch(
                    "src.synthesis.macro_note.build_macro_context_str",
                    new_callable=AsyncMock,
                    return_value="macro context",
                ),
                patch("src.synthesis.macro_note.fetch_spy_vix_snapshot", return_value=_snapshot_data),
                patch("src.synthesis.macro_note.fetch_wiki_events", return_value="wiki text"),
                patch("src.synthesis.macro_note._load_open_positions", return_value=[]),
                patch(
                    "src.synthesis.macro_note.score_wheel_candidates",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch("src.synthesis.macro_note._fetch_watchlist_snapshot", return_value=[]),
                patch(
                    "src.synthesis.macro_note._generate_forecast_and_plan",
                    new_callable=AsyncMock,
                    return_value=("forecast text", "regime plan text", "discovery text"),
                ),
            ):
                result = await generate_macro_note(out_dir=out_dir, target_week=target)
            assert result.name == "2026-37.md"
            assert (out_dir / "2026-37.md").exists()
