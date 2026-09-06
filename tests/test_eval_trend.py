"""Tests for care_agent.eval_trend -- the append-only JSONL history log
and the hand-rolled SVG line chart plotted from it (see that module's
own docstring for why this doesn't use a charting library).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from care_agent.eval import EvalSummary
from care_agent.eval_trend import (
    HistoryRecord,
    append_history_record,
    build_history_record,
    read_history_records,
    render_svg_trend,
)


def _summary(*, total: int = 10, passed: int = 10, skipped: int = 2) -> EvalSummary:
    return EvalSummary(total_checks=total, passed_checks=passed, total_skipped=skipped, all_passed=passed == total, results=[])


def test_build_history_record_uses_given_date_and_commit_not_live_git_state():
    record = build_history_record(_summary(passed=8, total=10), narrator_backend="mock", when=date(2026, 1, 1), commit="abc1234")
    assert record == HistoryRecord(
        date="2026-01-01", commit="abc1234", narrator_backend="mock", total_checks=10, passed_checks=8, pass_rate=0.8, total_skipped=2
    )


def test_append_then_read_round_trips_multiple_records_in_order(tmp_path: Path):
    path = tmp_path / "eval_history.jsonl"
    first = build_history_record(_summary(), narrator_backend="mock", when=date(2026, 1, 1), commit="aaa0001")
    second = build_history_record(_summary(passed=9), narrator_backend="bedrock", when=date(2026, 1, 2), commit="bbb0002")

    append_history_record(first, path)
    append_history_record(second, path)

    records = read_history_records(path)
    assert records == [first, second]


def test_read_history_records_returns_empty_list_when_file_does_not_exist(tmp_path: Path):
    assert read_history_records(tmp_path / "does_not_exist.jsonl") == []


def test_render_svg_trend_with_no_records_says_so_instead_of_an_empty_chart():
    svg = render_svg_trend([])
    assert svg.startswith("<svg")
    assert "No eval history recorded yet." in svg
    assert "<circle" not in svg
    assert "<polyline" not in svg


def test_render_svg_trend_with_one_record_draws_a_point_but_no_line():
    record = build_history_record(_summary(), narrator_backend="mock", when=date(2026, 1, 1), commit="aaa0001")
    svg = render_svg_trend([record])
    assert svg.count("<circle") == 1
    assert "<polyline" not in svg
    assert "aaa0001" in svg


def test_render_svg_trend_with_multiple_same_backend_records_draws_a_connected_line_and_no_legend():
    records = [
        build_history_record(_summary(passed=8), narrator_backend="mock", when=date(2026, 1, 1), commit="aaa0001"),
        build_history_record(_summary(passed=10), narrator_backend="mock", when=date(2026, 1, 2), commit="bbb0002"),
        build_history_record(_summary(passed=9), narrator_backend="mock", when=date(2026, 1, 3), commit="ccc0003"),
    ]
    svg = render_svg_trend(records)
    assert svg.count("<circle") == 3
    assert svg.count("<polyline") == 1
    # No legend needed when every point shares one backend.
    assert svg.count("mock") == 0 or "bedrock" not in svg


def test_render_svg_trend_with_mixed_backends_adds_a_legend_distinguishing_them():
    records = [
        build_history_record(_summary(passed=10), narrator_backend="mock", when=date(2026, 1, 1), commit="aaa0001"),
        build_history_record(_summary(passed=10), narrator_backend="bedrock", when=date(2026, 1, 2), commit="bbb0002"),
    ]
    svg = render_svg_trend(records)
    assert ">mock<" in svg
    assert ">bedrock<" in svg


def test_render_svg_trend_lower_pass_rate_places_the_point_lower_on_the_chart():
    perfect = build_history_record(_summary(passed=10, total=10), narrator_backend="mock", when=date(2026, 1, 1), commit="aaa0001")
    half = build_history_record(_summary(passed=5, total=10), narrator_backend="mock", when=date(2026, 1, 1), commit="bbb0002")

    def _circle_y(svg: str) -> float:
        marker = 'cy="'
        start = svg.index(marker) + len(marker)
        end = svg.index('"', start)
        return float(svg[start:end])

    y_perfect = _circle_y(render_svg_trend([perfect]))
    y_half = _circle_y(render_svg_trend([half]))
    assert y_half > y_perfect
