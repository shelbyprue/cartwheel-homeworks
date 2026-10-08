"""Monitor one failure mode: sample conversations, judge them, correct the rate.

    python -m monitoring.run --period before
    python -m monitoring.run --period after
    python -m monitoring.run --last-hours 24

Two substitutions, both because Langfuse could not run here (Docker is unavailable):

  * **Trace store.** Traces are read from the OpenTelemetry span files listed under
    ``trace_store`` in ``monitoring/config.json`` and filtered by time, the way a
    Langfuse trace query filters by timestamp. The spans are the same ones the server
    would have sent to Langfuse.
  * **Score store.** Score records are upserted by ``score_id`` into
    ``monitoring/output/scores.jsonl``, which is the Scores API's update-on-repeat
    contract. With ``"score_sink": "langfuse"`` they go to Langfuse through
    ``write_scores.post_scores`` instead.

The judge runs through ``monitoring/background_judge.py``, not
``run_judges.judge_sample``. Homework 5 validated this judge through
``analysis/tools/muse_classify``; DocETL wraps the prompt and parses the verdict
differently, which would make it a different judge. Background mode sends the
muse_classify request unchanged and only avoids the proxy's 60-second cutoff.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis.tools.muse_classify import _trace_text
from monitoring.background_judge import classify_background
from monitoring.chart import prevalence_chart
from monitoring.correct import corrected_mode_prevalence
from monitoring.dashboard import write_dashboard
from monitoring.run_judges import judge_test_data, load_monitoring_judge
from monitoring.sample import DEFAULT_RISK_GROUPS, select_traces
from monitoring.write_scores import build_score_records, post_scores

REPO = Path(__file__).resolve().parents[1]
MONITORING = REPO / "monitoring"
CONFIG = MONITORING / "config.json"
OUTPUT = MONITORING / "output"
HISTORY = MONITORING / "history.jsonl"
SCHEDULED_HISTORY = OUTPUT / "scheduled-history.jsonl"
CHART = MONITORING / "prevalence.svg"
# Committed files carry this in place of the internal model name.
MODEL_PLACEHOLDER = "<internal-model>"


# --------------------------------------------------------------------------- config


def load_config() -> dict[str, Any]:
    config = json.loads(CONFIG.read_text())
    config["model_name"] = config["model"]
    if config["model"] == MODEL_PLACEHOLDER:
        config["model_name"] = os.environ.get("CARTWHEEL_MODEL", "")
        if not config["model_name"]:
            raise SystemExit(f"config model is {MODEL_PLACEHOLDER}; set CARTWHEEL_MODEL in .env")
    unknown = set(config["risk_groups"]) - set(DEFAULT_RISK_GROUPS)
    if unknown:
        raise SystemExit(f"unknown risk groups {sorted(unknown)}; choose from {sorted(DEFAULT_RISK_GROUPS)}")
    return config


def parse_time(value: str) -> int:
    """ISO 8601 UTC -> nanoseconds, the unit the spans use."""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(moment.timestamp() * 1_000_000) * 1000


def iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- traces


def _messages_text(raw: str | None, role: str) -> str:
    """The text parts of the messages with ``role`` in a gen_ai.*.messages attribute."""
    if not raw:
        return ""
    try:
        messages = json.loads(raw)
    except json.JSONDecodeError:
        return str(raw)
    return "\n".join(
        part.get("content", "")
        for message in messages if message.get("role") == role
        for part in message.get("parts", []) if part.get("type", "text") == "text"
    )


def load_traces(store: list[str]) -> list[dict[str, Any]]:
    """Read the span files and assemble one summary per trace.

    The root span of each trace is one user message and the reply to it. Tool spans
    carry the arguments and results the judge needs as evidence.
    """
    # Where the trace_store span files live. A checkout on a runner has no span files
    # (they are gitignored), so the scheduled job points this at the working copy that
    # runs Cartwheel. Unset, it is this repository. Read here, not at import, so a
    # value in .env (loaded by main) also counts.
    data_dir = Path(os.environ.get("MONITOR_DATA_DIR") or REPO)
    spans: dict[str, dict] = {}
    for name in store:
        path = data_dir / name
        if not path.exists():
            print(f"trace store: {name} not found, skipped")
            continue
        for line in path.read_text().splitlines():
            if line.strip():
                span = json.loads(line)
                spans[span["span_id"]] = span
    # A quiet window is a zero count; a trace store that cannot be seen at all is not,
    # and must not pass as one.
    if not any((data_dir / name).exists() for name in store):
        raise SystemExit(f"trace store: none of {store} found under {data_dir}")

    by_trace: dict[str, list[dict]] = defaultdict(list)
    for span in spans.values():
        by_trace[span["trace_id"]].append(span)

    traces = []
    for trace_id, group in by_trace.items():
        group.sort(key=lambda s: s["start_time"])
        root = next((s for s in group if s["parent_span_id"] is None), None)
        if root is None:
            continue
        attributes = root["attributes"]
        traces.append({
            "trace_id": trace_id,
            "start": root["start_time"],
            "end": max(s["end_time"] for s in group),
            "scenario_id": attributes.get("cartwheel.scenario_id"),
            "session_id": attributes.get("cartwheel.session_id"),
            "models": sorted({s["attributes"]["gen_ai.request.model"] for s in group
                              if s["attributes"].get("gen_ai.request.model")}),
            "user": _messages_text(attributes.get("gen_ai.input.messages"), "user"),
            "reply": _messages_text(attributes.get("gen_ai.output.messages"), "assistant"),
            "tools": [
                {"name": s["attributes"]["gen_ai.tool.name"],
                 "arguments": s["attributes"].get("gen_ai.tool.call.arguments"),
                 "result": s["attributes"].get("gen_ai.tool.call.result")}
                for s in group if s["attributes"].get("gen_ai.tool.name")
            ],
        })
    return sorted(traces, key=lambda t: t["start"])


def build_conversation(key: str, traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine one conversation's ordered traces into one judge record.

    The message layout is Homework 5's (`analysis/run_judges.prepare_inputs`): every
    turn in order, then every tool call and result. Only trace evidence goes in: no
    labels, annotations or scenario metadata. The final trace id is the record id so
    the score lands on a trace.
    """
    messages: list[dict[str, Any]] = []
    for trace in traces:
        if trace["user"]:
            messages.append({"role": "user", "text": trace["user"]})
        if trace["reply"]:
            messages.append({"role": "assistant", "text": trace["reply"]})
    for trace in traces:
        for tool in trace["tools"]:
            messages.append({"role": "tool_call", "name": tool["name"],
                             "arguments": tool["arguments"]})
            messages.append({"role": "tool_result", "name": tool["name"],
                             "text": tool["result"]})
    record = {
        "id": traces[-1]["trace_id"],
        "trace_id": traces[-1]["trace_id"],
        "key": key,
        "trace_ids": [t["trace_id"] for t in traces],
        "tools": sorted({tool["name"] for t in traces for tool in t["tools"]}),
        "turn_count": len(traces),
        "has_reply": any(t["reply"] for t in traces),
        "trace": messages,
    }
    record["text"] = _trace_text(record)
    return record


def check_models(traces: list[dict[str, Any]], model: str) -> None:
    wrong = {t["trace_id"]: t["models"] for t in traces if set(t["models"]) - {model}}
    if wrong:
        raise SystemExit(f"rejected: {len(wrong)} eligible traces used another model, "
                         f"e.g. {next(iter(wrong.items()))}")


# --------------------------------------------------------------------------- output


def upsert_jsonl(path: Path, rows: list[dict[str, Any]], key: str) -> tuple[int, int]:
    """Insert or replace rows by ``key``; return (created, updated)."""
    existing = {}
    if path.exists():
        existing = {r[key]: r for r in map(json.loads, path.read_text().splitlines()) if r}
    created = sum(1 for row in rows if row[key] not in existing)
    for row in rows:
        existing[row[key]] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in existing.values()))
    return created, len(rows) - created


def write_scores(config: dict[str, Any], records: list[dict[str, Any]]) -> str:
    if config.get("score_sink") == "langfuse":
        return f"{post_scores(records)} scores sent to Langfuse"
    created, updated = upsert_jsonl(OUTPUT / "scores.jsonl", records, "score_id")
    return f"scores: {created} created, {updated} updated in {OUTPUT / 'scores.jsonl'}"


def redraw_chart(config: dict[str, Any]) -> None:
    rows = {r["label"]: r for r in map(json.loads, HISTORY.read_text().splitlines()) if r}
    points = [{"label": p["label"], **{k: rows[p["label"]][k]
               for k in ("corrected", "ci_low", "ci_high")}}
              for p in config["periods"] if p["label"] in rows]
    CHART.write_text(prevalence_chart(points, config["threshold"], config["judge_mode"]))
    print(f"chart: {len(points)} periods -> {CHART}")


# --------------------------------------------------------------------------- run


def judge_union(judge: dict[str, Any], records: list[dict[str, Any]], work: Path) -> dict[str, int]:
    """Judge every selected record once. Returns id -> 1 when the failure is present."""
    verdicts = classify_background(judge["model"], judge["prompt_text"], records, work)
    (work / "critiques.json").write_text(json.dumps(
        {tid: critique for tid, (_, critique) in verdicts.items()}, indent=1))
    return {tid: 1 - label for tid, (label, _) in verdicts.items()}


def run(config: dict[str, Any], label: str, start: int, end: int, scheduled: bool,
        assume_yes: bool, dry_run: bool) -> None:
    traces = [t for t in load_traces(config["trace_store"]) if start <= t["start"] <= end]
    print(f"{label}: {iso(start)} to {iso(end)}, {len(traces)} traces in the window")

    if scheduled:
        eligible = [t for t in traces if t["session_id"]]
        group_key = "session_id"
    else:
        scenario_ids = [json.loads(line)["id"] for line in
                        (REPO / config["scenarios"]).read_text().splitlines() if line.strip()]
        eligible = [t for t in traces if t["scenario_id"] in set(scenario_ids)]
        group_key = "scenario_id"
        missing = sorted(set(scenario_ids) - {t["scenario_id"] for t in eligible})
        if missing:
            raise SystemExit(f"rejected: {len(missing)} scenario ids have no trace in the "
                             f"window, e.g. {missing[:5]}")
    check_models(eligible, config["model_name"])

    grouped: dict[str, list[dict]] = defaultdict(list)
    for trace in eligible:
        grouped[trace[group_key]].append(trace)
    conversations = [build_conversation(key, group) for key, group in grouped.items()]
    if not scheduled and len(conversations) != len(scenario_ids):
        raise SystemExit(f"rejected: expected {len(scenario_ids)} conversations, "
                         f"built {len(conversations)}")

    entry: dict[str, Any] = {
        "label": label, "judge_id": config["judge_id"], "judge_mode": config["judge_mode"],
        "model": config["model"], "from": iso(start), "to": iso(end),
        "trace_count": len(eligible), "conversation_count": len(conversations),
    }
    history = SCHEDULED_HISTORY if scheduled else HISTORY
    if not conversations:
        entry.update(random_count=0, risk_count=0, judge_calls=0,
                     run_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        upsert_jsonl(history, [entry], "label")
        print("no eligible conversations; recorded a zero count, judge not called")
        print(f"dashboard -> {write_dashboard()}")
        return

    groups = {name: DEFAULT_RISK_GROUPS[name] for name in config["risk_groups"]}
    plan = select_traces(conversations, config["random_rate"], groups, seed=config.get("seed", 7))
    risk_ids = list(dict.fromkeys(r["id"] for g in plan["risk_groups"].values() for r in g))
    no_reply = [c["key"] for c in conversations if not c["has_reply"]]
    print(f"conversations: {len(conversations)}"
          + (f" ({len(no_reply)} without a reply: {no_reply})" if no_reply else ""))
    print(f"random sample: {len(plan['random'])}  risk groups: "
          + ", ".join(f"{n} {len(g)}" for n, g in plan["risk_groups"].items()))
    print(f"judge calls: {len(plan['to_judge'])} ({config['judge_id']})")

    work = OUTPUT / label
    work.mkdir(parents=True, exist_ok=True)
    (work / "plan.json").write_text(json.dumps({
        "random": [r["key"] for r in plan["random"]],
        "risk_groups": {n: [r["key"] for r in g] for n, g in plan["risk_groups"].items()},
        "to_judge": [r["key"] for r in plan["to_judge"]],
    }, indent=1))
    (work / "conversations.json").write_text(json.dumps(conversations, indent=1))
    if dry_run:
        print(f"dry run: plan saved to {work}, judge not called")
        return
    if not assume_yes and input("run the judge? [y/N] ").strip().lower() != "y":
        raise SystemExit("stopped before the judge")

    judge = load_monitoring_judge(config["judge_id"])
    verdicts = judge_union(judge, plan["to_judge"], work / "judge")
    random_verdicts = {r["id"]: verdicts[r["id"]] for r in plan["random"]}
    risk_verdicts = {tid: verdicts[tid] for tid in risk_ids}
    (work / "verdicts.json").write_text(json.dumps(
        {"random": random_verdicts, "risk": risk_verdicts}, indent=1))

    test_labels, test_preds = judge_test_data(config["judge_id"])
    estimate = corrected_mode_prevalence(list(random_verdicts.values()), test_labels, test_preds)
    print(f"raw {estimate['raw']}  corrected {estimate['corrected']}  "
          f"{estimate['confidence']:.0%} CI [{estimate['ci_low']}, {estimate['ci_high']}]")
    print(write_scores(config, build_score_records(
        config["judge_mode"], random_verdicts, risk_verdicts, estimate, label)))

    entry.update(
        random_count=len(random_verdicts), risk_count=len(risk_verdicts),
        risk_group_counts={n: len(g) for n, g in plan["risk_groups"].items()},
        judge_calls=len(plan["to_judge"]),
        random_flagged=sum(random_verdicts.values()), risk_flagged=sum(risk_verdicts.values()),
        **{k: estimate[k] for k in ("raw", "corrected", "ci_low", "ci_high", "confidence",
                                     "failure_sensitivity", "pass_specificity")},
        threshold=config["threshold"],
        run_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    upsert_jsonl(history, [entry], "label")
    print(f"history: {label} -> {history}")
    if not scheduled:
        redraw_chart(config)
    print(f"dashboard -> {write_dashboard()}")


def main() -> None:
    from observability.instrument import load_env

    load_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--period", help="a period label from monitoring/config.json")
    which.add_argument("--last-hours", type=float, help="monitor the trailing window")
    parser.add_argument("--yes", action="store_true", help="skip the judge confirmation")
    parser.add_argument("--dry-run", action="store_true", help="stop before the judge")
    args = parser.parse_args()

    config = load_config()
    if args.period:
        period = next((p for p in config["periods"] if p["label"] == args.period), None)
        if period is None:
            raise SystemExit(f"no period {args.period!r} in {CONFIG}")
        run(config, period["label"], parse_time(period["from"]), parse_time(period["to"]),
            scheduled=False, assume_yes=args.yes, dry_run=args.dry_run)
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=args.last_hours)
        run(config, f"daily-{end:%Y-%m-%d}", parse_time(start.isoformat()),
            parse_time(end.isoformat()), scheduled=True, assume_yes=args.yes,
            dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
