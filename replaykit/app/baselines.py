"""Two rule baselines for app incidents (SPEC-4 B6). They read the whole bundle without tools, and their thresholds were
chosen for this simulator, so they have an advantage no model gets; reports label them as rules."""

import re

from ..investigate import Investigation

NAMES = ("deepest_abnormal_commit", "revert_last_deploy")
HUNK = re.compile(r"@@ -(\S+) \+(\S+) @@")
SLOW_MS = 1000


def _decision(act, service="-", fault_type="-", commit="-", file="-", evidence=(), patch=""):
    return {
        "act": act,
        "calls": [],
        "service": service,
        "fault_type": fault_type,
        "commit": commit,
        "file": file,
        "function": "-",
        "confidence": 50 if act in ("diagnose", "patch") else 0,
        "evidence": list(evidence),
        "summary": "rule baseline",
        "patch": patch,
    }


def _signals(incident):
    """Per service, the ids of the events before the page that look abnormal, and the kinds of trouble they show."""
    found = {service: ([], set()) for service in incident.services}
    for event in incident.events:
        attrs, kind = event["attrs"], None
        if event["kind"] == "log" and attrs["level"] in ("warn", "error"):
            kind = "limit" if "limit" in attrs["message"] else "error"
        elif event["kind"] == "metric" and attrs["value"]:
            kind = {
                "http.server.error_rate": "error",
                "db.client.connections.pending_requests": "pool",
                "http.server.duration.p95": "slow" if attrs["value"] >= SLOW_MS else None,
            }.get(attrs["name"])
        if kind is not None:
            ids, kinds = found[event["service"]]
            ids.append(event["id"])
            kinds.add(kind)
    return {service: signal for service, signal in found.items() if signal[0]}


def _fault_type(kinds):
    if "pool" in kinds:
        return "resource_leak"
    if "limit" in kinds:
        return "config_error"
    return "slow_query" if "slow" in kinds else "code_defect"


def _height(calls, service, seen=()):
    """The longest chain of calls from *service* down to a service that calls nothing (0 for that service)."""
    return max(
        (1 + _height(calls, callee, (*seen, service)) for callee in calls[service] if callee not in seen), default=0
    )


def _reverse(diff):
    """The unified diff that undoes *diff* (as git shows a commit)."""
    lines = []
    for line in diff.removesuffix("\n").split("\n"):
        if line.startswith(("--- ", "+++ ", "diff --git ")):
            lines.append(line)
        elif line.startswith("index "):
            before, after = line[6:].split(" ")[0].split("..")
            lines.append(line.replace(f"{before}..{after}", f"{after}..{before}"))
        elif line.startswith("@@"):
            lines.append(HUNK.sub(lambda m: f"@@ -{m[2]} +{m[1]} @@", line, count=1))
        elif line[:1] in ("+", "-"):
            lines.append({"+": "-", "-": "+"}[line[0]] + line[1:])
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def revert_last_deploy(incident):
    """Revert the newest deploy before the page; escalate when nothing was deployed since the start."""
    deploys = [e for e in incident.events if e["kind"] == "deploy" and e["t"] > 0]
    if not deploys:
        return _decision("escalate")
    last = max(deploys, key=lambda e: (e["t"], e["id"]))
    service, commit = last["service"], last["attrs"]["version"]
    ids, kinds = _signals(incident).get(service, ([], set()))
    return _decision(
        "patch",
        service,
        _fault_type(kinds),
        commit,
        incident.code["commits"][commit]["files"][0],
        [last["id"], *ids][:8],
        _reverse(incident.code["diffs"][commit]),
    )


def deepest_abnormal_commit(incident):
    """Blame the deployed commit of the abnormal service furthest down the call graph; dismiss when none looks
    abnormal."""
    signals = _signals(incident)
    if not signals:
        return _decision("dismiss")
    service = min(signals, key=lambda s: (_height(incident.calls, s), s))
    ids, kinds = signals[service]
    commit = incident.code["deployed"][service]
    return _decision(
        "diagnose", service, _fault_type(kinds), commit, incident.code["commits"][commit]["files"][0], ids[:8]
    )


def baseline_investigation(incident, name):
    """Rule *name*'s decision as an investigation that saw every event and all code in the bundle."""
    decision = {"deepest_abnormal_commit": deepest_abnormal_commit, "revert_last_deploy": revert_last_deploy}[name](
        incident
    )
    code = incident.code
    observed = (
        {e["id"] for e in incident.events}
        | set(code["commits"])
        | {f for c in code["commits"].values() for f in c["files"]}
    )
    return Investigation(decision, "decided", incident.duration, 0, 0, frozenset(observed))
