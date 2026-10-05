"""Score every clip in the dataset and compare scores with views normalised by the account median.

Performance counters (views, likes, ...) are never sent to the service; they are joined here, afterwards.
The service only gets the title, the account, the platform and the sample id.

Resumable: each finished analysis is saved under --out/raw/<clip>.json and skipped on the next run
(pass --rerun to submit again). An analysis lost to a machine restart (`interrupted_by_restart`) is
resubmitted, up to --attempts times. Without `--fresh` an identical upload is answered from the cache.

usage:
  CLIP_API_URL=https://clip-scoring.fly.dev CLIP_API_AUTH=user:password \
    uv run python scripts/run_dataset.py [--only C01 C02] [--report-only]
writes --out/results.json and --out/report.md (default var/dataset/).
"""

import argparse
import csv
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx
from _client import BASE, submit, wait

DIMENSIONS = ["hook", "standalone_completeness", "clarity_payoff", "pacing_energy", "audio_quality", "on_screen_text"]
# Reach this far from the account median is discussed on its own, not inside the rank correlation.
OUTLIER_RATIO = 5.0
RETRYABLE_ERRORS = {"interrupted_by_restart"}


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _submit_with_retry(video: Path, metadata: dict, fresh: bool) -> dict:
    for attempt in range(5):
        try:
            return submit(video, metadata, fresh=fresh)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise
            time.sleep(5 * (attempt + 1))
    return submit(video, metadata, fresh=fresh)


def analyse(video: Path, metadata: dict, fresh: bool, attempts: int) -> tuple[dict, int]:
    for attempt in range(1, attempts + 1):
        accepted = _submit_with_retry(video, metadata, fresh)
        body = accepted if accepted["status"] in ("completed", "failed") else wait(accepted["id"])
        code = (body.get("error") or {}).get("code")
        if body["status"] == "completed" or code not in RETRYABLE_ERRORS:
            return body, attempt
        print(f"  {video.stem}: {code}, resubmitting")
    return body, attempts


def summarise(body: dict, row: dict, median_views: float | None) -> dict:
    overall = body.get("overall") or {}
    prov = body.get("provenance") or {}
    views = float(row["views"]) if row.get("views") else None
    return {
        "clip": row["sample_id"],
        "id": body["id"],
        "account": row["creator"],
        "title": row["title"],
        "group": row["group_id"],
        "duration_s": round(float(row["duration_seconds"]), 1),
        "age_days": float(row["age_days_at_snapshot"]),
        "status": body["status"],
        "error": (body.get("error") or {}).get("code"),
        "mode": body.get("mode"),
        "score": overall.get("score"),
        "raw_score": overall.get("raw_score"),
        "verdict": overall.get("verdict"),
        "capped": overall.get("capped"),
        "cap_reason": overall.get("cap_reason"),
        "summary": overall.get("summary"),
        "dimensions": {d["id"]: d["score"] for d in body.get("dimensions") or []},
        "top_fix": next(iter(body.get("priority_fixes") or []), None),
        "cost_usd": prov.get("cost_usd"),
        "duration_ms": prov.get("duration_ms"),
        "model": prov.get("model"),
        "versions": "/".join(str(prov.get(k)) for k in ("prompt_version", "rubric_version", "pipeline_version")),
        "views": views,
        "views_vs_median": round(views / median_views, 2) if views is not None and median_views else None,
    }


# --- statistics (stdlib only) -------------------------------------------------------------------


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return round(cov / (sx * sy), 2) if sx and sy else None


def _is_outlier(r: dict) -> bool:
    ratio = r["views_vs_median"]
    return ratio is not None and (ratio >= OUTLIER_RATIO or ratio <= 1 / OUTLIER_RATIO)


def _fmt(v, digits: int = 2) -> str:
    if v is None:
        return "–"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


# --- report -------------------------------------------------------------------------------------


def report(results: list[dict], medians: dict[str, float]) -> str:
    done = [r for r in results if r["status"] == "completed"]
    failed = [r for r in results if r["status"] != "completed"]
    accounts = sorted({r["account"] for r in results})
    costs = [r["cost_usd"] for r in done if r["cost_usd"] is not None]
    times = [r["duration_ms"] / 1000 for r in done if r["duration_ms"]]
    out = ["# Dataset run", ""]
    out += [
        (
            f"- Service: `{BASE}`; model(s): {', '.join(sorted({str(r['model']) for r in done}))}; "
            f"prompt/rubric/pipeline: {', '.join(sorted({r['versions'] for r in done}))}"
        ),
        f"- Clips: {len(results)} submitted, {len(done)} completed, {len(failed)} failed"
        + (f" ({', '.join(f'{r['clip']}: {r['error']}' for r in failed)})" if failed else ""),
    ]
    if costs:
        out.append(f"- Cost: total ${sum(costs):.4f}, mean ${statistics.mean(costs):.4f}, max ${max(costs):.4f} per clip")
    if times:
        out.append(f"- Processing time: median {statistics.median(times):.0f} s, max {max(times):.0f} s per clip")
    out.append("")

    out += ["## Verdicts", "", "| Account | median views (sample) | post | improve | skip | mean score | capped |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for acc in accounts + ["all"]:
        rows = [r for r in done if acc in ("all", r["account"])]
        c = Counter(r["verdict"] for r in rows)
        mean = statistics.mean(r["score"] for r in rows) if rows else None
        out.append(
            f"| {acc} | {_fmt(medians.get(acc), 0)} | {c['post']} | {c['improve']} | {c['skip']} | "
            f"{_fmt(mean, 1)} | {sum(1 for r in rows if r['capped'])} |"
        )
    out.append("")

    out += ["## Score against reach, within each account", "",
            (
                "Spearman rank correlation between the overall score and views ÷ account median. "
                f"Clips at ≥{OUTLIER_RATIO:g}× or ≤1/{OUTLIER_RATIO:g}× the median are outliers and are also shown excluded. "
                "With ~10 clips per account a correlation needs to be beyond about ±0.63 to be significant at p<0.05."
            ), "",
            "| Account | n | ρ(score, reach) | ρ without outliers | median reach: post | improve | skip |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for acc in accounts:
        rows = [r for r in done if r["account"] == acc and r["views_vs_median"] is not None]
        inl = [r for r in rows if not _is_outlier(r)]
        by_verdict = {
            v: statistics.median([r["views_vs_median"] for r in rows if r["verdict"] == v] or [math.nan])
            for v in ("post", "improve", "skip")
        }
        out.append(
            f"| {acc} | {len(rows)} | {_fmt(spearman([r['score'] for r in rows], [r['views_vs_median'] for r in rows]))} | "
            f"{_fmt(spearman([r['score'] for r in inl], [r['views_vs_median'] for r in inl]))} | "
            + " | ".join("–" if math.isnan(by_verdict[v]) else f"{by_verdict[v]:.2f}×" for v in ("post", "improve", "skip"))
            + " |"
        )
    out.append("")

    out += ["### Per dimension (ρ with reach, within account)", "",
            "| Dimension | " + " | ".join(accounts) + " |", "| --- |" + " ---: |" * len(accounts)]
    for dim in DIMENSIONS:
        cells = []
        for acc in accounts:
            rows = [r for r in done if r["account"] == acc and r["views_vs_median"] is not None
                    and r["dimensions"].get(dim) is not None]
            cells.append(_fmt(spearman([r["dimensions"][dim] for r in rows], [r["views_vs_median"] for r in rows])))
        out.append(f"| {dim} | " + " | ".join(cells) + " |")
    out.append("")

    out += ["### Mean dimension score per account", "",
            "| Dimension | " + " | ".join(accounts) + " |", "| --- |" + " ---: |" * len(accounts)]
    for dim in DIMENSIONS:
        cells = []
        for acc in accounts:
            vals = [r["dimensions"][dim] for r in done if r["account"] == acc and r["dimensions"].get(dim) is not None]
            cells.append(_fmt(statistics.mean(vals), 1) if vals else "–")
        out.append(f"| {dim} | " + " | ".join(cells) + " |")
    out.append("")

    outliers = [r for r in done if _is_outlier(r)]
    if outliers:
        out += ["## Reach outliers", "", "| Clip | Account | Title | reach | score | verdict |", "| --- | --- | --- | ---: | ---: | --- |"]
        for r in sorted(outliers, key=lambda r: -r["views_vs_median"]):
            out.append(f"| {r['clip']} | {r['account']} | {r['title']} | {r['views_vs_median']:.2f}× | {r['score']} | {r['verdict']} |")
        out.append("")

    groups = defaultdict(list)
    for r in done:
        groups[r["group"]].append(r)
    multi = {g: rs for g, rs in groups.items() if len(rs) > 1}
    if multi:
        out += ["## Same source, different clip", "",
                (
                    "Clips cut from the same episode share speaker, audio and setting, so a score spread inside a group "
                    "comes from the cut itself (hook, boundaries, payoff)."
                ), "",
                "| Group | clips | scores | reach |", "| --- | --- | --- | --- |"]
        for g, rs in sorted(multi.items()):
            rs.sort(key=lambda r: r["clip"])
            out.append(f"| {g} | {', '.join(r['clip'] for r in rs)} | {', '.join(str(r['score']) for r in rs)} | "
                       f"{', '.join(_fmt(r['views_vs_median']) + '×' for r in rs)} |")
        out.append("")

    out += ["## Every clip", "",
            "| Clip | Account | Title | s | score | verdict | cap | " + " | ".join(d.split("_")[0] for d in DIMENSIONS)
            + " | reach | age d |", "| --- | --- | --- | ---: | ---: | --- | --- |" + " ---: |" * (len(DIMENSIONS) + 2)]
    for r in results:
        dims = " | ".join(_fmt(r["dimensions"].get(d)) for d in DIMENSIONS)
        cap = (r["cap_reason"] or "yes") if r["capped"] else ""
        out.append(
            f"| [{r['clip']}]({BASE}/analyses/{r['id']}/report) | {r['account']} | {r['title']} | {r['duration_s']:.0f} | "
            f"{_fmt(r['score'])} | {r['verdict'] or r['error']} | {cap} | {dims} | {_fmt(r['views_vs_median'])}× | {r['age_days']:.0f} |"
        )
    out.append("")
    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=Path, default=Path("dataset"))
    p.add_argument("--out", type=Path, default=Path("var/dataset"))
    p.add_argument("--only", nargs="*", help="sample ids to run, e.g. C01 C02")
    p.add_argument("--rerun", action="store_true", help="submit again even when a saved result exists")
    p.add_argument("--fresh", action="store_true", help="bypass the service cache (new paid analysis)")
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--report-only", action="store_true", help="rebuild the report from saved results")
    a = p.parse_args()

    rows = _read_csv(a.dataset / "metadata.csv")
    medians = {r["creator"]: float(r["median_snapshot_views"]) for r in _read_csv(a.dataset / "account-context.csv")}
    raw_dir = a.out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for row in rows:
        clip = row["sample_id"]
        if a.only and clip not in a.only:
            continue
        saved = raw_dir / f"{clip}.json"
        if saved.exists() and not a.rerun:
            body = json.loads(saved.read_text())
        elif a.report_only:
            continue
        else:
            metadata = {"external_id": clip, "title": row["title"], "account": row["creator"], "platform": "tiktok"}
            started = time.monotonic()
            body, attempts = analyse(a.dataset / row["filename"], metadata, a.fresh, a.attempts)
            if body["status"] == "completed":
                saved.write_text(json.dumps(body, indent=2))
            print(f"{clip}: {body['status']} {(body.get('overall') or {}).get('score')} "
                  f"{(body.get('overall') or {}).get('verdict')} in {time.monotonic() - started:.0f}s, attempt {attempts}")
        results.append(summarise(body, row, medians.get(row["creator"])))

    (a.out / "results.json").write_text(json.dumps(results, indent=2))
    (a.out / "report.md").write_text(report(results, medians))
    print(f"wrote {a.out / 'results.json'} and {a.out / 'report.md'}")


if __name__ == "__main__":
    main()
