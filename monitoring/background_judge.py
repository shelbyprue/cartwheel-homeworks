"""Run the frozen Homework 5 judge through Responses API background mode.

The corporate proxy drops any connection that is silent for about 60 seconds, and on
slow days a judge answer takes longer than that to produce. Background mode submits
the request, returns a job id at once, and polls until the answer is ready, so every
HTTP call stays short.

Only the transport changes. The request is the one `analysis/tools/muse_classify`
sends, field for field:

    Chat Completions                Responses API (background)
    messages=[system, user]         input=[system, user]   (same text, built by the same code)
    temperature=0                   temperature=0
    max_tokens=16000, retry 32000   max_output_tokens=16000, retry 32000
    (no response_format)            (no text format)
    _parse(content)                 _parse(output_text)

The labels follow muse_classify: 1 = Pass, 0 = Fail. Callers convert.

Job ids and results go to disk the moment they exist, so a crash resumes polling
instead of paying for the call again.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from analysis.tools.muse_classify import MAX_TOKENS, RETRY_TOKENS, _parse, _trace_text

POLL_SECONDS = 15
# The note this follows saw jobs stuck in_progress for 29 minutes when a full answer
# takes under 3. Past this, cancel and resubmit once, then report.
MAX_WAIT_SECONDS = 15 * 60
PENDING = ("queued", "in_progress")


def build_request(model: str, prompt_text: str, trace: dict, budget: int) -> dict[str, Any]:
    """The muse_classify request, in Responses API form."""
    model = model.split("/", 1)[1] if model.startswith("openai/") else model
    return {
        "model": model,
        "input": [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content":
                "Evaluate this trace and reply with the JSON object described above.\n\n"
                "TRACE\n" + _trace_text(trace)},
        ],
        "temperature": 0,
        "max_output_tokens": budget,
        "background": True,
    }


def _read_jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {row["trace_id"]: row for row in rows}


def _write_jsonl(path: Path, rows: dict[str, dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows.values()))


def classify_background(
    model: str,
    prompt_text: str,
    traces: list[dict],
    work_dir: Path,
    client: Any = None,
) -> dict[str, tuple[int, str]]:
    """Judge every trace; return trace_id -> (label, critique), 1 = Pass.

    ``work_dir`` holds ``jobs.jsonl`` (pending job ids) and ``results.jsonl``
    (finished verdicts). Rerunning with the same directory skips finished traces and
    resumes polling pending ones.
    """
    if client is None:
        from openai import OpenAI

        client = OpenAI(timeout=45, max_retries=5)
    work_dir.mkdir(parents=True, exist_ok=True)
    jobs_path, results_path = work_dir / "jobs.jsonl", work_dir / "results.jsonl"
    by_id = {t["trace_id"]: t for t in traces}
    results = _read_jsonl(results_path)
    jobs = {tid: job for tid, job in _read_jsonl(jobs_path).items() if tid not in results}

    def submit(tid: str, budget: int, attempt: int) -> None:
        job = client.responses.create(**build_request(model, prompt_text, by_id[tid], budget))
        jobs[tid] = {"trace_id": tid, "job_id": job.id, "budget": budget,
                     "attempt": attempt, "submitted": time.time()}
        _write_jsonl(jobs_path, jobs)

    for tid in by_id:
        if tid not in results and tid not in jobs:
            submit(tid, MAX_TOKENS, 1)
    print(f"{len(results)} already judged, {len(jobs)} pending")

    failures: dict[str, str] = {}
    while jobs:
        time.sleep(POLL_SECONDS)
        for tid, info in list(jobs.items()):
            job = client.responses.retrieve(info["job_id"])
            if job.status in PENDING:
                if time.time() - info["submitted"] > MAX_WAIT_SECONDS:
                    client.responses.cancel(info["job_id"])
                    if info["attempt"] >= 2:
                        failures[tid] = f"stuck {job.status} twice"
                        del jobs[tid]
                    else:
                        submit(tid, info["budget"], info["attempt"] + 1)
                continue
            content = (job.output_text or "") if job.status == "completed" else ""
            if job.status == "completed" and content.strip():
                label, critique = _parse(content)
                results[tid] = {"trace_id": tid, "label": label, "critique": critique,
                                "job_id": info["job_id"], "budget": info["budget"]}
                _write_jsonl(results_path, results)
                del jobs[tid]
            elif info["budget"] == MAX_TOKENS:
                # Same rule as muse_classify: empty content means the reasoning used the
                # whole budget, so try once more with more headroom.
                submit(tid, RETRY_TOKENS, info["attempt"])
            else:
                failures[tid] = f"{job.status}: {job.error or job.incomplete_details}"
                del jobs[tid]
            _write_jsonl(jobs_path, jobs)
        print(f"  {len(results)} judged, {len(jobs)} pending, {len(failures)} failed")

    if failures:
        raise RuntimeError(f"judge jobs failed: {failures}")
    return {tid: (results[tid]["label"], results[tid]["critique"]) for tid in by_id}
