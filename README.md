# Tracebench

A benchmark for on-call AI agents. **Write-up:** https://yashgix.github.io/tracebench/

A small shop with three services and a real git history has six incidents in it. Each one fires a page. An agent
investigates with the same tools an on-call engineer has (alerts, metrics, logs, traces, deploys, commits, diffs and
source), then decides: patch the code, escalate to a human, or dismiss the page. Every patch is checked outside the
agent's reach by the shop's own tests, hidden checks and a replay of the same traffic. The agent never grades its own
work.

The benchmark code is the `replaykit` package in this repository. The shop is
[yashgix/tracebench-shop](https://github.com/yashgix/tracebench-shop).

## Results (one run, 9 Nov 2026)

12 incidents (6 cases × 2 seeds), three low-cost models and two rule baselines, 220 model calls, $0.22 in total.

| investigator | resolved | right decision | root cause | code location | valid fix | cost |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | 5/12 | 10/12 | 8/8 | 8/8 | 2/8 | $0.051 |
| GPT-6 Luna | 5/12 | 8/12 | 7/8 | 7/8 | 3/8 | $0.029 |
| DeepSeek Flash | 5/12 | 6/12 | 6/8 | 6/8 | 5/8 | $0.139 |
| Revert the last deploy (rule) | 4/12 | 10/12 | 6/8 | 2/8 | 4/8 | $0 |
| Blame the deepest failing service (rule) | 0/12 | 0/12 | 8/8 | 2/8 | 0/8 | $0 |

Root cause, code location and valid fix count the 8 incidents that need a code change. A code location is the file
and, where there is one, the function (both config incidents are a module-level setting). Full report:
[`results/m4/score.md`](results/m4/score.md).

**Side check, not part of the official score.** Six patches were refused by `git apply` because their single hunk had
no trailing context, which a unified diff reads as "this hunk ends the file". The code in each was right. With the
next three lines of the deployed file appended as context, and nothing else changed, all six pass every check, which
would make it Haiku 8/12, Luna 7/12 and Flash 6/12. The official score keeps the rule as frozen before the run. Files:
[`results/m4/side-check/`](results/m4/side-check/).

What the run shows: finding the cause is easier than shipping the fix. No model fell for the decoy deploy in c3; the
revert-last-deploy rule did, in both seeds. The slow query (c4) was the one real coding miss. Haiku escalated the
outside lock both times, Luna dismissed the load test both times. Flash wrote the most valid fixes when it answered,
but lost 6 incidents to invalid or truncated answers.

## The six incidents

Every bad commit passes the shop's own tests, as a real one would pass CI.

| case | what breaks | right call |
|---|---|---|
| c1 leak | A refactor stops returning the database connection when an item is out of stock; the pool fills | patch |
| c2 config | A limit meant to slow stock bots counts checkouts per product instead of per user | patch |
| c3 decoy | A new two-item cap in orders throws a server error; a harmless web deploy follows 90 s later | patch |
| c4 slow query | Case-insensitive product lookups scan all 6,000 rows, so product pages time out | patch |
| c5 outside lock | A job outside the shop holds a database lock; nothing in the code is wrong | escalate |
| c6 load test | A planned load test hits the rate limit and is over before the page | dismiss |

## How an incident runs

1. **Build.** The shop is checked out at the incident's deployed commit and driven with seeded traffic on a simulated
   clock (`replaykit/app/world.py`, `incident.py`). Its real code produces the spans, logs and gauges, and the alert
   rules fire the page.
2. **Bundle.** Only what existed at the page goes to the agent: telemetry up to that moment and the commits up to the
   deployed one (`bundle.py`). Later commits, the fix and the answer are not in it.
3. **Investigate.** A bounded loop (`replaykit/investigate.py`): at most 15 turns, 25 tool calls, 200k tokens and 600 s
   of model time. Each turn the model either calls tools (`topology`, `overview`, `alerts`, `metrics`, `logs`,
   `deploys`, `traces`, `commits`, `diff`, `source`, `search`) or decides. A decision names the service, fault type,
   commit, file and function, cites the evidence ids it read, and for a patch carries a unified diff. Two invalid
   answers in a row, or one truncated answer, end the incident.
4. **Validate.** The patch is checked as text, applied to a fresh clone at the deployed commit, then the shop's own
   tests, the case's protected checks and a replay of the same traffic run on the patched code (`replaykit/app/patch.py`,
   `replaykit/app/protected/`). Model-written code runs in a sandbox with no network; the trusted side reads only a
   result file.
5. **Score.** Decisions, citations and validation results are scored against the case's truth (`replaykit/app/report.py`).

The protected checks were hidden from the models during the run. They are public now that the run is over; a new
version of the suite would need new hidden checks.

## Reproduce the results offline

No API keys or network. Python 3.12 or newer, standard library only.

```
git clone https://github.com/yashgix/tracebench
git clone https://github.com/yashgix/tracebench-shop
cd tracebench
make reproduce
```

`make reproduce` replays the recorded model answers (`results/m4/recorded.json`) through the investigation loop,
reruns both rule baselines, then scores the result with the recorded patch validations. It checks that the
investigation report and the score come out byte for byte identical to `results/m4/report.json` and `score.json`.
The tag `m4-evaluated` marks the exact code that ran; it and the current code both reproduce the results.

## Development

```
make test    # all tests (needs the shop clone next to this one, or TRACEBENCH_SHOP=path)
make lint    # ruff check and ruff format --check
```

The patch tests use macOS `sandbox-exec`, so they run on macOS (CI uses a macOS runner).

## What is not in this repository

Live model runs went through a small local broker that held the API keys, recorded every call and stopped at a
spending cap, and model patches ran as a separate macOS account. Both are part of my private tooling. `replaykit`
only has the client side (`replaykit/broker.py`, `--broker URL`); the recorded answers let anyone rerun everything
else. The earlier prompt-injection evaluation is not included either.

## Limits

- One run of 12 incidents. A difference of one incident between models is noise.
- The shop runs on a simulated clock with an in-memory SQLite database: an application-level demo, not a production
  system. The rule baselines read every event and all the code, and their thresholds were tuned on this simulator.
- Patched code runs in the same process as its checks. The sandbox keeps it away from files, keys and the network, and
  a person reviews every patch before it would ship.
- Escalate and dismiss need no cited evidence.

## How I built it

I ran Tracebench as a series of milestones, each with a written spec I approved before any code (`docs/SPEC-1.md` to
`SPEC-4.md`): telemetry replay and scoring, then rule baselines and pinned suites, then a tool-using investigator that
can escalate, then incidents in a real app with validated patches. I set the rules that keep it honest: models never
see the future or the answer, the suite and prompt are frozen before a paid run, every paid run has its own budget
that I approve with Touch ID, and diagnosis, evidence, escalation and patches are scored separately.

I built it with AI coding agents. One agent wrote the code to the spec and a different one reviewed every milestone
independently; nothing merged until that review passed. When a builder's own result fell short, I kept it recorded as
not accepted and had it fixed in labelled commits, so the history shows what the agent did and what was corrected. I
approved the six cases, decided that dismissing the load test is the right call, and kept the official score when the
side check looked better.

The shop's history ([tracebench-shop](https://github.com/yashgix/tracebench-shop)) is written by fictional developers
on purpose: the bugs are hidden in ordinary looking commits, as in a real repository.

## License

MIT. Yash Gupta · [github.com/yashgix](https://github.com/yashgix) · [linkedin.com/in/yash-guptaix](https://www.linkedin.com/in/yash-guptaix)
