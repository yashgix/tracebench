# replaykit milestone 2: realistic incidents, pinned suites, useful reports

The repository already implements milestone 1 (the full specification follows as Part A). Extend it. Everything in
Part A still holds unless this part changes it; keep every existing public name and behaviour. Python 3.12 or newer,
standard library only, no network. Keep the style of the existing code. Update `README.md` and add your own tests
for the new behaviour in `tests/`. `fixtures/` and `suites/` are given; do not change them.

## M1. New fault type `pool_exhaustion` (`replaykit/model.py`, `replaykit/inject.py`)

`FAULT_TYPES = ("bad_deploy", "config_error", "dependency_timeout", "memory_leak", "pool_exhaustion")`.
`replaykit.__version__ = "0.2.0"`.

Effects of `pool_exhaustion` (S = the fault's service; "from X" means every sample with `t >= X`):
- S's `latency_ms` from `start` is uniform in `[1500, 3000]`; S's `error_rate` from `start + 60` is uniform in
  `[0.1, 0.3]`.
- A `warn` log for S at every sample time from `start` with message `"connection pool exhausted (50/50 in use)"`.
- The services S calls are unchanged: their pool is not the problem.
- Callers: the same effects as for `dependency_timeout` (`latency_ms` from `start + 60` uniform in `[800, 1500]`,
  `error_rate` from `start + 60` uniform in `[0.05, 0.15]`).
- An `alert` for S at `t = start + 120` with rule `"latency_high"`, severity `"page"`, if that time is `<= duration`.

All of these events and changed metrics are in `truth.injected_ids`, as in Part A.

## M2. Faults: optional `cascade` and `noise` keys (`replaykit/inject.py`)

A fault has the keys `type`, `service` and `start`, and may also have `cascade` and `noise`. Any other key, a missing
required key, or a wrong value raises `ValueError` (`bool` is not an accepted int, as in Part A).

### Cascading timeouts (`cascade`)
`cascade` is a bool, default `false`. `true` is allowed only for `dependency_timeout` and `pool_exhaustion`; for
other types it raises `ValueError`.

The **hop** of a service is its shortest distance to S along reversed `calls` edges: the callers of S are at hop 1,
their callers (not already at a smaller hop) at hop 2, and so on. S itself and services that do not reach S have no
hop.

- Without cascade, nothing changes from Part A: only hop-1 services get the caller effects.
- With cascade, instead of the caller effects, every service at hop `h >= 1` gets, at every sample with
  `t >= start` (requests waiting on S slow down at once): `latency_ms` uniform in `[4000 * h + 1000,
  4000 * h + 4000]` and `error_rate` uniform in `[0.2, 0.4]`. These metrics are in `truth.injected_ids`.

### Noise (`noise`)
`noise` is an object with optional keys `decoy_deploy` (bool, default `false`) and `flapping_alerts` (int,
`0..20`, default `0`). Any other key or a wrong value raises `ValueError`.

Noise draws its random values from its own generator, `random.Random(f"{seed}:noise")`, so adding noise never
changes the `t`, `kind`, `service` or `attrs` of any other event (only ids may shift).

- `decoy_deploy`: a `deploy` event for a service D at `t = max(0, start - 120)`, with `version` = `"r"` followed by
  6 lowercase hex characters. D is chosen with the noise generator among the services other than S, in
  `services` order. It is a harmless deploy that happens just before the incident.
- `flapping_alerts`: that many `alert` events, each with severity `"ticket"`, a rule chosen from
  `("disk_usage_high", "cert_expiring", "cpu_throttled")`, a service chosen among the services other than S, and a
  sample time chosen among all sample times (`0, 60, ...` up to `duration`), all with the noise generator.
- Any noise in a scenario with a single service raises `ValueError`.

Noise events are never in `truth.injected_ids`. `Truth` gains two fields with defaults, so existing code that builds
`Truth` with four fields keeps working: `noise_ids: frozenset = frozenset()` (the ids of all noise events) and
`decoy_service: str | None = None` (D, or `None` without a decoy deploy).

The incident id stays `f"{scenario_id}:{fault_type}:{service}:{seed}"`.

## M3. Noise scores (`replaykit/score.py`)

`score` is unchanged. New `noise_score(incident, run) -> dict` with exactly these keys:
- `decoy_blamed`: the incident has a decoy deploy and the diagnosis names `truth.decoy_service` (`False` if no
  diagnosis).
- `noise_cited`: how many cited evidence ids are in `truth.noise_ids` (`0` if no diagnosis).

## M4. `TopologyAgent` and the agent registry (`replaykit/baselines.py`)

`TopologyAgent(calls)` takes the incident's `calls`. It tracks, per service, the latest value and event id of each
metric, the latest `deploy` id, and the latest id of a `warn` log with the message `"connection pool exhausted
(50/50 in use)"`. At each call, after taking in the new events:

1. A service is **anomalous** if its latest `memory_mb >= 900`, `latency_ms >= 1500` or `error_rate >= 0.2`.
2. The **candidates** are the anomalous services that call no anomalous service. If there are anomalous services
   but no candidates (a cycle), all anomalous services are candidates. With no anomalous service, return `None`.
3. Diagnose the candidate whose name sorts first. Its type, checking in this order: `memory_leak` if
   `memory_mb >= 900`; else, if `latency_ms >= 1500`, `pool_exhaustion` when a pool log for it has been seen, else
   `dependency_timeout`; else `bad_deploy` when a deploy for it has been seen, else `config_error`.
4. Confidence `0.8`. Evidence: the id of the triggering metric event (the metric that decided the type), followed
   by the latest pool log id for `pool_exhaustion`, or the latest deploy id for `bad_deploy`.

`AGENT_NAMES = ("last_deploy", "threshold", "topology")` and `make_agent(name, calls)` returns a new agent for a
name (`topology` gets `calls`); an unknown name raises `ValueError`. The `run` command accepts `topology` too.

## M5. Pinned suites (`replaykit/suite.py`)

A **suite file** is a JSON object with exactly the keys:
`id` (non-empty str), `scenario` (str: a path relative to the suite file's folder), `scenario_sha256` (64 lowercase
hex characters), `seeds` (non-empty list of distinct ints), `step` (int > 0), `agents` (non-empty list of distinct
names from `AGENT_NAMES`) and `cases` (non-empty list of faults, each valid for the scenario as in M2).

`load_suite(path) -> dict` reads and validates a suite file and returns `{"suite": <the suite object>,
"scenario": <load_scenario result>, "suite_sha256": <SHA-256 hex of the suite file's bytes>,
"scenario_sha256": <SHA-256 hex of the scenario file's bytes>}`. It raises `ValueError` for any problem, including a
scenario file whose SHA-256 differs from `scenario_sha256` (message starting `"scenario changed"`).

`run_pinned(path) -> dict` loads the suite, builds one incident per case (in file order) and seed (in suite order),
runs `run_suite` with the suite's agents (built with `make_agent`) and `step`, and returns the `run_suite` result
with these changes:
- a `"meta"` key: `{"suite_id", "suite_sha256", "scenario_id", "scenario_sha256", "replaykit_version", "seeds",
  "step"}`;
- every incident entry gains `"truth": {"service", "fault_type", "start", "cascade", "decoy_service"}` (`cascade` is
  the fault's value or `false`), and every per-agent result in it gains the two `noise_score` keys plus
  `diagnosed_service` and `diagnosed_type` (the diagnosis's `service` and `fault_type`, or `None` without one);
- a `"breakdown"` key (M6).

The same suite file always gives an identical report: no timestamps, no absolute paths, nothing from the
environment.

## M6. Breakdown and Markdown report (`replaykit/report.py`)

`breakdown(report) -> dict` computes from a `run_pinned` report (without `breakdown`):
```
{"by_fault_type": {agent: {fault_type: {"runs": int, "correct": int}}},
 "confusion": {agent: {truth_fault_type: {diagnosed_fault_type or "none": count}}},
 "decoy_blamed": {agent: int}, "noise_cited": {agent: int}}
```
`correct` counts `root_cause_correct` runs. Only fault types that occur in the suite appear. `confusion` counts every
run by its true type and its diagnosed type (`"none"` when not diagnosed). `decoy_blamed` and `noise_cited` sum the
`noise_score` values over runs.

`render_markdown(report) -> str` returns, for a full `run_pinned` report, these parts separated by one empty line,
ending with a newline:
1. `# Suite <suite_id>`
2. `` suite `<first 12 hex of suite_sha256>` · scenario <scenario_id> `<first 12 hex of scenario_sha256>` · replaykit
   <version> · seeds <seeds joined by ", "> · step <step> `` (one line)
3. `## Agents`, then the Part A table (`| agent | runs | root cause accuracy | mean points | brier |`, a separator row,
   one row per agent).
4. `## Accuracy by fault type`, then a table `| agent | <type> | ... |` with the suite's fault types in
   `FAULT_TYPES` order, a separator row, and one row per agent with cells `correct/runs` (e.g. `2/3`).
5. `## Misdiagnoses`, then `| agent | truth | diagnosed | runs |`, a separator row, and one row per confusion entry
   whose diagnosed type differs from the true type, sorted by agent, truth, diagnosed. If there are none, the line
   `None.` instead of the table.
6. `## Decoys and noise`, then `| agent | decoy blamed | noise cited |`, a separator row, one row per agent.

Separator rows are `|` followed by ` --- |` once per column. Cells print values as Python's `str()` does, and
`None` as `-`. Agents are sorted by name everywhere.

## M7. Command line

```
python3 -m replaykit suite PATH [--out PATH] [--md PATH]
```
Runs `run_pinned`. Writes the report to `--out` as `json.dumps(report, indent=2, sort_keys=True) + "\n"`, writes the
Markdown to `--md`, and prints the Markdown to stdout. Exit code 0 on success. On invalid input print
`error: <message>` to stderr and exit with code 2. The `run` command keeps its Part A behaviour.

## Given files
- `fixtures/shop.json`: a 7-service shop (edge, web, cart, checkout, payments, inventory, orders-db) with
  background telemetry.
- `suites/shop-v1.json`: 8 cases (connection pool exhaustion with cascading timeouts and a decoy deploy, slow
  database with cascade, deploys, configuration errors, a memory leak, noisy alerts) × 3 seeds, all three agents.

---

# Part A: milestone 1 specification (already implemented)

