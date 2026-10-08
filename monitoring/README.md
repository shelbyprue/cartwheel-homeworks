# Homework 7 — monitoring `irrelevant_policy_detail`

The monitor samples conversations from a time window, runs the frozen Homework 5 judge
`irrelevant_policy_detail-v6` on them, and corrects the judge's raw flag rate for its
known mistakes. Cartwheel and the judge both run on internal models, written here as
`<internal-model>`.

## Results

| | before | after |
|---|---|---|
| window (UTC) | 2026-09-17 19:58:18 to 2026-09-18 00:43:46 | 2026-10-06 18:36:16 to 19:15:26 |
| traces / conversations | 54 / 50 | 55 / 50 |
| random sample flagged | 1 of 10 | 1 of 10 |
| raw rate | 0.10 | 0.10 |
| **corrected rate** | **0.049** | **0.049** |
| 95% interval | [0.00, 0.69] | [0.00, 0.65] |
| risk groups flagged | 3 of 15 (policy_lookup 14, write_action 2) | 3 of 12 (policy_lookup 11, write_action 1) |
| flagged conversations | support-0036, 0050, 0206 | support-0037, 0093, 0206 |

Threshold: corrected rate 0.15, chosen on 2026-10-02 before either period was judged.
Neither period crosses it. History: `history.jsonl`. Chart: `prevalence.svg`.

## Interpretation (Part E)

1. **Did the corrected failure estimate move between the two periods?**
   No. This was the same in both periods because the inputs were identical: 1 flagged out
   of 10 random conversations both times, through the same correction.
2. **Do the intervals support a conclusion, or is the result uncertain?**
   No, the result is uncertain. Both ranges run from 0% to about 65–69%, sit on both sides
   of the 15% threshold, and overlap completely. This is likely due to the small sample of
   ten random conversations per period and an unreliable judge that only caught 3 of 5
   failures. A larger random sample and more human-labelled failures to measure the judge
   more precisely would help.
3. **What did the risk groups reveal that the random estimate did not?**
   The risk groups revealed a higher share flagged (3 of 15, or 20%, before; 3 of 12, or
   25%, after) against 10% in the random sample. All six flags came from policy_lookup and
   none came from write_action, which fits the HW5 finding that every confirmed failure
   involved a policy lookup. The 20–25% isn't a failure rate, as the conversations were
   picked because they're risky and tell us where to look. Support-0206 was flagged in both
   periods, but a human labelled it a pass in HW5, and something about it seems to
   consistently fool the judge, which is a lead towards improving the judge.
4. **What action should happen if the estimate crosses the threshold?**
   1. A human should review each flagged trace and confirm or reject the flag.
   2. Group confirmed failures by cause.
   3. Add each confirmed failure as an eval case, so CI can catch it in the future.
   4. Fix the agent, then rerun CI and the monitor.

   Human review comes first because this judge would cross the threshold on false alarms
   alone about 17% of the time, even if the agent never failed.

## How the two periods were made

**Same scenarios, same model.** Both periods are the 50 scenarios in
`scenarios/monitoring_scenarios.jsonl` (55 user turns) on the same Cartwheel model. The
monitor rejects a window if any scenario ID is missing or an eligible trace used another
model.

**Before** is the whole Homework 3 first pass, from `.hw3-logs/final-spans.jsonl`. Two
scenarios, support-0191 and support-0196, ended in a connection error in that pass and
completed only in a later resume run. The resume run is outside the window, because
retries may not be combined, so these two count as their errored attempts. Neither was
selected by the random sample or a risk group.

**After** is one new run of the current agent on 2026-10-06. The database was seeded once,
then the 50 scenarios ran in five batches of ten, each scenario exactly once, with no
retries and no other traffic on the server. All 50 completed, including support-0191 and
support-0196, with no server errors. Batching only added pause points: an error in any
batch would have voided the whole period. Results: `scenarios/hw7-after-results.jsonl`.

**Update, not duplicate.** Running the after period a second time created 0 scores and
updated 23, with identical values (49 score records in total).

## What was substituted, and why

This follows the approach of `analysis/report/hw6-ci-substitution.md`: Docker cannot be
installed on this machine, and the model credential is internal and may not go to a
third-party service.

| handout | used instead | why |
|---|---|---|
| Langfuse traces | OpenTelemetry span files, filtered by time (`trace_store` in `config.json`) | Langfuse needs Docker; Langfuse Cloud would receive internal model data |
| Langfuse scores | `monitoring/output/scores.jsonl`, upserted by the same stable `score_id` | same update-on-repeat contract; `"score_sink": "langfuse"` posts through `write_scores.post_scores` instead |
| Langfuse dashboard | `monitoring/dashboard.html`, built by `monitoring/dashboard.py` after every monitor run | reads the score store and shows the random verdict score over time (with the corrected rate, interval and threshold), the risk verdict score over time, and the traces flagged by either score with the judge's critique |
| DocETL `judge_sample` | `analysis/tools/muse_classify`'s request, sent through Responses API background mode (`background_judge.py`) | Homework 5 validated the judge through `muse_classify`. DocETL wraps the prompt and parses the verdict differently, which would make it a different judge. Background mode avoids the corporate proxy's 60-second cutoff. Four Homework 5 test traces gave the same verdicts both ways. |
| GitHub Actions schedule | `.github/workflows/monitor.yml`, complete and pushed, but disabled on GitHub (Actions -> monitor -> Disable workflow) | the judge's key may not go to GitHub, and a self-hosted runner on this laptop would let GitHub run jobs with it |

**The daily schedule does not run on its own.** The plan was to run the workflow's
command, `scripts/run_monitor_local.sh` (`--last-hours 24`), every day through launchd,
macOS's scheduler. launchd started it twice and both times it failed with
`Operation not permitted`: macOS privacy controls stop background jobs from reading the
Desktop folder, where this repository lives. Fixing that means giving the shell Full Disk
Access or moving the repository, and neither was worth it for this homework. The same
script is run by hand instead. An empty window records a zero count and exits without
calling the judge, as on 2026-10-02 and 2026-10-08. Evidence: `monitoring/output/scheduled-history.jsonl`
and `.hw7-logs/scheduled-runs/`.

## Caveats

- **Homework 5 said not to use this judge for prevalence.** Its test failure sensitivity
  is 0.60 [0.23, 0.88], with only 5 failures in the test set. The corrected intervals
  above inherit that width.
- **One held-out verdict behind the correction was judged on mismatched input.** The
  Homework 5 resume run overwrote `final-results.jsonl`, so for 3 traces the tool evidence
  came from one attempt and the reply from another. One of them is in test (support-0196,
  trace d90ff269). The saved test results are used as they are.
- **The judge endpoint was overloaded on 2026-10-06.** The first after-period judge run
  failed on all 19 calls (`503 service_overloaded`) and wrote nothing. A probe retried
  every 10 minutes and the run went through at 21:05 UTC, unchanged.
