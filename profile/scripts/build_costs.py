"""Build the Homework 9 Part A cost tables from saved files.

    .venv/bin/python profile/scripts/build_costs.py

Writes two files:

  profile/results/costs.csv         one row per call site, as the handout asks
  profile/results/costs_by_run.csv  the same calls, one row per run

Reads local files only: no model call. Model names are the nicknames from
optimize/config.json; the real ids never appear.

Where the numbers come from (option (c), measured where possible):
  - HW8 conversation runs: tokens the API reported, saved by optimize.runner.
  - HW9 judge measurement: tokens the API reported (measure_judge_usage.py).
  - Everything else saved no token counts, so input is estimated as characters / 4.
    Agent input adds the tool definitions, which the spans don't record.
  - Estimated output uses a measured output-to-input ratio: the HW8 starting run for the
    agent (same prompt and model as HW3), the HW9 measurement for the judge. The visible
    text alone (replies, tool calls, critiques) is kept as a lower bound in
    costs_by_run.csv, because both models bill hidden reasoning tokens.
Every price is the $1-per-million placeholder in optimize/config.json, so cost_usd
measures tokens, not money.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from agent.agent import TOOLS_BY_ROLE  # noqa: E402
from analysis.tools.muse_classify import _trace_text  # noqa: E402

RESULTS = REPO / "profile" / "results"
JUDGES = REPO / "analysis" / "state" / "judges"
CHARS_PER_TOKEN = 4
AGENT_MODEL = "muse-1-3"
JUDGE_MODEL = "muse-1-1"


def tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def price(model: str) -> dict:
    config = json.loads((REPO / "optimize" / "config.json").read_text())
    return config["prices_per_million_tokens_usd"][model]


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = price(model)
    return round((input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000, 6)


def line_of(path: Path, needle: str) -> str:
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if needle in line:
            return f"{path.relative_to(REPO)}:{number}"
    raise SystemExit(f"call site not found: {needle!r} in {path}")


# --- measured ratios ----------------------------------------------------------------

def agent_ratio() -> float:
    """Output per input token, measured on the HW8 starting run (same prompt as HW3)."""
    d = json.loads(next((REPO / "optimize/results").glob("development-starting-*.json")).read_text())
    return d["output_tokens"] / d["input_tokens"]


def judge_measurement() -> dict:
    return json.loads((RESULTS / "judge_usage_sample.json").read_text())


# --- customer conversation runs -----------------------------------------------------

TOOL_DEF_CHARS = sum(
    len(json.dumps({"name": t.name, "description": t.description,
                    "parameters": t.params_json_schema}))
    for t in TOOLS_BY_ROLE["shopper"])


def part_chars(part: dict) -> int:
    return sum(len(str(part.get(k) or "")) for k in ("content", "arguments", "result", "response"))


def span_run(label: str, spans_file: str, results_file: str, ratio: float) -> dict:
    """One server run: count model calls on the spans and estimate their tokens."""
    calls, input_chars, tool_call_chars = 0, 0, 0
    last_input_by_trace: dict[str, tuple[int, list]] = {}
    for span in read_jsonl(REPO / spans_file):
        if span.get("name") != "openai.response":
            continue
        messages = json.loads(span["attributes"].get("gen_ai.input.messages") or "[]")
        calls += 1
        input_chars += sum(part_chars(p) for m in messages for p in m.get("parts", []))
        input_chars += TOOL_DEF_CHARS
        start = int(span.get("start_time") or 0)
        if start >= last_input_by_trace.get(span["trace_id"], (-1, []))[0]:
            last_input_by_trace[span["trace_id"]] = (start, messages)
    # Visible output: each turn's tool calls (after its last user message, in the turn's
    # final model input) plus the replies saved in the results file.
    for _start, messages in last_input_by_trace.values():
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
        tool_call_chars += sum(part_chars(p) for m in messages[last_user + 1:]
                               if m.get("role") == "assistant" for p in m.get("parts", []))
    reply_chars = sum(len(t.get("agent") or "") for r in read_jsonl(REPO / results_file)
                      for t in r.get("turns") or [])
    input_tokens = tokens(input_chars)
    return {
        "cost_category": "customer_conversation",
        "call_site": line_of(REPO / "server/app.py", "result = await Runner.run("),
        "run": label, "model": AGENT_MODEL, "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": round(input_tokens * ratio),
        "output_tokens_visible_floor": tokens(tool_call_chars + reply_chars),
        "method": f"estimated: input = characters/4 incl. tool definitions; "
                  f"output = input x {ratio:.3f} (HW8 measured agent ratio)",
    }


def hw8_runs() -> list[dict]:
    """HW8 runs through replay/rollout.py, with the token counts the API reported."""
    site = line_of(REPO / "replay/rollout.py", "result = await Runner.run(")
    groups = {
        "HW8 development runs (8 result files)": sorted(
            p for p in (REPO / "optimize/results").glob("development-*.json")),
        "HW8 test, configuration 1": [REPO / "optimize/results/test-configuration-1.json"],
        "HW8 test, configuration 2": [REPO / "optimize/results/test-configuration-2.json"],
        "HW8 test, configurations 3 and 4": [REPO / "optimize/results/test-configuration-3.json",
                                             REPO / "optimize/results/test-configuration-4.json"],
    }
    rows = []
    for label, files in groups.items():
        data = [json.loads(f.read_text()) for f in files]
        models = sorted({d["model"] for d in data})
        assert len(models) == 1, (label, models)
        rows.append({
            "cost_category": "customer_conversation", "call_site": site, "run": label,
            "model": models[0],
            "calls": sum(d["evaluated_case_runs"] for d in data),
            "input_tokens": sum(d["input_tokens"] for d in data),
            "output_tokens": sum(d["output_tokens"] for d in data),
            "output_tokens_visible_floor": "",
            "method": f"measured (API usage); calls = conversations, model requests not saved; "
                      f"{sum(d['cached_input_tokens'] for d in data)} input tokens were cached",
        })
    return rows


# --- judge runs ---------------------------------------------------------------------

def hw5_traces() -> dict[str, dict]:
    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(REPO / "analysis/state/hw5_trace_inputs.json")
    from analysis.helpers.scale import load_store_traces
    return {t["trace_id"]: t for t in load_store_traces()}


def judge_row(label: str, site: str, calls: list[tuple[str, str, str]], ratio: float) -> dict:
    """calls: (prompt_text, trace_text, critique) per saved verdict."""
    input_tokens = tokens(sum(len(p) + len(t) for p, t, _c in calls))
    return {
        "cost_category": "judge", "call_site": site, "run": label, "model": JUDGE_MODEL,
        "calls": len(calls), "input_tokens": input_tokens,
        "output_tokens": round(input_tokens * ratio),
        "output_tokens_visible_floor": tokens(sum(len(c) for _p, _t, c in calls)),
        "method": f"estimated: input = characters/4 of prompt + trace; output = input x "
                  f"{ratio:.3f} (HW9 measured judge ratio); retries not recorded",
    }


def hw5_judge(traces: dict, ratio: float) -> dict:
    calls = []
    for version in (4, 5, 6):
        judge = json.loads((JUDGES / f"irrelevant_policy_detail-v{version}.json").read_text())
        critiques = judge["critiques"][judge["prompt_hash"]]
        for trace_id in judge["predictions"][judge["prompt_hash"]]:
            calls.append((judge["prompt_text"], _trace_text(traces[trace_id]),
                          critiques.get(trace_id, "")))
    site = line_of(REPO / "analysis/tools/muse_classify.py", "urllib.request.urlopen(req")
    return judge_row("HW5 judge, irrelevant_policy_detail v4 to v6", site, calls, ratio)


def hw7_judge(ratio: float) -> dict:
    prompt = json.loads((JUDGES / "irrelevant_policy_detail-v6.json").read_text())["prompt_text"]
    calls = []
    for period in ("before", "after"):
        out = REPO / "monitoring/output" / period
        by_id = {c["trace_id"]: c for c in json.loads((out / "conversations.json").read_text())}
        for r in read_jsonl(out / "judge/results.jsonl"):
            calls.append((prompt, _trace_text(by_id[r["trace_id"]]), r.get("critique") or ""))
    site = line_of(REPO / "monitoring/background_judge.py", "client.responses.create(")
    return judge_row("HW7 monitor, before and after periods", site, calls, ratio)


def hw8_judge(hw5_row: dict, ratio: float) -> dict:
    """The saved HW8 results keep the judge's critique but not the trace it read, so the
    input per call is the HW5 judge's average estimate."""
    critiques = []
    files = sorted((REPO / "optimize/results").glob("development-*.json")) + sorted(
        (REPO / "optimize/results").glob("test-configuration-*.json"))
    for f in files:
        for case in json.loads(f.read_text())["cases"]:
            for run in case["run_details"]:
                critiques += list((run.get("judge_reasons") or {}).values())
    per_call = hw5_row["input_tokens"] / hw5_row["calls"]
    input_tokens = round(per_call * len(critiques))
    return {
        "cost_category": "judge",
        "call_site": line_of(REPO / "replay/rollout.py", "return client.responses.create("),
        "run": "HW8 judge on e-011 and e-012", "model": JUDGE_MODEL,
        "calls": len(critiques), "input_tokens": input_tokens,
        "output_tokens": round(input_tokens * ratio),
        "output_tokens_visible_floor": tokens(sum(len(c) for c in critiques)),
        "method": f"estimated: input = HW5 judge average ({per_call:.0f}/call), traces not "
                  f"saved; output = input x {ratio:.3f}; excludes 1 failed call and 8 setup checks",
    }


def hw9_measurement(m: dict) -> dict:
    visible = sum(a["visible_output_chars"] for c in m["calls"] for a in c["attempts"])
    return {
        "cost_category": "judge",
        "call_site": line_of(REPO / "profile/scripts/measure_judge_usage.py", "urllib.request.urlopen(req"),
        "run": "HW9 judge usage measurement", "model": m["model"],
        "calls": m["requests"], "input_tokens": m["input_tokens"],
        "output_tokens": m["output_tokens"],
        "output_tokens_visible_floor": tokens(visible),
        "method": "measured (API usage)",
    }


# --- assemble -----------------------------------------------------------------------

def main() -> None:
    a_ratio = agent_ratio()
    measurement = judge_measurement()
    j_ratio = measurement["output_to_input_ratio"]
    traces = hw5_traces()

    runs = [
        span_run("HW3 pilot, 30 scenarios", ".hw3-logs/pilot-spans.jsonl",
                 "scenarios/pilot-results.jsonl", a_ratio),
        span_run("HW3 final run, 250 scenarios", ".hw3-logs/final-spans.jsonl",
                 "scenarios/final-results.jsonl", a_ratio),
        span_run("HW7 after run, 50 scenarios", ".hw7-logs/after-spans.jsonl",
                 "scenarios/hw7-after-results.jsonl", a_ratio),
        *hw8_runs(),
    ]
    hw5 = hw5_judge(traces, j_ratio)
    runs += [hw5, hw7_judge(j_ratio), hw8_judge(hw5, j_ratio), hw9_measurement(measurement)]
    for r in runs:
        r["cost_usd"] = cost(r["model"], r["input_tokens"], r["output_tokens"])

    RESULTS.mkdir(parents=True, exist_ok=True)
    by_run_fields = ["cost_category", "call_site", "run", "model", "calls", "input_tokens",
                     "output_tokens", "output_tokens_visible_floor", "cost_usd", "method"]
    with (RESULTS / "costs_by_run.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=by_run_fields)
        writer.writeheader()
        writer.writerows(runs)

    sites: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in runs:
        sites[(r["cost_category"], r["call_site"])].append(r)
    rows = []
    for (category, site), group in sites.items():
        models = sorted({r["model"] for r in group})
        rows.append({
            "cost_category": category, "call_site": site,
            "purpose": "; ".join(f"{r['run']} [{r['method']}]" for r in group),
            "model": " / ".join(models),
            "calls": sum(r["calls"] for r in group),
            "input_tokens": sum(r["input_tokens"] for r in group),
            "output_tokens": sum(r["output_tokens"] for r in group),
            "cost_usd": round(sum(r["cost_usd"] for r in group), 6),
        })
    rows.sort(key=lambda r: (r["cost_category"], -r["cost_usd"]))
    with (RESULTS / "costs.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["cost_category", "call_site", "purpose", "model",
                                               "calls", "input_tokens", "output_tokens", "cost_usd"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"agent ratio {a_ratio:.3f} (HW8 starting run), judge ratio {j_ratio:.3f} (HW9 measurement)")
    print(f"tool definitions: {TOOL_DEF_CHARS} characters per agent call\n")
    for r in rows:
        print(f"{r['cost_category']:22} {r['call_site']:44} calls {r['calls']:>5}  "
              f"in {r['input_tokens']:>9,}  out {r['output_tokens']:>8,}  ${r['cost_usd']:.4f}")
    turns = sum(len(r.get("turns") or []) for r in read_jsonl(REPO / "scenarios/final-results.jsonl"))
    hw3 = next(r for r in runs if r["run"].startswith("HW3 final"))
    starting = json.loads(next((REPO / "optimize/results").glob("development-starting-*.json")).read_text())
    print(f"\nestimate check: HW3 final run {hw3['input_tokens'] / turns:,.0f} input tokens per turn "
          f"(estimated) vs HW8 starting run "
          f"{starting['input_tokens'] / starting['evaluated_case_runs']:,.0f} per conversation (measured)")
    print("\nNot covered (no saved calls or tokens): HW5 generation waves (results only, no "
          "spans), HW6 runs, the lost first HW8 tool-change run, HW1 command-line chats.")
    print(f"Saved {(RESULTS / 'costs.csv').relative_to(REPO)} and "
          f"{(RESULTS / 'costs_by_run.csv').relative_to(REPO)}")


if __name__ == "__main__":
    main()
