# Homework 8 — scoring cases with the judge Homework 5 validated

Homework 8 scores every evaluation case through `replay.__main__.make_runner`. For the
cases that name a judge (e-011 and e-012, both `irrelevant_policy_detail`), the course
code calls `replay.rollout.judge_reply_with_text`. In this fork that call would not be the
judge Homework 5 validated, so it was replaced before the case split and the starting
version were recorded. Every configuration in the final comparison is scored the same way.
Model names here are the nicknames from `optimize/config.json`; the judge's pinned model is
muse-1-1.

## Why the course judge call could not be used

`judge_reply_with_text` differs from the validated judge in four ways, and each one
changes verdicts:

| | course call | Homework 5 validation (`analysis/tools/muse_classify`) |
|---|---|---|
| judge input | the final reply plus the retrieved policy text | the whole trace: user message, reply, every tool call and result |
| token budget | none | 16,000, retried at 32,000 when the answer comes back empty |
| verdict parser | looks for `"answer": "pass"` | reads `"result": "Pass" \| "Fail"` |
| fallback | any "fail" anywhere in the text means fail, otherwise pass | the verdict word, else the run errors |

This endpoint returns *empty content* when the budget is too small. The course call would
then read the empty text as **pass**. When the judge does answer, its critique often
contains the word "fail" ("this does not fail because..."), and the fallback would read
that as **fail**. Homework 6 met the same problem and worked around it in its own driver
(`analysis/report/hw6-ci-substitution.md`).

## What replaced it

`replay.rollout.judge_transcript` sends the request Homework 5 validated, through Responses
API background mode, which Homework 7 also used (the corporate proxy drops connections that
stay silent for about 60 seconds). `replay/__main__.py` calls it instead of
`judge_reply_with_text`. The same prompt, model, temperature 0, budget and parser are used.

A driver outside the course code, as in Homework 6, would not work here.
`optimize.frontier run` checks out each configuration's recorded commit in a fresh
worktree and imports that commit's `replay/` code. So the change had to be committed, and
it was committed before `optimize.prepare`, so the starting and final configurations both
contain it. `optimize.workflow.grader_hash` covers the cases, `tests/eval` and the saved
judges. It does not cover `replay/`, and none of those files changed.

## The judge's input, matched to Homework 5

`replay.rollout.hw5_trace_text` builds the trace in the layout of the exported Homework 5
traces: per turn, the user message, then the reply, then each tool call with the model's
compact JSON arguments and the tool's JSON result. Rebuilding all **127** single-turn
Homework 5 traces this way reproduces the text they were judged on, **byte for byte**.

A first version copied the Homework 6 local-CI layout, which lists the tool calls before the
reply and spaces the arguments. Rerunning four held-out Homework 5 test traces gave
**3 of 4** saved verdicts: the missed failure `38ad3f495711` came back fail. With the
Homework 5 layout, the same four traces gave **4 of 4** saved verdicts, the missed failure included. Script and logs:
`.hw8-logs/judge_check.py`, `judge_check.log` (first layout) and `judge_check-2.log`.

So e-011 and e-012 are not scored with exactly the trace text Homework 6 used. Homework 6's
layout was never validated against the Homework 5 verdicts; this one reproduces them.

## Model nicknames

The repository is public. `optimize/config.json` and every Homework 8 result name
muse-1-1, muse-1-2 and muse-1-3. `agent.agent.model_aliases` maps those nicknames to the
real model ids from `CARTWHEEL_MODEL_ALIASES` in `.env`, which is not committed. The agent
resolves a nickname to the same model and settings as the full id.

## Prices

The internal models have no public price list. All three are given the same placeholder,
$1.00 per million input and output tokens, dated 2026-10-09. The cost columns therefore
measure token usage, not money: a configuration that costs twice as much used about twice
the tokens.

## Other departures from the handout

**Models.** The handout's defaults (GLM 5.2, `gpt-5.5`, `claude-opus-4-6`) are not
reachable from this machine. The internal gateway in `.env` lists exactly three models, the
three Muse versions, so they fill all three comparison slots, with muse-1-3 as the
development and search model (the Cartwheel model used in Homeworks 3 and 7). The judge
stays on muse-1-1, a different model from the agent. `gepa_reflection` keeps a placeholder
because GEPA was not used.

**Commands.** `uv` is not installed here: its installer is a piped shell script, which was
declined on security grounds. Every `uv run python ...` in the handout ran as
`.venv/bin/python ...`.

**Part C order.** Each layer was tested from the starting version and reverted afterwards,
so that each change had to fix e-008 and e-009 on its own: prompt 2 of 2, tool 1 of 2,
harness 2 of 2. The harness change was the last layer, so it was kept. The prompt change
was then reapplied on top of it (784d389) as the starting point for Part D.

**Part D.** The improve loop, not GEPA (GEPA would have needed extra wiring for the model
nicknames). The loop's baseline run was charged to the search budget along with each
candidate. Both candidates improved the development score, so the two-misses stopping rule
never fired. The loop stopped at a time cap set by the student, with 48 of 150 evaluated
case runs used. `improve-loop.jsonl` records the stop and the remaining failure: e-002
asks before refunding in most runs, and its customer asked to return the item but never
said "refund".

**Final version commit.** `final_version.json` records 32bb610, the commit that added the
Part C and D records. Its agent code is identical to a862b65, the last improve loop
candidate, because `save_version` requires the development result to come from the
current commit.

**Interruptions.**
- The first tool-change run lost its result when the judge returned a server error on the
  last case. The runner saves only at the end, and the run did not use `--search`, so
  nothing was charged. It was rerun unchanged. `judge_transcript` retries stuck and empty
  judge jobs but not failed ones. Adding a retry would have changed `replay/` partway
  through the experiment, so it was not added.
- During the test batch the laptop slept for about three minutes (lid closed) while
  configuration 1 was running. The batch resumed, and no run recorded an error.

**Optional Harbor exercise.** Not attempted: Harbor needs Docker, which cannot be installed
on this machine (see `hw6-ci-substitution.md`).
