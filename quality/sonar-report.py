#!/usr/bin/env python3
"""Print the SonarQube quality report for the analysed projects.

The dashboard is nicer to browse, but a report you have to open a browser for is
a report that does not make it into a pull request. This prints the same numbers
where the rest of the tooling already lives.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("SONAR_URL", "http://localhost:9002").rstrip("/")
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sonar-token")

MEASURES = [
    "ncloc",
    "bugs",
    "vulnerabilities",
    "security_hotspots",
    "code_smells",
    "coverage",
    "line_coverage",
    "uncovered_lines",
    "duplicated_lines_density",
    "duplicated_blocks",
    "cognitive_complexity",
    "sqale_index",
    "reliability_rating",
    "security_rating",
    "sqale_rating",
]

LABELS = {
    "ncloc": "Lines of code",
    "bugs": "Bugs",
    "vulnerabilities": "Vulnerabilities",
    "security_hotspots": "Security hotspots",
    "code_smells": "Code smells",
    "coverage": "Coverage %",
    "line_coverage": "Line coverage %",
    "uncovered_lines": "Uncovered lines",
    "duplicated_lines_density": "Duplication %",
    "duplicated_blocks": "Duplicated blocks",
    "cognitive_complexity": "Cognitive complexity",
    "sqale_index": "Technical debt (min)",
    "reliability_rating": "Reliability",
    "security_rating": "Security",
    "sqale_rating": "Maintainability",
}

RATINGS = {"1.0": "A", "2.0": "B", "3.0": "C", "4.0": "D", "5.0": "E"}
SEVERITY_ORDER = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def token() -> str:
    if os.environ.get("SONAR_TOKEN"):
        return os.environ["SONAR_TOKEN"]
    try:
        with open(TOKEN_FILE, encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        sys.exit("no analysis token; run `make sonar-up` first")


def get(path: str) -> dict:
    request = urllib.request.Request(URL + path)
    # SonarQube takes a token as the HTTP basic username with an empty password.
    request.add_header("Authorization", "Basic " + _basic(token()))
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _basic(user: str) -> str:
    import base64

    return base64.b64encode(f"{user}:".encode()).decode()


def wait_for_processing(timeout: float = 120.0) -> None:
    """Block until SonarQube has finished ingesting every submitted analysis.

    Uploading a report and having it processed are two different events, and the
    scanner returns after the first one. Printing straight away reads the
    *previous* analysis and reports issues that were fixed minutes ago — which
    is worse than reporting nothing, because it looks like a result.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        status = get("/api/ce/activity_status")
        if status.get("pending", 0) == 0 and status.get("inProgress", 0) == 0:
            return
        time.sleep(1)

    print(f"{DIM}still processing after {timeout:.0f}s; numbers may lag{RESET}")


def report(project: str) -> int:
    try:
        measures = get(f"/api/measures/component?component={project}&metricKeys={','.join(MEASURES)}")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print(f"{BOLD}{project}{RESET}: not analysed yet — run `make sonar-scan`\n")
            return 0
        raise

    found = {m["metric"]: m["value"] for m in measures["component"]["measures"]}

    print(f"{BOLD}{project}{RESET}  {DIM}{URL}/dashboard?id={project}{RESET}")
    for metric in MEASURES:
        value = found.get(metric, "-")
        if metric.endswith("_rating"):
            value = RATINGS.get(value, value)
        print(f"    {LABELS[metric]:<24} {value}")

    issues = get(f"/api/issues/search?componentKeys={project}&resolved=false&ps=200")
    total = issues["total"]
    print(f"    {DIM}{total} open issue(s){RESET}")

    for issue in sorted(
        issues["issues"],
        key=lambda i: (SEVERITY_ORDER.get(i.get("severity", ""), 9), i["component"]),
    ):
        path = issue["component"].split(":", 1)[-1]
        print(f"      [{issue.get('severity', '?'):<8}] {path}:{issue.get('line', '-')}")
        print(f"                 {issue['message']}  {DIM}({issue['rule']}){RESET}")

    print()
    return total


def main() -> None:
    wait_for_processing()

    projects = sys.argv[1:] or ["scoring-processor-service"]
    for project in projects:
        report(project)


if __name__ == "__main__":
    main()
