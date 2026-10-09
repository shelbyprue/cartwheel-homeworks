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
