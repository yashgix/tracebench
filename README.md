# replaykit

Replayable incident evaluation for AI SRE agents. Turns a clean telemetry scenario plus a fault into a
deterministic incident, replays it to an agent in time order without leaking the future or the answer, scores the
diagnosis, and compares agents across a suite. Version 0.2.0 adds connection-pool failures, cascading timeouts,
decoy/noise telemetry and pinned, reproducible suites.

Python 3.12 or newer, standard library only, no network.

## Install

From the repository root (no packaging step needed):

```
python3 -c "import replaykit; print(replaykit.__version__)"
```

`replaykit.__version__` is `0.2.0`.

## Modules

| module | role |
| --- | --- |
| `replaykit.model` | event/scenario validation, `load_scenario`, `FAULT_TYPES`, `Diagnosis` |
| `replaykit.inject` | deterministic `inject(scenario, fault, seed) -> Incident` |
| `replaykit.replay` | time-ordered `replay(incident, agent, step) -> RunResult` |
| `replaykit.score` | `score(incident, run)`, `noise_score(incident, run)` and `brier(pairs)` |
| `replaykit.baselines` | `LastDeployAgent`, `ThresholdAgent`, `TopologyAgent`, `make_agent` |
| `replaykit.suite` | `run_suite`, `load_suite`, `run_pinned` |
| `replaykit.report` | `breakdown` and `render_markdown` |
| `replaykit.__main__` | `python3 -m replaykit run ...` and `suite ...` |

## Incidents and faults

A fault is a JSON object with `type`, `service` and `start`, and may also have `cascade` and `noise`.

`FAULT_TYPES` is `bad_deploy`, `config_error`, `dependency_timeout`, `memory_leak`, `pool_exhaustion`.
`pool_exhaustion` raises latency, raises the error rate after one sample, emits connection-pool `warn` logs and
pages with a `latency_high` alert.

`cascade` is a bool and is allowed only for `dependency_timeout` and `pool_exhaustion`. When true, callers at
every hop get increasing latency and error-rate effects instead of only the first-hop caller effects.

`noise` is an object with optional `decoy_deploy` (bool) and `flapping_alerts` (int, `0..20`). A decoy deploy is a
harmless deploy shortly before the incident; flapping alerts are unrelated `ticket` alerts. Noise uses its own
random generator, so it never changes the non-noise events, and noise ids are never part of `truth.injected_ids`.

## Command line

Run a set of faults against a scenario:

```
python3 -m replaykit run \
    --scenario examples/scenario.json \
    --faults examples/faults.json \
    --seeds 1,2 \
    --agents last_deploy,threshold \
    --out report.json
```

Incidents are built for each fault (in file order) and each seed (in the given order). The Markdown summary goes to
stdout, e.g.:

```
| agent | runs | root cause accuracy | mean points | brier |
| --- | --- | --- | --- | --- |
| last_deploy | 6 | 0.3333 | 41.2 | 0.4123 |
| threshold | 6 | 0.6667 | 62.5 | 0.2531 |
```

`brier` prints as `-` when there is nothing to score. The `run` command accepts `last_deploy`, `threshold` and
`topology`.

Run a pinned suite:

```
python3 -m replaykit suite suites/shop-v1.json --out report.json --md report.md
```

The suite command writes `report.json` with `json.dumps(report, indent=2, sort_keys=True) + "\n"`, writes the
Markdown report to `report.md` when requested, and prints the same Markdown to stdout. Invalid input (bad file, bad
fault, unknown agent, bad seeds, hash mismatch) prints `error: <message>` to stderr and exits with code 2.

## Writing an agent

An agent is any object with `observe(new_events, now)` returning a `Diagnosis` or `None`. It sees only the events
with `previous_now < t <= now`, as deep copies, and never sees the truth.

```python
from replaykit.inject import inject
from replaykit.model import Diagnosis, load_scenario
from replaykit.replay import replay
from replaykit.score import score


class ErrorRateAgent:
    def __init__(self):
        self.worst = None  # (value, service, event id)

    def observe(self, events, now):
        for event in events:
            if event["kind"] == "metric" and event["attrs"]["name"] == "error_rate":
                value = event["attrs"]["value"]
                if self.worst is None or value > self.worst[0]:
                    self.worst = (value, event["service"], event["id"])
        if self.worst and self.worst[0] >= 0.2:
            return Diagnosis(self.worst[1], "config_error", 0.6, (self.worst[2],))
        return None


scenario = load_scenario("examples/scenario.json")
incident = inject(scenario, {"type": "pool_exhaustion", "service": "db", "start": 300}, seed=1)
run = replay(incident, ErrorRateAgent())
print(score(incident, run))
```

To compare several agents, pass zero-argument factories to `run_suite(incidents, {"name": factory})`; every agent
runs on every incident with a fresh instance. `make_agent(name, calls)` returns a fresh baseline by name.

## Baseline agents

- `LastDeployAgent`: blames the most recent deploy seen so far as `bad_deploy`.
- `ThresholdAgent`: diagnoses the highest threshold breach, preferring memory, then latency, then errors.
- `TopologyAgent(calls)`: diagnoses the deepest anomalous service in the call graph, distinguishing
  `pool_exhaustion` by connection-pool logs.

## Pinned suites

A suite file is a JSON object with exactly `id`, `scenario`, `scenario_sha256`, `seeds`, `step`, `agents` and
`cases`. `scenario` is relative to the suite file; `scenario_sha256` pins the scenario bytes. `load_suite(path)`
returns the suite, the loaded scenario and both SHA-256 hashes, and raises `ValueError` on any problem (including
`scenario changed`). `run_pinned(path)` runs one incident per case and seed and adds `meta`, per-incident `truth`,
per-result `noise_score`/`diagnosed_service`/`diagnosed_type`, and `breakdown` to the `run_suite` report. The same
suite file always produces the same report.

`report.py` renders the pinned report as: the suite header, the agents table, accuracy by fault type, misdiagnoses,
and decoy/noise totals.

## Scoring

`score` returns `diagnosed`, `service_correct`, `type_correct`, `root_cause_correct`, `evidence_valid`,
`evidence_precision`, `delay` and `points`. Points are `50 * service_correct + 30 * type_correct` plus up to 20 for
timeliness, and are `0` when the diagnosis is missing or the cited evidence is empty, unknown or from the future.

`noise_score(incident, run)` returns `decoy_blamed` (the diagnosis named the decoy deploy's service) and
`noise_cited` (how many cited evidence ids are noise events).

`brier(pairs)` is the mean squared error of confidences against correctness, rounded to 4 places, or `None` for an
empty list.

## Examples

`examples/scenario.json` is a three-service scenario (`api -> db -> cache`, 20 minutes of background telemetry) and
`examples/faults.json` lists sample faults that the CLI can replay against it. `fixtures/shop.json` is a seven-service
shop scenario, and `suites/shop-v1.json` is a pinned suite of eight cases across three seeds and three agents.

## Tests

```
python3 -m unittest discover -s tests
```
