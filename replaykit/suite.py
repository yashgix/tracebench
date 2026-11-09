"""Run every agent on every incident and aggregate the results."""

import hashlib
import json
import os
import re
from pathlib import Path

from . import __version__
from .baselines import AGENT_NAMES, make_agent
from .inject import inject, validate_fault
from .investigate import REPLY_KEYS, Limits, Reply, baseline_investigation, investigate, packet_sha256
from .model import _is_int, parse_scenario
from .replay import replay
from .report import breakdown, findings
from .score import brier, noise_score, score, score_investigation

SUITE_KEYS = ("id", "scenario", "scenario_sha256", "seeds", "step", "agents", "cases")
INVESTIGATION_KEYS = ("id", "scenario", "scenario_sha256", "seeds", "limits", "investigators", "cases")
MODEL_NAME = re.compile(r"[a-z0-9][a-z0-9.-]*")
GIVEN = Path(__file__).resolve().parent / "investigator"


def _summary(records):
    runs = len(records)
    diagnosed = sum(1 for run, _ in records if run.diagnosis is not None)
    correct = sum(1 for _, result in records if result["root_cause_correct"])
    total_points = sum(result["points"] for _, result in records)
    delays = [result["delay"] for _, result in records if result["root_cause_correct"] and result["delay"] is not None]
    pairs = [
        (run.diagnosis.confidence, result["root_cause_correct"]) for run, result in records if run.diagnosis is not None
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


def _validate_names(value, what, allowed):
    if not isinstance(value, list) or not value:
        raise ValueError(f"suite {what} must be a non-empty list")
    if any(not isinstance(name, str) or not allowed(name) for name in value):
        raise ValueError(
            f"suite {what} must be names from {AGENT_NAMES}"
            if what == "agents"
            else f"suite {what} must be baseline or model names"
        )
    if len(set(value)) != len(value):
        raise ValueError(f"suite {what} must be distinct")


def _validate_common(suite, keys):
    if not isinstance(suite, dict):
        raise ValueError("suite must be a JSON object")
    if set(suite) != set(keys):
        raise ValueError(f"suite must have exactly the keys {sorted(keys)}")
    if not isinstance(suite["id"], str) or not suite["id"]:
        raise ValueError("suite id must be a non-empty string")
    if not isinstance(suite["scenario"], str) or not suite["scenario"]:
        raise ValueError("suite scenario must be a non-empty string")
    if os.path.isabs(suite["scenario"]):
        raise ValueError("suite scenario must be a relative path")
    sha = suite["scenario_sha256"]
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("suite scenario_sha256 must be 64 lowercase hex characters")
    seeds = suite["seeds"]
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("suite seeds must be a non-empty list")
    if any(not _is_int(seed) for seed in seeds):
        raise ValueError("suite seeds must be ints")
    if len(set(seeds)) != len(seeds):
        raise ValueError("suite seeds must be distinct")
    if not isinstance(suite["cases"], list) or not suite["cases"]:
        raise ValueError("suite cases must be a non-empty list")


def _validate_suite_shape(suite):
    _validate_common(suite, SUITE_KEYS)
    step = suite["step"]
    if not _is_int(step) or step <= 0:
        raise ValueError("suite step must be a positive int")
    _validate_names(suite["agents"], "agents", lambda name: name in AGENT_NAMES)


def _is_investigator(name):
    return name in AGENT_NAMES or MODEL_NAME.fullmatch(name) is not None


def _validate_investigation_shape(suite):
    _validate_common(suite, INVESTIGATION_KEYS)
    limits = suite["limits"]
    fields = list(Limits.__dataclass_fields__)
    if not isinstance(limits, dict) or set(limits) != set(fields):
        raise ValueError(f"suite limits must have exactly the keys {sorted(fields)}")
    if any(not _is_int(limits[key]) or limits[key] <= 0 for key in fields):
        raise ValueError("suite limits must be positive ints")
    _validate_names(suite["investigators"], "investigators", _is_investigator)
    pairs = [(case.get("type"), case.get("service")) for case in suite["cases"] if isinstance(case, dict)]
    if len(set(pairs)) != len(pairs):
        raise ValueError("suite cases must not repeat a type and service")


def _read(path, what):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ValueError(f"cannot read {what} {os.fspath(path)!r}: {exc}") from None


def _load(path, validate):
    suite_path = os.fspath(path)
    raw = _read(suite_path, "suite")
    try:
        suite = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON in {suite_path}: {exc}") from None
    validate(suite)
    scenario_path = os.path.join(os.path.dirname(os.path.abspath(suite_path)), suite["scenario"])
    try:
        with open(scenario_path, "rb") as handle:
            scenario_raw = handle.read()
    except OSError as exc:
        raise ValueError(f"cannot read scenario {suite['scenario']!r}: {exc}") from None
    scenario_sha256 = hashlib.sha256(scenario_raw).hexdigest()
    if scenario_sha256 != suite["scenario_sha256"]:
        raise ValueError(f"scenario changed: expected {suite['scenario_sha256']}, got {scenario_sha256}")
    scenario = parse_scenario(scenario_raw, scenario_path)
    for case in suite["cases"]:
        validate_fault(scenario, case)
    return {
        "suite": suite,
        "scenario": scenario,
        "suite_sha256": hashlib.sha256(raw).hexdigest(),
        "scenario_sha256": scenario_sha256,
    }


def load_suite(path):
    """Read and validate a pinned suite, returning it with hashes and its scenario."""
    return _load(path, _validate_suite_shape)


def load_investigation_suite(path):
    """Read and validate an investigation suite, returning it with hashes and its scenario."""
    return _load(path, _validate_investigation_shape)


def _incidents(loaded):
    suite = loaded["suite"]
    return [(inject(loaded["scenario"], case, seed), case) for case in suite["cases"] for seed in suite["seeds"]]


def _meta(loaded):
    suite, scenario = loaded["suite"], loaded["scenario"]
    return {
        "suite_id": suite["id"],
        "suite_sha256": loaded["suite_sha256"],
        "scenario_id": scenario["id"],
        "scenario_sha256": loaded["scenario_sha256"],
        "replaykit_version": __version__,
        "seeds": list(suite["seeds"]),
    }


def _pinned_truth(entry, incident, case, runs):
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
        result["diagnosed_service"] = diagnosis.service if diagnosis is not None else None
        result["diagnosed_type"] = diagnosis.fault_type if diagnosis is not None else None


def run_pinned(path):
    """Run a suite file and return a deterministic report with truth and breakdown."""
    loaded = load_suite(path)
    suite = loaded["suite"]
    built = _incidents(loaded)
    calls = loaded["scenario"]["calls"]
    factories = {name: (lambda name=name: make_agent(name, calls)) for name in suite["agents"]}
    report, runs_by_incident = _evaluate([incident for incident, _ in built], factories, suite["step"])
    report["meta"] = {**_meta(loaded), "step": suite["step"]}
    for entry, (incident, case), runs in zip(report["incidents"], built, runs_by_incident):
        _pinned_truth(entry, incident, case, runs)
    report["breakdown"] = breakdown(report)
    return report


def _investigator_summary(results):
    count = len(results)

    def total(key, expected=None):
        return sum(1 for r in results if r[key] and expected in (None, r["expected"]))

    def cases(expected):
        return sum(1 for r in results if r["expected"] == expected)

    return {
        "incidents": count,
        "resolved": total("resolved"),
        "decision_correct": total("decision_correct"),
        "root_cause_correct": total("root_cause_correct"),
        "diagnose_cases": cases("diagnose"),
        "evidence_valid": total("evidence_valid"),
        "invalid_evidence": sum(1 for r in results if r["decision"] is not None and not r["evidence_valid"]),
        "escalated": total("decision_correct", "escalate"),
        "escalate_cases": cases("escalate"),
        "false_escalations": sum(1 for r in results if r["decision"] == "escalate" and r["expected"] != "escalate"),
        "dismissed": total("decision_correct", "dismiss"),
        "dismiss_cases": cases("dismiss"),
        "injection_followed": total("injection_followed"),
        "injection_cases": sum(1 for r in results if r["injection_service"] is not None),
        "decoy_blamed": total("decoy_blamed"),
        "budget_exhausted": sum(1 for r in results if r["outcome"] == "budget_exhausted"),
        "mean_turns": round(sum(r["turns"] for r in results) / count, 1),
        "mean_tool_calls": round(sum(r["tool_calls"] for r in results) / count, 1),
        "tokens": sum(r["tokens"] for r in results),
        "cost_microusd": sum(r["cost_microusd"] for r in results),
        "latency_ms": sum(r["latency_ms"] for r in results),
    }


def _investigation_result(incident, name, models, limits):
    if name in AGENT_NAMES:
        inv = baseline_investigation(incident, name)
    else:
        inv = investigate(incident, models[name], limits)
    return {**score_investigation(incident, inv), "now": inv.now, "action": inv.decision, "transcript": inv.transcript}


def _investigation_truth(truth):
    return {
        "service": truth.service,
        "fault_type": truth.fault_type,
        "start": truth.start,
        "expected": truth.expected,
        "decoy_service": truth.decoy_service,
        "injection_service": truth.injection_service,
    }


def run_investigations(path, models):
    """Run every investigator of an investigation suite on every incident and report the results."""
    loaded = load_investigation_suite(path)
    suite = loaded["suite"]
    names = sorted(suite["investigators"])
    missing = [name for name in names if name not in AGENT_NAMES and name not in models]
    if missing:
        raise ValueError(f"no model given for {missing}")
    limits = Limits(**suite["limits"])
    incidents = []
    for incident, _ in _incidents(loaded):
        results = {name: _investigation_result(incident, name, models, limits) for name in names}
        incidents.append({"id": incident.id, "truth": _investigation_truth(incident.truth), "results": results})
    summaries = {}
    for name in names:
        rows = [
            {**entry["results"][name], "injection_service": entry["truth"]["injection_service"]} for entry in incidents
        ]
        summaries[name] = {"kind": "baseline" if name in AGENT_NAMES else "model", **_investigator_summary(rows)}
    meta = {
        **_meta(loaded),
        "limits": dict(suite["limits"]),
        "prompt_sha256": hashlib.sha256((GIVEN / "prompt.txt").read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256((GIVEN / "action.schema.json").read_bytes()).hexdigest(),
    }
    report = {"meta": meta, "investigators": summaries, "incidents": incidents}
    report["findings"] = findings(report)
    return report


class RecordedModel:
    """A model that answers from the transcripts of one investigator in a saved report."""

    def __init__(self, report, name):
        self._turns = {}
        for entry in report["incidents"]:
            for turn in entry["results"][name]["transcript"]:
                self._turns[(entry["id"], turn["turn"])] = turn

    def __call__(self, incident_id, turn, packet):
        recorded = self._turns.get((incident_id, turn))
        if recorded is None:
            raise ValueError(f"no recorded turn {turn} for {incident_id}")
        if recorded["packet_sha256"] != packet_sha256(packet):
            raise ValueError(f"recording does not match turn {turn} of {incident_id}")
        return Reply(*(recorded[key] for key in REPLY_KEYS))
