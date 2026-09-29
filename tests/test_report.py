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
