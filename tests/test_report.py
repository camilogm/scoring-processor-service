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


def _footer(provenance: dict) -> str:
    html = render_report({**EXAMPLE, "provenance": provenance})
    return re.search(r"<footer>(.*?)</footer>", html, flags=re.DOTALL).group(1)


def test_footer_shows_the_frame_budget():
    assert "up to 16 frames" in _footer(EXAMPLE["provenance"])
    assert "no frames" in _footer({**EXAMPLE["provenance"], "vision": False})


def test_footer_renders_provenance_from_before_the_frame_fields():
    old = {k: v for k, v in EXAMPLE["provenance"].items() if k not in ("vision", "max_frames")}

    assert "frames" not in _footer(old)
