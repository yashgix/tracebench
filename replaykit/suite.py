"""Run every agent on every incident and aggregate the results."""

import hashlib
import json
import os

from . import __version__
from .baselines import AGENT_NAMES, make_agent
from .inject import inject, validate_fault
from .model import parse_scenario
from .replay import replay
from .report import breakdown
from .score import brier, noise_score, score

SUITE_KEYS = (
    "id",
    "scenario",
    "scenario_sha256",
    "seeds",
    "step",
    "agents",
    "cases",
)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _summary(records):
    runs = len(records)
    diagnosed = sum(1 for run, _ in records if run.diagnosis is not None)
    correct = sum(1 for _, result in records if result["root_cause_correct"])
    total_points = sum(result["points"] for _, result in records)
    delays = [
        result["delay"]
        for _, result in records
        if result["root_cause_correct"] and result["delay"] is not None
    ]
    pairs = [
        (run.diagnosis.confidence, result["root_cause_correct"])
        for run, result in records
        if run.diagnosis is not None
    ]
    return {
        "runs": runs,
        "diagnosed": diagnosed,
        "root_cause_accuracy": round(correct / runs, 4) if runs else 0.0,
        "mean_points": round(total_points / runs, 2) if runs else 0.0,
        "mean_delay": round(sum(delays) / len(delays), 1) if delays else None,
        "brier": brier(pairs),
        "errors": sum(1 for run, _ in records if run.error is not None),
    }


def _evaluate(incidents, agents, step):
    names = sorted(agents)
    records = {name: [] for name in names}
    incident_reports = []
    runs_by_incident = []
    for incident in incidents:
        results = {}
        runs = {}
        for name in names:
            run = replay(incident, agents[name](), step)
            result = score(incident, run)
            results[name] = {**result, "error": run.error}
            records[name].append((run, result))
            runs[name] = run
        incident_reports.append({"id": incident.id, "results": results})
        runs_by_incident.append(runs)
    report = {
        "agents": {name: _summary(records[name]) for name in names},
        "incidents": incident_reports,
    }
    return report, runs_by_incident


def run_suite(incidents, agents, step=60):
    """Run every agent factory on every incident and aggregate scores."""
    report, _ = _evaluate(incidents, agents, step)
    return report


def _validate_suite_shape(suite):
    if not isinstance(suite, dict):
        raise ValueError("suite must be a JSON object")
    if set(suite) != set(SUITE_KEYS):
        raise ValueError(f"suite must have exactly the keys {sorted(SUITE_KEYS)}")
    if not isinstance(suite["id"], str) or not suite["id"]:
        raise ValueError("suite id must be a non-empty string")
    if not isinstance(suite["scenario"], str) or not suite["scenario"]:
        raise ValueError("suite scenario must be a non-empty string")
    sha = suite["scenario_sha256"]
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(character not in "0123456789abcdef" for character in sha)
    ):
        raise ValueError("suite scenario_sha256 must be 64 lowercase hex characters")

    seeds = suite["seeds"]
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("suite seeds must be a non-empty list")
    if any(not _is_int(seed) for seed in seeds):
        raise ValueError("suite seeds must be ints")
    if len(set(seeds)) != len(seeds):
        raise ValueError("suite seeds must be distinct")

    step = suite["step"]
    if not _is_int(step) or step <= 0:
        raise ValueError("suite step must be a positive int")

    agents = suite["agents"]
    if not isinstance(agents, list) or not agents:
        raise ValueError("suite agents must be a non-empty list")
    if any(not isinstance(name, str) or name not in AGENT_NAMES for name in agents):
        raise ValueError(f"suite agents must be names from {AGENT_NAMES}")
    if len(set(agents)) != len(agents):
        raise ValueError("suite agents must be distinct")

    if not isinstance(suite["cases"], list) or not suite["cases"]:
        raise ValueError("suite cases must be a non-empty list")


def load_suite(path):
    """Read and validate a pinned suite, returning it with hashes and its scenario."""
    suite_path = os.fspath(path)
    try:
        with open(suite_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError(f"cannot read suite {suite_path!r}: {exc}") from None
    try:
        suite = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON in {suite_path}: {exc}") from None
    _validate_suite_shape(suite)

    base = os.path.dirname(os.path.abspath(suite_path))
    scenario_path = os.path.join(base, suite["scenario"])
    try:
        with open(scenario_path, "rb") as handle:
            scenario_raw = handle.read()
    except OSError as exc:
        raise ValueError(
            f"cannot read scenario {suite['scenario']!r}: {exc}"
        ) from None
    scenario_sha256 = hashlib.sha256(scenario_raw).hexdigest()
    if scenario_sha256 != suite["scenario_sha256"]:
        raise ValueError(
            f"scenario changed: expected {suite['scenario_sha256']}, "
            f"got {scenario_sha256}"
        )
    scenario = parse_scenario(scenario_raw, scenario_path)
    for case in suite["cases"]:
        validate_fault(scenario, case)

    return {
        "suite": suite,
        "scenario": scenario,
        "suite_sha256": hashlib.sha256(raw).hexdigest(),
        "scenario_sha256": scenario_sha256,
    }


def run_pinned(path):
    """Run a suite file and return a deterministic report with truth and breakdown."""
    loaded = load_suite(path)
    suite = loaded["suite"]
    scenario = loaded["scenario"]

    incidents = []
    incident_cases = []
    for case in suite["cases"]:
        for seed in suite["seeds"]:
            incidents.append(inject(scenario, case, seed))
            incident_cases.append(case)

    calls = scenario["calls"]
    factories = {
        name: (lambda name=name: make_agent(name, calls)) for name in suite["agents"]
    }
    report, runs_by_incident = _evaluate(incidents, factories, suite["step"])

    report["meta"] = {
        "suite_id": suite["id"],
        "suite_sha256": loaded["suite_sha256"],
        "scenario_id": scenario["id"],
        "scenario_sha256": loaded["scenario_sha256"],
        "replaykit_version": __version__,
        "seeds": list(suite["seeds"]),
        "step": suite["step"],
    }

    for entry, incident, case, runs in zip(
        report["incidents"], incidents, incident_cases, runs_by_incident
    ):
        truth = incident.truth
        entry["truth"] = {
            "service": truth.service,
            "fault_type": truth.fault_type,
            "start": truth.start,
            "cascade": case.get("cascade", False),
            "decoy_service": truth.decoy_service,
        }
        for name, result in entry["results"].items():
            run = runs[name]
            result.update(noise_score(incident, run))
            diagnosis = run.diagnosis
            result["diagnosed_service"] = (
                diagnosis.service if diagnosis is not None else None
            )
            result["diagnosed_type"] = (
                diagnosis.fault_type if diagnosis is not None else None
            )

    report["breakdown"] = breakdown(report)
    return report
