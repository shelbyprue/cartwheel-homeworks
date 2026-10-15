"""Score the cascade at several thresholds on the Homework 5 dev labels (Part D, step 2).

    .venv/bin/python cascade/thresholds.py

Reads cascade/results/proxy_dev.jsonl, the frozen oracle's saved verdicts
(`irrelevant_policy_detail-v6`) and the human labels. No model call.

The cascade keeps the proxy's verdict when its confidence is at or above the threshold,
and uses the oracle's verdict otherwise. Cost per 1,000 verdicts counts the proxy on every
example (it always runs first) plus the oracle on the examples it is handed. Proxy tokens
are the ones its API calls reported. The oracle's tokens are estimated on the same
conversation: its input is the proxy's input minus the one added confidence sentence (the
prompts are otherwise identical), and its output is that input times the oracle's measured
output-to-input ratio (profile/results/judge_usage_sample.json). Prices are the $1-per-million
placeholders in optimize/config.json, so cost measures tokens.

Writes cascade/results/thresholds.csv with the handout's columns, and
cascade/results/thresholds_detail.csv with the missed failures added.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
RESULTS = REPO / "cascade/results"
THRESHOLDS = [0.0, 0.5, 0.8, 0.9, 0.95, 0.97, 0.98, 0.99, 1.0]


def load_inputs(split: str):
    proxy = {r["trace_id"]: r for r in map(json.loads,
             (RESULTS / f"proxy_{split}.jsonl").read_text().splitlines()) if r}
    judge = json.loads((REPO / "analysis/state/judges/irrelevant_policy_detail-v6.json").read_text())
    oracle = {t: ("pass" if v == 1 else "fail")
              for t, v in judge["predictions"][judge["prompt_hash"]].items()}
    human = {}
    for line in (REPO / "analysis/state/hw5_labels/irrelevant_policy_detail.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            human[r["trace_id"]] = "pass" if r["label"] == 1 else "fail"
    ids = json.loads((REPO / "analysis/state/splits.json").read_text())[judge["mode"]][split]
    missing = [t for t in ids if t not in proxy]
    if missing:
        raise SystemExit(f"{len(missing)} {split} conversations have no proxy verdict yet")
    return ids, proxy, oracle, human


def prices():
    config = json.loads((REPO / "optimize/config.json").read_text())["prices_per_million_tokens_usd"]
    return config


def proxy_cost(row: dict, price: dict) -> float:
    return sum(a["input_tokens"] * price["input"] + a["output_tokens"] * price["output"]
               for a in row["attempts"]) / 1_000_000


def oracle_cost(row: dict, price: dict) -> float:
    """The oracle's estimated cost on the conversation the proxy judged in `row`."""
    from cascade.run_proxy import CONFIDENCE_INSTRUCTION
    ratio = json.loads((REPO / "profile/results/judge_usage_sample.json").read_text())["output_to_input_ratio"]
    input_tokens = row["attempts"][0]["input_tokens"] - round(len(CONFIDENCE_INSTRUCTION) / 4)
    return (input_tokens * price["input"] + input_tokens * ratio * price["output"]) / 1_000_000


def evaluate(threshold: float, ids, proxy, oracle, human, p_price, o_price) -> dict:
    kept = agree_o = agree_h = missed_o = missed_h = 0
    cost = 0.0
    for t in ids:
        row = proxy[t]
        cost += proxy_cost(row, p_price)
        use_proxy = row["verdict"] is not None and row["confidence"] is not None \
            and row["confidence"] >= threshold
        verdict = row["verdict"] if use_proxy else oracle[t]
        if not use_proxy:
            cost += oracle_cost(row, o_price)
        kept += use_proxy
        agree_o += verdict == oracle[t]
        agree_h += verdict == human[t]
        missed_o += oracle[t] == "fail" and verdict == "pass"
        missed_h += human[t] == "fail" and verdict == "pass"
    n = len(ids)
    return {
        "threshold": threshold,
        "agreement_with_oracle": round(agree_o / n, 4),
        "agreement_with_human_labels": round(agree_h / n, 4),
        "proxy_share": round(kept / n, 4),
        "cost_per_1000_verdicts_usd": round(cost / n * 1000, 4),
        "missed_oracle_fails": missed_o,
        "missed_human_fails": missed_h,
    }


def main() -> None:
    ids, proxy, oracle, human = load_inputs("dev")
    price = prices()
    rows = [evaluate(t, ids, proxy, oracle, human, price["muse-1-2"], price["muse-1-1"]) for t in THRESHOLDS]
    oracle_alone = sum(oracle_cost(proxy[t], price["muse-1-1"]) for t in ids) / len(ids) * 1000

    fields = ["threshold", "agreement_with_oracle", "agreement_with_human_labels",
              "proxy_share", "cost_per_1000_verdicts_usd"]
    with (RESULTS / "thresholds.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with (RESULTS / "thresholds_detail.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields + ["missed_oracle_fails", "missed_human_fails"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"dev: {len(ids)} conversations, oracle fails {sum(oracle[t] == 'fail' for t in ids)}, "
          f"human fails {sum(human[t] == 'fail' for t in ids)}")
    print(f"oracle alone: ${oracle_alone:.4f} per 1,000 verdicts (estimated on the same conversations)\n")
    print(f"{'threshold':>9} {'agree oracle':>13} {'agree human':>12} {'proxy share':>12} "
          f"{'$/1,000':>8} {'missed oracle fails':>20}")
    for r in rows:
        print(f"{r['threshold']:>9} {r['agreement_with_oracle']:>13} {r['agreement_with_human_labels']:>12} "
              f"{r['proxy_share']:>12} {r['cost_per_1000_verdicts_usd']:>8} {r['missed_oracle_fails']:>20}")
    print(f"\nSaved {(RESULTS / 'thresholds.csv').relative_to(REPO)} and thresholds_detail.csv")


if __name__ == "__main__":
    main()
