"""Measure the Homework 5 judge's real token usage on a small sample (Homework 9 Part A).

No judge run saved the token counts the API reported: `muse_classify` reads the response
and discards `usage`. The visible critiques undercount billed output, because the judge
is a reasoning model with a 16,000-token budget. This script sends the exact Homework 5
request for five traces and keeps `usage`, so the cost table can scale the other judge
runs by a measured output-to-input ratio instead of a guess.

The request is muse_classify's: the frozen judge's prompt and model, its trace
formatting, temperature 0, max_tokens 16,000 with one retry at 32,000 on empty content.
The sample is the first five trace ids, sorted, of the 118 the frozen judge graded.

    .venv/bin/python profile/scripts/measure_judge_usage.py

Writes profile/results/judge_usage_sample.json. The real model id is never written;
the judge's model is recorded by its nickname.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from analysis.helpers.scale import load_store_traces  # noqa: E402
from analysis.tools.muse_classify import (  # noqa: E402
    MAX_TOKENS,
    RETRY_TOKENS,
    TIMEOUT,
    _endpoint,
    _parse,
    _trace_text,
)
from observability.instrument import load_env  # noqa: E402

JUDGE = REPO / "analysis/state/judges/irrelevant_policy_detail-v6.json"
INPUTS = REPO / "analysis/state/hw5_trace_inputs.json"
OUTPUT = REPO / "profile/results/judge_usage_sample.json"
SAMPLE_SIZE = 5
MODEL_NICKNAME = "muse-1-1"


def ask(base: str, key: str, model: str, prompt_text: str, trace: dict) -> dict:
    """One judge request, as muse_classify sends it, keeping the reported usage."""
    attempts = []
    for budget in (MAX_TOKENS, RETRY_TOKENS):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content":
                    "Evaluate this trace and reply with the JSON object described above.\n\n"
                    "TRACE\n" + _trace_text(trace)},
            ],
            "temperature": 0,
            "max_tokens": budget,
        }
        req = urllib.request.Request(
            f"{base}/chat/completions", data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT).read())
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        attempts.append({
            "max_tokens": budget,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "visible_output_chars": len(content),
            "finish_reason": choice.get("finish_reason"),
        })
        if content.strip():
            label, _critique = _parse(content)
            return {"verdict": "pass" if label == 1 else "fail", "attempts": attempts}
    return {"verdict": None, "attempts": attempts}


def main() -> None:
    load_env()
    # As in analysis/run_judges.py: .env carries LANGFUSE_* keys from Homework 2, so
    # pin the judge to the saved Homework 5 inputs instead of a server that never ran.
    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(INPUTS)
    judge = json.loads(JUDGE.read_text())
    model = judge["model"].split("/", 1)[1] if judge["model"].startswith("openai/") else judge["model"]
    saved = judge["predictions"][judge["prompt_hash"]]
    sample = sorted(saved)[:SAMPLE_SIZE]
    store = {t["trace_id"]: t for t in load_store_traces()}
    missing = [t for t in sample if t not in store]
    if missing:
        raise SystemExit(f"sample traces not in the judge trace source: {missing}")

    base, key = _endpoint()
    calls = []
    for trace_id in sample:
        result = ask(base, key, model, judge["prompt_text"], store[trace_id])
        saved_verdict = "pass" if saved[trace_id] == 1 else "fail"
        calls.append({"trace_id": trace_id, "saved_verdict": saved_verdict,
                      "matches_saved": result["verdict"] == saved_verdict, **result})
        last = result["attempts"][-1]
        print(f"{trace_id[:12]}  in {last['input_tokens']}  out {last['output_tokens']}"
              f"  reasoning {last['reasoning_tokens']}  verdict {result['verdict']}"
              f" (HW5: {saved_verdict})")

    billed = [a for c in calls for a in c["attempts"]]
    total_in = sum(a["input_tokens"] or 0 for a in billed)
    total_out = sum(a["output_tokens"] or 0 for a in billed)
    summary = {
        "judge_id": judge["judge_id"],
        "model": MODEL_NICKNAME,
        "sample": f"first {SAMPLE_SIZE} sorted trace ids of the {len(saved)} the judge graded",
        "requests": len(billed),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "mean_input_per_request": round(total_in / len(billed), 1),
        "mean_output_per_request": round(total_out / len(billed), 1),
        "output_to_input_ratio": round(total_out / total_in, 4) if total_in else None,
        "verdicts_matching_hw5": sum(c["matches_saved"] for c in calls),
        "calls": calls,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "calls"}, indent=2))
    print(f"Saved {OUTPUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
