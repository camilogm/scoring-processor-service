"""Repeatability: N fresh runs per clip; report score spread per dimension and verdict flips.

usage: uv run python scripts/repeatability.py data/videos/C01.mp4 data/videos/C12.mp4 --runs 5
"""

import argparse
import statistics
from collections import defaultdict
from pathlib import Path

from _client import submit, wait


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("videos", nargs="+", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    for video in args.videos:
        scores: dict[str, list] = defaultdict(list)
        verdicts, overall = [], []
        for i in range(args.runs):
            body = wait(submit(video, {"external_id": f"repeat-{i}"}, fresh=True)["id"])
            if body["status"] != "completed":
                print(f"{video.name} run {i}: failed {body['error']}")
                continue
            verdicts.append(body["overall"]["verdict"])
            overall.append(body["overall"]["score"])
            for d in body["dimensions"]:
                if d["score"] is not None:
                    scores[d["id"]].append(d["score"])

        print(f"\n{video.name}: {len(overall)} completed runs")
        print(f"  overall  {overall}  spread {max(overall) - min(overall):.1f}" if overall else "  no runs")
        print(f"  verdicts {verdicts}  flips: {'YES' if len(set(verdicts)) > 1 else 'no'}")
        for dim, vals in scores.items():
            sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            print(f"  {dim:<15} {vals}  range {max(vals) - min(vals)}  sd {sd:.2f}")


if __name__ == "__main__":
    main()
