"""Run the cascade's proxy judge on the Homework 5 labels (Homework 9 Part D).

The proxy gets the frozen Homework 5 judge prompt (`irrelevant_policy_detail-v6`) word for
word, plus one instruction asking for its confidence, and runs on a model that thinks
less than the oracle. The request is muse_classify's: the same trace formatting,
temperature 0, max_tokens 16,000 with one retry at 32,000 on empty content. Every call
keeps the token counts the API reports, so the cascade's cost is measured, not guessed.

    .venv/bin/python cascade/run_proxy.py dev
    .venv/bin/python cascade/run_proxy.py test

Writes cascade/results/proxy_<split>.jsonl, one line per conversation. A rerun skips
conversations already done, so an interrupted run resumes. Never writes the real model id.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agent.agent import model_aliases  # noqa: E402
from analysis.helpers.scale import load_store_traces  # noqa: E402
from analysis.tools.muse_classify import (  # noqa: E402
    MAX_TOKENS,
    RETRY_TOKENS,
    TIMEOUT,
    WORKERS,
    _endpoint,
    _parse,
    _trace_text,
)
from observability.instrument import load_env  # noqa: E402

PROXY_MODEL = "muse-1-2"
JUDGE = REPO / "analysis/state/judges/irrelevant_policy_detail-v6.json"
SPLITS = REPO / "analysis/state/splits.json"
INPUTS = REPO / "analysis/state/hw5_trace_inputs.json"
RESULTS = REPO / "cascade/results"
CONFIDENCE_INSTRUCTION = (
    "\n\nAlso include a \"confidence\" field in the JSON: a number from 0 to 1 for how sure "
    "you are of your verdict."
)


def parse_confidence(content: str) -> float | None:
    import re
    match = re.search(r"\{.*\}", content, re.S)
    try:
        value = json.loads(match.group(0)).get("confidence") if match else None
        return None if value is None else max(0.0, min(1.0, float(value)))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def ask(base: str, key: str, model: str, prompt: str, trace: dict) -> dict:
    attempts = []
    for budget in (MAX_TOKENS, RETRY_TOKENS):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
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
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        attempts.append({
            "max_tokens": budget,
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        })
        if content.strip():
            label, critique = _parse(content)
            return {"verdict": "pass" if label == 1 else "fail",
                    "confidence": parse_confidence(content), "critique": critique,
                    "attempts": attempts}
    return {"verdict": None, "confidence": None, "critique": "", "attempts": attempts}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("dev", "test"):
        raise SystemExit("usage: run_proxy.py dev|test")
    split = sys.argv[1]
    load_env()
    # As in analysis/run_judges.py: pin the judge to the saved Homework 5 inputs, because
    # .env carries LANGFUSE_* keys from Homework 2 for a server that never ran.
    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(INPUTS)
    judge = json.loads(JUDGE.read_text())
    prompt = judge["prompt_text"] + CONFIDENCE_INSTRUCTION
    model = model_aliases()[PROXY_MODEL]
    # The aliases carry LiteLLM's "openai/" routing prefix; a direct request must drop it,
    # as muse_classify does for the judge's model.
    model = model.split("/", 1)[1] if model.startswith("openai/") else model
    ids = json.loads(SPLITS.read_text())[judge["mode"]][split]
    store = {t["trace_id"]: t for t in load_store_traces()}

    out = RESULTS / f"proxy_{split}.jsonl"
    RESULTS.mkdir(parents=True, exist_ok=True)
    done = {json.loads(l)["trace_id"] for l in out.read_text().splitlines() if l.strip()} \
        if out.exists() else set()
    todo = [t for t in ids if t not in done]
    print(f"{split}: {len(ids)} conversations, {len(done)} already done, {len(todo)} to run")
    base, key = _endpoint()

    def one(trace_id: str) -> dict:
        return {"trace_id": trace_id, "model": PROXY_MODEL,
                **ask(base, key, model, prompt, store[trace_id])}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool, out.open("a") as handle:
        for row in pool.map(one, todo):
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            last = row["attempts"][-1]
            print(f"  {row['trace_id'][:12]}  {row['verdict']}  confidence {row['confidence']}"
                  f"  in {last['input_tokens']}  out {last['output_tokens']}", flush=True)
    print(f"Saved {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
