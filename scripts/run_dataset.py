"""Run every clip in a folder and compare scores with views normalised by the account median.

Performance numbers are never sent to the service; they are only joined here, afterwards.

usage:
  uv run python scripts/run_dataset.py data/videos --metadata data/clips.csv --accounts data/account-context.csv
Column names are flags because the dataset layout isn't fixed yet.
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

from _client import submit, wait


def _read_csv(path: Path | None) -> list[dict]:
    if not path:
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("videos", type=Path)
    p.add_argument("--metadata", type=Path)
    p.add_argument("--accounts", type=Path)
    p.add_argument("--id-col", default="sample_id")
    p.add_argument("--account-col", default="account")
    p.add_argument("--title-col", default="title")
    p.add_argument("--views-col", default="views")
    p.add_argument("--median-col", default="median_views")
    p.add_argument("--out", type=Path, default=Path("var/dataset_results.json"))
    a = p.parse_args()

    meta_by_id = {r[a.id_col]: r for r in _read_csv(a.metadata)}
    medians = {r[a.account_col]: float(r[a.median_col]) for r in _read_csv(a.accounts) if r.get(a.median_col)}

    results = []
    for video in sorted(a.videos.glob("*.mp4")):
        row = meta_by_id.get(video.stem, {})
        metadata = {"external_id": video.stem, "account": row.get(a.account_col), "title": row.get(a.title_col)}
        body = wait(submit(video, {k: v for k, v in metadata.items() if v})["id"])
        views = float(row[a.views_col]) if row.get(a.views_col) else None
        median = medians.get(row.get(a.account_col))
        entry = {
            "clip": video.stem,
            "account": row.get(a.account_col),
            "status": body["status"],
            "score": (body.get("overall") or {}).get("score"),
            "verdict": (body.get("overall") or {}).get("verdict"),
            "capped": (body.get("overall") or {}).get("capped"),
            "cost_usd": (body.get("provenance") or {}).get("cost_usd"),
            "views_vs_median": round(views / median, 2) if views and median else None,
        }
        results.append(entry)
        print(json.dumps(entry))

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(results, indent=2))

    # Directional check, within each account only.
    by_account = defaultdict(list)
    for r in results:
        if r["score"] is not None and r["views_vs_median"] is not None:
            by_account[r["account"]].append(r)
    for account, rows in by_account.items():
        if len(rows) < 3:
            continue
        rows.sort(key=lambda r: r["score"], reverse=True)
        half = len(rows) // 2
        top = statistics.median(r["views_vs_median"] for r in rows[:half])
        bottom = statistics.median(r["views_vs_median"] for r in rows[half:])
        print(f"{account}: top-half median views/median {top:.2f} vs bottom-half {bottom:.2f}")

    costs = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
    if costs:
        print(f"average cost per clip: ${statistics.mean(costs):.4f}")


if __name__ == "__main__":
    main()
