"""Check the chosen threshold once on the Homework 5 test labels (Part D, step 3).

    .venv/bin/python cascade/check.py

Reads the threshold locked in cascade/results/chosen_threshold.json (chosen on dev), the
proxy's test verdicts in cascade/results/proxy_test.jsonl, the oracle's saved verdicts and
the human labels. No model call. Writes cascade/results/check.json. The threshold is not
changed after this result.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from cascade.thresholds import evaluate, load_inputs, oracle_cost, prices  # noqa: E402

RESULTS = REPO / "cascade/results"


def main() -> None:
    chosen = json.loads((RESULTS / "chosen_threshold.json").read_text())
    ids, proxy, oracle, human = load_inputs("test")
    price = prices()
    r = evaluate(chosen["threshold"], ids, proxy, oracle, human, price["muse-1-2"], price["muse-1-1"])
    oracle_alone = sum(oracle_cost(proxy[t], price["muse-1-1"]) for t in ids) / len(ids) * 1000
    disagreements = round((1 - r["agreement_with_oracle"]) * len(ids))
    met = r["agreement_with_oracle"] >= chosen["agreement_requirement"] and r["missed_oracle_fails"] == 0
    check = {
        "split": "test",
        "conversations": len(ids),
        "oracle_fails": sum(oracle[t] == "fail" for t in ids),
        "human_fails": sum(human[t] == "fail" for t in ids),
        "threshold": chosen["threshold"],
        "agreement_with_oracle": r["agreement_with_oracle"],
        "disagreements_with_oracle": disagreements,
        "agreement_with_human_labels": r["agreement_with_human_labels"],
        "missed_oracle_fails": r["missed_oracle_fails"],
        "missed_human_fails": r["missed_human_fails"],
        "proxy_share": r["proxy_share"],
        "cost_per_1000_verdicts_usd": r["cost_per_1000_verdicts_usd"],
        "oracle_alone_cost_per_1000_verdicts_usd": round(oracle_alone, 4),
        "requirement": f"agreement_with_oracle >= {chosen['agreement_requirement']} and "
                       "no missed oracle failures",
        "requirement_met": met,
        "decision": "accept" if met else "reject",
    }
    (RESULTS / "check.json").write_text(json.dumps(check, indent=2) + "\n")
    print(json.dumps(check, indent=2))


if __name__ == "__main__":
    main()
