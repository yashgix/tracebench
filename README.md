# replaykit

Replayable incident evaluation for AI SRE agents. Turns a clean telemetry scenario plus a fault into a
deterministic incident, replays it to an agent in time order without leaking the future or the answer, scores the
diagnosis, and compares agents across a suite.

Python 3.12 or newer, standard library only, no network.

## Install

From the repository root (no packaging step needed):

```
python3 -c "import replaykit; print(replaykit.__version__)"
```

## Modules

| module | role |
| --- | --- |
| `replaykit.model` | event/scenario validation, `load_scenario`, `FAULT_TYPES`, `Diagnosis` |
| `replaykit.inject` | deterministic `inject(scenario, fault, seed) -> Incident` |
| `replaykit.replay` | time-ordered `replay(incident, agent, step) -> RunResult` |
| `replaykit.score` | `score(incident, run)` and `brier(pairs)` |
| `replaykit.baselines` | `LastDeployAgent`, `ThresholdAgent` |
| `replaykit.suite` | `run_suite(incidents, agents, step)` report |
| `replaykit.__main__` | `python3 -m replaykit run ...` |

## Command line

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

`brier` prints as `-` when there is nothing to score. Invalid input (bad file, bad fault, unknown agent, bad seeds)
prints `error: <message>` to stderr and exits with code 2.

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
incident = inject(scenario, {"type": "bad_deploy", "service": "db", "start": 300}, seed=1)
run = replay(incident, ErrorRateAgent())
print(score(incident, run))
```

To compare several agents, pass zero-argument factories to `run_suite(incidents, {"name": factory})`; every agent
runs on every incident with a fresh instance.

## Scoring

`score` returns `diagnosed`, `service_correct`, `type_correct`, `root_cause_correct`, `evidence_valid`,
`evidence_precision`, `delay` and `points`. Points are `50 * service_correct + 30 * type_correct` plus up to 20 for
timeliness, and are `0` when the diagnosis is missing or the cited evidence is empty, unknown or from the future.

## Examples

`examples/scenario.json` is a three-service scenario (`api -> db -> cache`, 20 minutes of background telemetry) and
`examples/faults.json` lists one fault per type that the CLI replays against it.

## Tests

```
python3 -m unittest discover -s tests
```
