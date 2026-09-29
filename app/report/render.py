"""The report has no data of its own: it formats the exact JSON `GET /analyses/{id}` returns."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.pipeline.model import fmt_ts

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["mmss"] = lambda s: fmt_ts(s) if s is not None else "–"
_env.filters["pct"] = lambda w: f"{w * 100:.0f}%"


def _band(score: float | None) -> str:
    if score is None:
        return "na"
    return "good" if score >= 7 else "mid" if score >= 5 else "bad"


_env.filters["band"] = _band


def render_report(analysis: dict) -> str:
    return _env.get_template("report.html").render(a=analysis)
