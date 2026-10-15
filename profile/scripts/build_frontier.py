"""Build the Homework 9 Part E frontier from saved development results.

    .venv/bin/python profile/scripts/build_frontier.py

One row for the Homework 8 final run and one for each Homework 9 Part B and C run, all on
the development cases with muse-1-3. A configuration is dominated when another has an
equal or higher development score and an equal or lower cost, and is better on at least
one of the two, the same rule as optimize.workflow.dominates in Homework 8. Reads saved
results only: no model call.

Runs made before the cached input price was added (commit 91623f6) were billed with every
input token at the full price; they are left as billed.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "optimize/results"
OUTPUT = REPO / "profile/results/frontier.csv"
CANDIDATES = ["final", "fewer-tokens", "control-no-trim", "cache-before", "cache-after"]


def latest(candidate: str) -> dict:
    files = sorted(RESULTS.glob(f"development-{candidate}-*.json"))
    if not files:
        raise SystemExit(f"no development result for {candidate}")
    return json.loads(files[-1].read_text())


def dominates(a: dict, b: dict) -> bool:
    better_or_equal = (a["development_score"] >= b["development_score"]
                       and a["cost_per_100_conversations_usd"] <= b["cost_per_100_conversations_usd"])
    strictly_better = (a["development_score"] > b["development_score"]
                       or a["cost_per_100_conversations_usd"] < b["cost_per_100_conversations_usd"])
    return better_or_equal and strictly_better


def main() -> None:
    rows = []
    for candidate in CANDIDATES:
        d = latest(candidate)
        rows.append({
            "candidate": "hw8-final" if candidate == "final" else candidate,
            "model": d["model"],
            "development_score": d["score"],
            "write_pass_5": d["write_pass_5"],
            "cost_per_100_conversations_usd": d["cost_per_100_conversations_usd"],
            "median_latency_seconds": d["median_latency_seconds"],
        })
    for row in rows:
        dominated = any(dominates(other, row) for other in rows if other is not row)
        row["frontier_status"] = "dominated" if dominated else "frontier"

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        print(f"{r['candidate']:16} {r['model']:9} score {r['development_score']:.3f}  "
              f"wp5 {r['write_pass_5']}  ${r['cost_per_100_conversations_usd']:.4f}  "
              f"{r['median_latency_seconds']:5.1f} s  {r['frontier_status']}")
    print(f"Saved {OUTPUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
