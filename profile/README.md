# Homework 9 notes: reducing cost

Model names are the nicknames from `optimize/config.json` (muse-1-1, muse-1-2, muse-1-3).
Prices are placeholders: the internal models have no public price list, so every model is
billed at $1.00 per million input and output tokens. Cost columns measure token usage,
not money. The token counts themselves are what the model service reported.

## Part A, where cost comes from

`scripts/build_costs.py` writes `results/costs.csv` (one row per call site, as the handout
asks) and `results/costs_by_run.csv` (the same calls, one row per run).

- Only the Homework 8 runs and the Homework 9 judge measurement saved the token counts the
  API reported. Every other run is estimated: input as characters / 4, plus the tool
  definitions, which the trace spans don't record.
- Estimated output uses measured output-to-input ratios: 0.280 for the agent (the Homework 8
  starting run, same prompt and model as Homework 3) and 0.653 for the judge
  (`scripts/measure_judge_usage.py`, five calls of the frozen Homework 5 judge). Visible
  text alone undercounts badly, because both models bill hidden reasoning: about 89% of
  the judge's output tokens were reasoning.
- The estimates are probably low, by about 20%: Homework 3 comes out at 6,660 input tokens
  per turn, against 8,306 measured per conversation in the Homework 8 starting run.
- Not covered, because nothing was saved: the Homework 5 generation waves, Homework 6, the
  lost first Homework 8 tool-change run, and the Homework 1 command-line chats.

## Part B, fewer tokens (reverted)

The change dropped `quantity` and `shipped_at` from `list_my_orders` results, the largest
tool result in the Homework 3 traces. One shopper's 20-order list shrank from 4,492 to 3,640
characters.

| run | score | write_pass_5 | input | output | cost / 100 | median latency |
|---|---|---|---|---|---|---|
| Homework 8 final (Oct 13) | 0.875 | 0.5 | 191,560 | 32,950 | $1.40 | 46.3 s |
| control, no trim (Oct 14) | 0.875 | 0.5 | 206,192 | 32,676 | $1.49 | 46.0 s |
| fewer-tokens (Oct 14) | 0.900 | 0.5 | 215,997 | 32,287 | $1.55 | 20.6 s |

**Finding:** the trim had no measurable effect. The same agent on two days differed by 6%
in cost, and the trim against a back-to-back control differed by 4%, inside that noise.
Latency is not usable here: runs 30 minutes apart differed by more than 2x because the
shared endpoint changed pace. The control ran from commit 462149c on the local branch
`hw9-control`. Reverted, because no lower cost or latency could be attributed to the change.

## Part C, prompt order for caching (reverted)

The handout asks for an OpenAI model; the internal gateway has none, so muse-1-3 stands
in. Its Homework 8 runs already showed cached tokens. A placeholder `cached_input` price of
$0.50 per million (50% of input) was added before these runs.

| run | score | write_pass_5 | input | cached | cached_fraction | cost / 100 |
|---|---|---|---|---|---|---|
| cache-before | 0.925 | 0.5 | 230,834 | 27,096 | 0.117 | $1.58 |
| cache-after | 0.900 | 0.5 | 226,538 | 18,737 | 0.083 | $1.59 |

**Finding:** moving the Session context block to the end did not raise the cached fraction,
and the dip is noise, not an effect of the order.

- Calls inside one conversation already share almost their whole beginning (same user,
  same history) wherever the user id sits. That reuse is the 12% seen before the move.
- The move only helps between different users, on each conversation's first call: the
  shared beginning grows from about 940 to about 1,320 tokens. A development run has
  8 distinct users, so the move could add at most about 3,000 cached tokens (about 1.3% of
  input). Cached tokens fell by about 8,400, almost three times that maximum, so
  server-side factors swamped the order: which model replica answered, and how long its
  cache held.
- With an OpenAI model the move would matter more: OpenAI caches only beginnings of at
  least 1,024 tokens, and the shared part crosses that line only after the move.

Reverted under the handout's rule, because the development score dropped (0.925 to 0.900).
That drop is one e-002 run asking before refunding, inside the run-to-run noise, so the
reorder remains the better design for a deployment with many users.

## Part D, a model cascade on one judge (rejected)

The oracle is the frozen Homework 5 judge `irrelevant_policy_detail-v6` on muse-1-1; its
verdicts were already saved. The proxy is muse-1-2 with the same prompt plus one sentence
asking for a confidence from 0 to 1 (`../cascade/run_proxy.py`). Every proxy call kept the
token counts the API reported.

**Requirement, set before any results:** agreement with the oracle of at least 0.85 **and**
no missed oracle failures. Only 2 of the 59 dev conversations are failures, so a proxy that
always says "pass" would agree about 97% of the time; the second condition guards against that.

**Dev (`../cascade/results/thresholds.csv`):** the proxy caught both failures and raised one
false alarm, so agreement was 0.983 with the proxy deciding everything. Cost counts the proxy
on every conversation plus the oracle on the ones it hands off; the oracle's cost is
estimated on the same conversations.

| threshold | agreement with oracle | proxy share | cost per 1,000 verdicts |
|---|---|---|---|
| 0.0 to 0.8 | 0.983 | 100% | $4.73 |
| 0.9 | 1.000 | 92% | $5.19 |
| 0.95 | 1.000 | 80% | $5.80 |
| 1.0 | 1.000 | 7% | $9.47 |
| oracle alone | | | $5.01 |

The lowest threshold meeting the requirement is **0.0**: the proxy decides every verdict.
It was locked (`../cascade/results/chosen_threshold.json`) before the test run.

**Test check, once (`../cascade/results/check.json`):** agreement with the oracle 0.966,
**2 disagreements** in 59: one missed failure (a conversation the oracle and the human label
both call a fail, which the proxy passed at confidence 0.93) and one false alarm. Agreement
with the human labels 0.864. Cost $4.85 per 1,000 verdicts against $4.97 for the oracle
alone. **The requirement is not met, so the cascade is rejected.**

**Finding:** a cascade only saves money when the proxy is much cheaper than the oracle.
muse-1-2 thinks about as hard as muse-1-1 on this prompt (about 1,670 output tokens per
verdict for both), so the best case saved about 2%, every hand-off added cost, and the one
missed failure made it unsafe. An agreement-only requirement would have accepted it. The
oracle itself agrees with the human test labels only 0.898 of the time and misses 2 of 5
real failures, so a cascade built on it would not be ready for CI or monitoring either way.
A cheaper first step, such as a code check for replies that explain no policy rule, is the
more promising design.

The proxy runs hit the corporate proxy's cutoff for connections silent about a minute;
`run_proxy.py` resumes interrupted runs and retries a dropped call. A dropped call may
still have been billed without reporting its tokens.

## Part E, the frontier

`scripts/build_frontier.py` writes `results/frontier.csv`.

| candidate | score | write_pass_5 | cost / 100 | median latency | status |
|---|---|---|---|---|---|
| hw8-final | 0.875 | 0.5 | $1.40 | 46.3 s | frontier |
| fewer-tokens | 0.900 | 0.5 | $1.55 | 20.6 s | frontier |
| control-no-trim | 0.875 | 0.5 | $1.49 | 46.0 s | dominated |
| cache-before | 0.925 | 0.5 | $1.58 | 39.0 s | frontier |
| cache-after | 0.900 | 0.5 | $1.59 | 34.1 s | dominated |

**The configuration I would use:** the Homework 8 final agent, the configuration measured
in the cache-before run. It is on the frontier with the highest measured score, and none of
the cost-cutting changes in Parts B and C produced a difference larger than run-to-run
noise, so I kept none of them.

Limits of that choice:

- cache-before and hw8-final are the same agent code. The higher score (e-002 passing 2 of
  5 instead of 0) and the lower latency come from run-to-run variation and the server's pace,
  not from the agent.
- cache-before is the only frontier row billed with the 50% cached discount. At the full
  price it would be about $1.67 per 100 conversations.
- write_pass_5 is 0.5 in every row: e-002 usually asks before refunding. Refund reliability
  remains the weak spot.
- Homework 8 and 9 both tuned against the same 8 development cases. The only held-out check
  is Homework 8's test batch, where this agent scored 1.00 on 4 cases.
- With the cascade rejected, judge cost for CI or monitoring stays at the oracle's price.

## Runs billed without a cached discount

Runs before commit 91623f6, including the Homework 8 final run and both Part B runs, were
billed with every input token at the full price. They are left as billed. Under the $0.50
cached price they would be slightly cheaper.
