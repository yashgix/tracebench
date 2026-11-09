"""Turn a recorded run of the shop app into an incident: what monitoring and git showed at the first page. The truth
stays in `cases.py`; nothing here reads it."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import world

LEVELS = {"INFO": "info", "WARN": "warn", "ERROR": "error"}


@dataclass(frozen=True)
class AppIncident:
    id: str
    services: list
    calls: dict
    duration: int
    events: list
    spans: list
    code: dict


def _events(raw, end):
    rows = [(d["time_ms"], "deploy", d["service"], {"version": d["commit"]}) for d in raw["deploys"]]
    rows += [
        (m["time_ms"], "metric", m["service"], {"name": m["name"], "value": m["value"]})
        for m in raw["metrics"] + raw["gauges"]
    ]
    rows += [
        (
            log["time_ms"],
            "log",
            log["service"],
            {"level": LEVELS[log["severity"]], "message": log["body"], "trace_id": log["trace_id"]},
        )
        for log in raw["logs"]
    ]
    page = raw["page"]
    rows.append((page["time_ms"], "alert", page["service"], {"rule": page["rule"], "severity": page["severity"]}))
    rows = sorted((row for row in rows if row[0] <= end), key=lambda r: (*r[:3], json.dumps(r[3], sort_keys=True)))
    return [
        {"id": f"ev-{i:04d}", "t": t, "kind": kind, "service": service, "attrs": attrs}
        for i, (t, kind, service, attrs) in enumerate(rows, 1)
    ]


def _calls(spans):
    calls = {service: set() for service in world.SERVICES}
    for span in spans:
        if span["kind"] == "client":
            calls[span["service"]].add(span["attributes"]["peer.service"])
    return {service: sorted(callees) for service, callees in calls.items()}


def _git(repo, *args):
    return subprocess.run(
        ["git", "--git-dir", str(Path(repo) / ".git"), *args], capture_output=True, check=True, text=True
    ).stdout


def _commit(repo, full):
    when, author, message = _git(repo, "show", "-s", "--format=%aI%x00%an%x00%s", full).rstrip("\n").split("\0")
    files = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", full).splitlines()
    diff = _git(
        repo,
        "diff-tree",
        "-p",
        "--root",
        "--no-renames",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-commit-id",
        full,
    )
    return {"hash": full[:7], "time": when, "author": author, "message": message, "files": files}, diff


def _code(repo, deploys, end):
    """Git as the investigator may see it: the history behind each commit deployed by *end*, and those trees."""
    live = [d for d in deploys if d["time_ms"] <= end]
    deployed = {d["service"]: d["commit"] for d in live}
    shipped = sorted({d["commit"] for d in live})
    history, commits, diffs = {}, {}, {}
    for short in shipped:
        fulls = _git(repo, "rev-list", short).split()
        history[short] = [full[:7] for full in fulls]
        for full in fulls:
            if full[:7] not in commits:
                commits[full[:7]], diffs[full[:7]] = _commit(repo, full)
    trees = {short: {str(path): data.decode() for path, data in world.blobs(repo, short)} for short in shipped}
    return {"deployed": deployed, "history": history, "commits": commits, "diffs": diffs, "trees": trees}


def bundle(repo, case, raw):
    """The incident of *case* (its run description) from *raw*, the output of `incident.run_case`."""
    page = raw["page"]
    if page is None:
        raise ValueError(f"case {case['id']} seed {case['seed']} never pages")
    end = page["time_ms"]
    spans = [span for span in raw["spans"] if span["end_ms"] <= end]
    return AppIncident(
        id=f"shop-app:{case['id']}:{case['seed']}",
        services=list(world.SERVICES),
        calls=_calls(spans),
        duration=end,
        events=_events(raw, end),
        spans=spans,
        code=_code(repo, raw["deploys"], end),
    )
