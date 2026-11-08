"""Run one case of the shop app and return everything the monitoring system recorded."""

import tempfile
import uuid
from pathlib import Path

from . import world

DRAIN_MS = 30_000  # traffic stops at the end; requests still running finish before the record is read


def run_case(repo, case):
    """*case*: `id`, `seed`, `baseline` (commit every service starts on), `deploys` ([{`at_ms`, `service`, `commit`}])
    and `duration_ms`; optional outside events: `catalogue` (size), `load_test` and `db_lock` ({`at_ms`}).
    Returns spans, logs, gauges, request metrics, deploys and the first page (or None)."""
    with tempfile.TemporaryDirectory() as folder:
        commits = {case["baseline"], *(d["commit"] for d in case["deploys"])}
        code = {}
        for commit in sorted(commits):
            world.export(repo, commit, Path(folder) / commit)
            code[commit] = world.load(Path(folder) / commit, f"shop_{case['id']}_{case['seed']}_{commit}")
        stock, skus, weights = world.catalogue(case["seed"], case.get("catalogue", 40))
        deployment = world.Deployment(code[case["baseline"]], case["seed"], uuid.uuid4().hex, stock)
        for service in reversed(world.SERVICES):
            deployment.deploy(service, code[case["baseline"]], case["baseline"])
        for d in case["deploys"]:
            deployment.runtime.call_at(d["at_ms"], lambda d=d: deployment.deploy(d["service"], code[d["commit"]],
                                                                                 d["commit"]))
        world.traffic(deployment, case["seed"], skus, weights, case["duration_ms"])
        if "load_test" in case:
            world.load_test(deployment, case["load_test"])
        if "db_lock" in case:
            deployment.hold_lock(case["db_lock"]["at_ms"])
        deployment.runtime.run(case["duration_ms"] + DRAIN_MS)
        deployment.close()
    telemetry = deployment.telemetry
    metrics = world.request_metrics(telemetry.spans, case["duration_ms"])
    return {"spans": list(telemetry.spans), "logs": list(telemetry.logs), "gauges": list(telemetry.gauges),
            "metrics": metrics, "deploys": list(deployment.deploys), "page": world.first_page(metrics)}
