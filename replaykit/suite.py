"""Run every agent on every incident and aggregate the results."""

from .replay import replay
from .score import brier, score


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


def run_suite(incidents, agents, step=60):
    """Run every agent factory on every incident and aggregate scores."""
    names = sorted(agents)
    records = {name: [] for name in names}
    incident_reports = []
    for incident in incidents:
        results = {}
        for name in names:
            run = replay(incident, agents[name](), step)
            result = score(incident, run)
            results[name] = {**result, "error": run.error}
            records[name].append((run, result))
        incident_reports.append({"id": incident.id, "results": results})
    return {
        "agents": {name: _summary(records[name]) for name in names},
        "incidents": incident_reports,
    }
