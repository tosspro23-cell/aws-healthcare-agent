"""Turns `care_agent.eval`'s per-run `EvalSummary` into a small,
append-only history log (`docs/eval_history.jsonl`) and a hand-rolled
SVG line chart (`docs/eval_trend.svg`) plotted straight from that log --
no charting library, since the whole chart is four SVG primitives
(line, polyline, circle, text) and pulling in matplotlib (this
project's own dependency list is otherwise empty -- see
`pyproject.toml`) for four shapes would be a worse trade than writing
them directly.

The log is deliberately separate from `docs/EVAL_HISTORY.md`'s own
human-readable markdown table: both are written from the same
`EvalSummary` in `scripts/update_eval_history.py`'s `main()`, but the
JSONL log exists purely to be re-read and re-plotted by this module,
not to be hand-edited or read as prose -- parsing the markdown back out
would mean re-deriving structured data from a format designed for a
human reader, the same class of mistake as the project's own
ordinal-list-marker regex bug (see `docs/DECISIONS.md`), just on the
output side instead of the input side.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from care_agent.eval import EvalSummary, git_short_sha


@dataclass(frozen=True)
class HistoryRecord:
    date: str
    commit: str
    narrator_backend: str
    total_checks: int
    passed_checks: int
    pass_rate: float
    total_skipped: int


def build_history_record(
    summary: EvalSummary, *, narrator_backend: str, when: date | None = None, commit: str | None = None
) -> HistoryRecord:
    return HistoryRecord(
        date=(when or date.today()).isoformat(),
        commit=commit or git_short_sha(),
        narrator_backend=narrator_backend,
        total_checks=summary.total_checks,
        passed_checks=summary.passed_checks,
        pass_rate=summary.pass_rate,
        total_skipped=summary.total_skipped,
    )


def append_history_record(record: HistoryRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record)) + "\n")


def read_history_records(path: Path) -> list[HistoryRecord]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(HistoryRecord(**json.loads(line)))
    return records


_WIDTH = 640
_HEIGHT = 220
_PAD_LEFT = 50
_PAD_RIGHT = 20
_PAD_TOP = 24
_PAD_BOTTOM = 30
# Colored by backend, not by pass/fail -- pass_rate's own y-position
# already shows the result; the color's job is to distinguish the free,
# CI-run mock narrator from an occasional hand-run, real-money bedrock
# check, matching the distinction docs/EVAL_HISTORY.md's own headings
# already draw per entry.
_BACKEND_COLORS = {"mock": "#2f7d32", "bedrock": "#b8590a"}
_DEFAULT_COLOR = "#4a5568"


def render_svg_trend(records: list[HistoryRecord]) -> str:
    if not records:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}" '
            f'width="{_WIDTH}" height="{_HEIGHT}" font-family="sans-serif">'
            f'<rect width="{_WIDTH}" height="{_HEIGHT}" fill="#f8f9fa"/>'
            f'<text x="{_WIDTH / 2}" y="{_HEIGHT / 2}" text-anchor="middle" font-size="14" fill="#555">'
            "No eval history recorded yet.</text></svg>"
        )

    plot_w = _WIDTH - _PAD_LEFT - _PAD_RIGHT
    plot_h = _HEIGHT - _PAD_TOP - _PAD_BOTTOM

    def x_at(i: int) -> float:
        return _PAD_LEFT + plot_w / 2 if len(records) == 1 else _PAD_LEFT + plot_w * i / (len(records) - 1)

    def y_at(pass_rate: float) -> float:
        return _PAD_TOP + plot_h * (1 - pass_rate)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}" '
        f'width="{_WIDTH}" height="{_HEIGHT}" font-family="sans-serif">',
        f'<rect width="{_WIDTH}" height="{_HEIGHT}" fill="#f8f9fa"/>',
    ]

    for pct in (0.0, 0.5, 1.0):
        y = y_at(pct)
        parts.append(f'<line x1="{_PAD_LEFT}" y1="{y:.1f}" x2="{_WIDTH - _PAD_RIGHT}" y2="{y:.1f}" stroke="#dcdcdc" stroke-width="1"/>')
        parts.append(f'<text x="{_PAD_LEFT - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#666">{pct:.0%}</text>')

    points = [(x_at(i), y_at(r.pass_rate)) for i, r in enumerate(records)]
    if len(points) > 1:
        polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        parts.append(f'<polyline points="{polyline}" fill="none" stroke="#94a3b8" stroke-width="2"/>')

    for (x, y), record in zip(points, records, strict=True):
        color = _BACKEND_COLORS.get(record.narrator_backend, _DEFAULT_COLOR)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}">'
            f"<title>{record.date} {record.commit} ({record.narrator_backend}): {record.pass_rate:.0%}</title>"
            "</circle>"
        )

    first, last = records[0], records[-1]
    parts.append(f'<text x="{_PAD_LEFT}" y="{_HEIGHT - 8}" font-size="11" fill="#666">{first.date} · {first.commit}</text>')
    parts.append(
        f'<text x="{_WIDTH - _PAD_RIGHT}" y="{_HEIGHT - 8}" text-anchor="end" font-size="11" fill="#666">'
        f"{last.date} · {last.commit} · {last.pass_rate:.0%}</text>"
    )

    backends_present = sorted({r.narrator_backend for r in records})
    if len(backends_present) > 1:
        legend_y = _PAD_TOP - 12
        offset = 0.0
        for backend in backends_present:
            color = _BACKEND_COLORS.get(backend, _DEFAULT_COLOR)
            parts.append(f'<circle cx="{_PAD_LEFT + offset:.1f}" cy="{legend_y}" r="4" fill="{color}"/>')
            parts.append(f'<text x="{_PAD_LEFT + offset + 8:.1f}" y="{legend_y + 4}" font-size="10" fill="#666">{backend}</text>')
            offset += 70

    parts.append("</svg>")
    return "\n".join(parts)
