# replaykit: replayable incident evaluation for AI SRE agents

Build the Python package `replaykit`. It turns a clean telemetry scenario plus a fault into a deterministic incident,
replays the incident to an agent in time order without leaking the future or the answer, scores the agent's
diagnosis, and compares agents across a suite. Python 3.12 or newer, standard library only, no network.

Layout: the package lives in `replaykit/` at the repository root (`replaykit/__init__.py` plus the modules below).
Put your own tests in `tests/` (runnable with `python3 -m unittest discover -s tests`). Write a short `README.md`
with a usage example. Optional: `examples/` with a scenario and a faults file.

## 1. Telemetry model (`replaykit/model.py`)

An **event** is a dict with exactly the keys `id` (str), `t` (int, seconds since the incident started), `kind`,
`service` (str) and `attrs` (dict). Kinds and their `attrs`:

| kind | attrs (exact keys) |
|---|---|
| `metric` | `name`: one of `error_rate`, `latency_ms`, `memory_mb`; `value`: int or float |
| `log` | `level`: one of `info`, `warn`, `error`; `message`: str |
| `deploy` | `version`: str |
| `alert` | `rule`: str; `severity`: `page` or `ticket` |

A **scenario** file is a JSON object with exactly the keys `id` (non-empty str), `duration` (int > 0), `services`
(non-empty list of distinct str), `calls` (object: caller service -> list of callee services) and `events` (list of
events: the clean background telemetry).

`load_scenario(path) -> dict` reads and validates a scenario file and returns it with `events` sorted by `(t, id)`.
It raises `ValueError` (with a message) for: invalid JSON, missing or extra keys, a wrong type, an unknown event kind,
a wrong or missing attrs key or value, an event or call naming a service not in `services`, a service calling
itself, a duplicate event id, or an event `t` outside `0..duration` inclusive. `bool` is not an accepted int.

`FAULT_TYPES = ("bad_deploy", "config_error", "dependency_timeout", "memory_leak")`.

`Diagnosis` is a frozen dataclass with fields `service: str`, `fault_type: str`, `confidence: float` and
`evidence: tuple[str, ...]` (cited event ids). Creating one raises `ValueError` if `fault_type` is not in
`FAULT_TYPES` or `confidence` is outside `0..1` inclusive.

## 2. Fault injection (`replaykit/inject.py`)

A **fault** is a dict with exactly the keys `type` (in `FAULT_TYPES`), `service` (in the scenario's services) and
`start` (int with `0 < start < duration`). Anything else raises `ValueError`.

`inject(scenario, fault, seed: int) -> Incident`. `Incident` is a dataclass with fields `id`, `scenario_id`,
`duration`, `services`, `calls`, `events` (list of event dicts) and `truth`. `Truth` is a frozen dataclass with
fields `service`, `fault_type`, `start` and `injected_ids` (frozenset of event ids). The incident id is
`f"{scenario_id}:{fault_type}:{service}:{seed}"`.

Use `random.Random(seed)` for every random value, so the same scenario, fault and seed always give identical events.
The **callers** of service S are the services whose `calls` list contains S.

**Generated metrics.** At every sample time `t = 0, 60, 120, ...` up to and including `duration`, each service gets
three metric events: `error_rate` uniform in `[0, 0.02]`, `latency_ms` uniform in `[80, 120]`, `memory_mb` uniform
in `[400, 420]`. Faults change some of these values as below. Background events from the scenario are kept with the
same `t`, `kind`, `service` and `attrs`.

**Fault effects** (S = the fault's service, `start` = its start; "from X" means every sample with `t >= X`):

- `bad_deploy`: a `deploy` event for S at `t = start`, `version` = `"r"` followed by 6 lowercase hex characters.
  S's `error_rate` from `start` is uniform in `[0.25, 0.5]`. An `error` log for S at every sample time from `start`
  with message `"request failed status=500"`. Callers' `error_rate` from `start + 60` is uniform in `[0.05, 0.15]`.
- `config_error`: an `info` log for S at `t = start` with message `"config reloaded"`, and no deploy. S's
  `error_rate` from `start` is uniform in `[0.25, 0.5]`. An `error` log for S at every sample time from `start` with
  message `"invalid configuration key"`. Callers' `error_rate` from `start + 60` is uniform in `[0.05, 0.15]`.
- `dependency_timeout`: S's `latency_ms` from `start` is uniform in `[2000, 5000]`, and a `warn` log for S at every
  sample time from `start` with message `"query exceeded 2000ms"`. Callers' `latency_ms` from `start + 60` is
  uniform in `[800, 1500]` and their `error_rate` from `start + 60` is uniform in `[0.05, 0.15]`.
- `memory_leak`: S's `memory_mb` at sample time `t >= start` is `420 + 40 * (k + 1)` where `k` is the number of
  sample times `>= start` before `t` (so strictly increasing by 40). At the first sample time where S's `memory_mb`
  is `>= 1000`, add an `error` log for S with message `"OOMKilled"` and from that sample on S's `error_rate` is
  uniform in `[0.1, 0.3]`.
- For `bad_deploy` and `config_error`: an `alert` for S at `t = start + 120` with rule `"error_rate_high"`, severity
  `"page"`. For `dependency_timeout`: the same with rule `"latency_high"`. For `memory_leak`: rule `"memory_high"` at
  the `OOMKilled` sample time. An alert is only added if its time is `<= duration`.

**Ids and order.** After building all events, sort them by `(t, kind, service, json.dumps(attrs, sort_keys=True))`
and assign ids `"ev-0001"`, `"ev-0002"`, ... in that order (zero-padded to at least 4 digits). Background event ids
are replaced too, so an id never reveals whether an event was injected.

`truth.injected_ids` holds exactly the events added by the fault (deploys, logs, alerts) plus the metric events
whose value range was changed by the fault. Background events and unchanged generated metrics are not in it.

## 3. Replay (`replaykit/replay.py`)

An **agent** is any object with `observe(new_events: list[dict], now: int) -> Diagnosis | None`.

`replay(incident, agent, step: int = 60) -> RunResult`. `RunResult` is a dataclass with fields `diagnosis`
(`Diagnosis` or `None`), `diagnosed_at` (int or `None`), `error` (str or `None`) and `calls` (int).

- `step` must be a positive int, else `ValueError`.
- Call `observe` at `now = step, 2*step, ...` while `now <= duration`, plus a final call at `now = duration` if
  `duration` is not a multiple of `step`. Each call receives, in incident order, exactly the events with
  `previous_now < t <= now` (for the first call: `t <= now`), as deep copies, so the agent cannot change the
  incident. The agent never receives `truth`.
- Stop at the first `Diagnosis`; set `diagnosed_at = now`.
- If `observe` raises, stop and set `error` to `f"{type(exc).__name__}: {exc}"`. If it returns something that is
  neither `None` nor a `Diagnosis`, stop and set `error = "invalid diagnosis"`. In both cases `diagnosis` is `None`.
- `calls` is the number of `observe` calls made.

## 4. Scoring (`replaykit/score.py`)

`score(incident, run) -> dict` with exactly these keys:

- `diagnosed`: the run has a diagnosis.
- `service_correct`, `type_correct`: the diagnosis matches `truth.service` / `truth.fault_type` (`False` if none).
- `root_cause_correct`: both are correct.
- `evidence_valid`: the evidence is non-empty and every cited id is an event of the incident with
  `t <= diagnosed_at` (`False` if no diagnosis).
- `evidence_precision`: the fraction of cited ids that are in `truth.injected_ids`, rounded to 4 places (`0.0` if no
  diagnosis or no evidence).
- `delay`: `diagnosed_at - truth.start`, or `None` if no diagnosis.
- `points`: `0` if not diagnosed or the evidence is not valid. Otherwise `50 * service_correct + 30 * type_correct
  + 20 * timeliness * root_cause_correct`, where `timeliness = max(0, 1 - delay / (duration - start))` if
  `delay >= 0`, else `0`. Rounded to 2 places.

`brier(pairs) -> float | None`: for a list of `(confidence, correct)` pairs, the mean of
`(confidence - (1 if correct else 0)) ** 2`, rounded to 4 places; `None` for an empty list.

## 5. Baseline agents (`replaykit/baselines.py`)

Each agent keeps its own state across `observe` calls. A new instance is used for each incident.

- `LastDeployAgent`: at the first call after it has seen at least one `deploy` event, diagnose the most recent
  deploy seen so far (largest `t`, ties broken by the larger id) as `bad_deploy` with confidence `0.6` and evidence
  `(deploy_id,)`. Otherwise return `None`.
- `ThresholdAgent`: track each service's latest value of each metric (and the id of that metric event) and the
  deploys seen. At each call, after taking in the new events, check in this order:
  1. any service with latest `memory_mb >= 900`: diagnose the one with the highest value as `memory_leak`;
  2. else any service with latest `latency_ms >= 1500`: the highest as `dependency_timeout`;
  3. else any service with latest `error_rate >= 0.2`: the highest; type `bad_deploy` if a deploy for that service
     has been seen, else `config_error`.

  Ties go to the service name that sorts first. Confidence `0.7`. Evidence: the id of the triggering metric event,
  followed by the id of that service's latest deploy when the type is `bad_deploy`. Return `None` if nothing
  triggers.

## 6. Suite and report (`replaykit/suite.py`)

`run_suite(incidents, agents, step=60) -> dict`, where `agents` maps a name to a zero-argument factory that returns
a fresh agent. Every agent runs on every incident with a new instance. Result:

```
{"agents": {name: {"runs": int, "diagnosed": int, "root_cause_accuracy": float, "mean_points": float,
                   "mean_delay": float | None, "brier": float | None, "errors": int}},
 "incidents": [{"id": incident_id, "results": {name: {**score_dict, "error": str | None}}}]}
```

- `root_cause_accuracy` = correct runs / runs (4 places); `mean_points` (2 places); `mean_delay` = mean `delay` of
  root-cause-correct runs (1 place) or `None`; `brier` over diagnosed runs, using `root_cause_correct`; `errors` =
  runs whose `error` is not `None`.
- Incidents keep the given order; agent names are sorted inside every dict.

## 7. Command line (`python3 -m replaykit`)

```
python3 -m replaykit run --scenario PATH --faults PATH --seeds 1,2,3 --agents last_deploy,threshold [--out PATH]
```

- `--faults` is a JSON list of faults. Incidents are built for each fault (in file order) and each seed (in the given
  order). Agent names: `last_deploy` -> `LastDeployAgent`, `threshold` -> `ThresholdAgent`.
- Writes the `run_suite` report as JSON to `--out` when given. Prints a Markdown table to stdout: the header
  `| agent | runs | root cause accuracy | mean points | brier |`, a separator row, then one row per agent sorted by
  name; `brier` prints as `-` when `None`.
- Exit code 0 on success. On invalid input (bad file, bad fault, unknown agent, bad seeds) print `error: <message>`
  to stderr and exit with code 2.
