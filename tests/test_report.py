import json
import re
from pathlib import Path

from app.report.render import render_report

EXAMPLE = json.loads((Path(__file__).parents[1] / "docs" / "report-example.json").read_text())


def test_signal_rows_are_labelled_with_row_headers():
    html = render_report(EXAMPLE)

    rows = re.findall(r"<tr>(.*?)</tr>", html, flags=re.DOTALL)
    assert rows
    assert all(row.lstrip().startswith('<th scope="row">') for row in rows)


def _format_section(analysis: dict) -> str:
    html = render_report(analysis)
    match = re.search(r'<section class="format"[^>]*>(.*?)</section>', html, flags=re.DOTALL)
    return match.group(1) if match else ""


def test_format_section_confirms_an_optimal_clip():
    section = _format_section(EXAMPLE)

    assert "1080×1920" in section
    assert "9:16" in section
    assert "Instagram and TikTok" in section


def test_format_section_lists_issues_with_their_fix():
    check = {**EXAMPLE["format_check"], "status": "not_optimal", "aspect_ratio": "16:9", "orientation": "horizontal",
             "issues": [{"check": "aspect_ratio", "status": "not_optimal", "message": "Horizontal 16:9.",
                         "fix": "Reframe to 9:16."}]}

    section = _format_section({**EXAMPLE, "format_check": check})

    assert "Horizontal 16:9." in section
    assert "Reframe to 9:16." in section
    assert "Not optimal" in section


def test_format_section_also_shows_while_the_analysis_is_pending():
    pending = {k: EXAMPLE[k] for k in ("id", "input", "metadata", "format_check")}

    assert "9:16" in _format_section({**pending, "status": "queued", "current_step": None, "error": None})


def _footer(provenance: dict) -> str:
    html = render_report({**EXAMPLE, "provenance": provenance})
    return re.search(r"<footer>(.*?)</footer>", html, flags=re.DOTALL).group(1)


def test_footer_shows_the_frame_budget():
    assert "up to 16 frames" in _footer(EXAMPLE["provenance"])
    assert "no frames" in _footer({**EXAMPLE["provenance"], "vision": False})


def test_footer_renders_provenance_from_before_the_frame_fields():
    old = {k: v for k, v in EXAMPLE["provenance"].items() if k not in ("vision", "max_frames")}

    assert "frames" not in _footer(old)
