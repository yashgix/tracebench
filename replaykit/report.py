"""Breakdowns and Markdown summaries for pinned suite reports."""

from .model import FAULT_TYPES

__all__ = ["agents_table", "breakdown", "findings", "render_investigation_markdown", "render_markdown"]


def _cell(value):
    return "-" if value is None else str(value)


def _table(headers, rows):
    lines = [
        "| " + " | ".join(str(header) for header in headers) + " |",
        "|" + " --- |" * len(headers),
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def agents_table(report):
    """Render the Part A agent summary table."""
    headers = ["agent", "runs", "root cause accuracy", "mean points", "brier"]
    rows = []
    for name in sorted(report.get("agents", {})):
        summary = report["agents"][name]
        rows.append(
            [
                name,
                summary.get("runs"),
                summary.get("root_cause_accuracy"),
                summary.get("mean_points"),
                summary.get("brier"),
            ]
        )
    return _table(headers, rows)


def breakdown(report):
    """Compute fault-type accuracy, confusion and noise totals for a pinned report."""
    agents = sorted(report.get("agents", {}))
    incidents = report.get("incidents", [])
    present = [
        fault_type
        for fault_type in FAULT_TYPES
        if any(
            entry.get("truth", {}).get("fault_type") == fault_type
            for entry in incidents
        )
    ]

    by_fault_type = {
        agent: {
            fault_type: {"runs": 0, "correct": 0} for fault_type in present
        }
        for agent in agents
    }
    confusion = {agent: {} for agent in agents}
    decoy_blamed = {agent: 0 for agent in agents}
    noise_cited = {agent: 0 for agent in agents}

    for entry in incidents:
        truth_type = entry.get("truth", {}).get("fault_type")
        results = entry.get("results", {})
        for agent in agents:
            result = results.get(agent)
            if result is None:
                continue
            if truth_type in by_fault_type[agent]:
                bucket = by_fault_type[agent][truth_type]
                bucket["runs"] += 1
                if result.get("root_cause_correct"):
                    bucket["correct"] += 1

            diagnosed = result.get("diagnosed_type")
            if diagnosed is None:
                diagnosed = "none"
            diagnosed_counts = confusion[agent].setdefault(truth_type, {})
            diagnosed_counts[diagnosed] = diagnosed_counts.get(diagnosed, 0) + 1

            if result.get("decoy_blamed"):
                decoy_blamed[agent] += 1
            noise_cited[agent] += int(result.get("noise_cited", 0) or 0)

    return {
        "by_fault_type": by_fault_type,
        "confusion": confusion,
        "decoy_blamed": decoy_blamed,
        "noise_cited": noise_cited,
    }


def render_markdown(report):
    """Render a full pinned report as the milestone 2 Markdown summary."""
    meta = report.get("meta", {})
    suite_sha = meta.get("suite_sha256") or ""
    scenario_sha = meta.get("scenario_sha256") or ""
    seeds = ", ".join(str(seed) for seed in meta.get("seeds", []))

    sections = [
        f"# Suite {meta.get('suite_id')}",
        (
            f"suite `{suite_sha[:12]}` \u00b7 scenario {meta.get('scenario_id')} "
            f"`{scenario_sha[:12]}` \u00b7 replaykit {meta.get('replaykit_version')} "
            f"\u00b7 seeds {seeds} \u00b7 step {meta.get('step')}"
        ),
        "## Agents\n\n" + agents_table(report),
    ]

    data = breakdown(report)
    present = [
        fault_type
        for fault_type in FAULT_TYPES
        if any(
            entry.get("truth", {}).get("fault_type") == fault_type
            for entry in report.get("incidents", [])
        )
    ]

    headers = ["agent"] + present
    rows = []
    for agent in sorted(report.get("agents", {})):
        by_type = data.get("by_fault_type", {}).get(agent, {})
        row = [agent]
        for fault_type in present:
            counts = by_type.get(fault_type, {"runs": 0, "correct": 0})
            row.append(f"{counts.get('correct', 0)}/{counts.get('runs', 0)}")
        rows.append(row)
    sections.append("## Accuracy by fault type\n\n" + _table(headers, rows))

    misdiagnoses = []
    confusion = data.get("confusion", {})
    for agent in sorted(confusion):
        for truth_type in sorted(confusion[agent]):
            for diagnosed_type in sorted(confusion[agent][truth_type]):
                if diagnosed_type == truth_type:
                    continue
                runs = confusion[agent][truth_type][diagnosed_type]
                if runs:
                    misdiagnoses.append(
                        [agent, truth_type, diagnosed_type, runs]
                    )
    if misdiagnoses:
        sections.append(
            "## Misdiagnoses\n\n"
            + _table(["agent", "truth", "diagnosed", "runs"], misdiagnoses)
        )
    else:
        sections.append("## Misdiagnoses\n\nNone.")

    noise_rows = [
        [
            agent,
            data.get("decoy_blamed", {}).get(agent, 0),
            data.get("noise_cited", {}).get(agent, 0),
        ]
        for agent in sorted(report.get("agents", {}))
    ]
    sections.append(
        "## Decoys and noise\n\n"
        + _table(["agent", "decoy blamed", "noise cited"], noise_rows)
    )

    return "\n\n".join(sections) + "\n"


def _usd(microusd):
    return f"{microusd / 1e6:.4f}"


def _each(summaries, sentence, keep=lambda s: True):
    return [sentence(name, summaries[name]) for name in sorted(summaries) if keep(summaries[name])]


def _counted(s, key, sentence):
    return _each(s, lambda n, x: sentence.format(name=n, k=x[key]), lambda x: x[key] > 0)


def findings(report):
    """Plain sentences about an investigation report, in a fixed order."""
    s = report["investigators"]
    names = sorted(s)
    best = min(names, key=lambda n: (-s[n]["resolved"], n))
    lines = [f"Most resolved: {best}, {s[best]['resolved']} of {s[best]['incidents']}."]
    paid = [n for n in names if s[n]["cost_microusd"] > 0 and s[n]["resolved"] > 0]
    if paid:
        cheap = min(paid, key=lambda n: (s[n]["cost_microusd"] / s[n]["resolved"], n))
        lines.append(f"Cheapest per resolved incident: {cheap}, ${_usd(s[cheap]['cost_microusd'] / s[cheap]['resolved'])}.")
    lines += _each(s, lambda n, x: f"{n} followed the injected instruction in {x['injection_followed']} of "
                   f"{x['injection_cases']} incidents.", lambda x: x["injection_followed"] > 0)
    if any(x["escalate_cases"] for x in s.values()):
        lines += _each(s, lambda n, x: f"{n} escalated {x['escalated']} of {x['escalate_cases']} incidents that "
                       "needed a human.")
    lines += _counted(s, "false_escalations", "{name} escalated {k} incidents that did not need a human.")
    if any(x["dismiss_cases"] for x in s.values()):
        lines += _each(s, lambda n, x: f"{n} dismissed {x['dismissed']} of {x['dismiss_cases']} false pages.")
    lines += _counted(s, "invalid_evidence", "{name} cited evidence it had not seen in {k} incidents.")
    lines += _counted(s, "decoy_blamed", "{name} blamed the decoy deploy in {k} incidents.")
    lines += _counted(s, "budget_exhausted", "{name} ran out of budget in {k} incidents.")
    return lines


def _decisions_table(report):
    headers = ["investigator", "kind", "resolved", "right decision", "root cause", "evidence valid", "escalated",
               "false escalations", "dismissed", "injection followed"]
    rows = []
    for name, x in sorted(report["investigators"].items()):
        n = x["incidents"]
        rows.append([
            name, x["kind"], f"{x['resolved']}/{n}", f"{x['decision_correct']}/{n}",
            f"{x['root_cause_correct']}/{x['diagnose_cases']}", f"{x['evidence_valid']}/{n}",
            f"{x['escalated']}/{x['escalate_cases']}", x["false_escalations"], f"{x['dismissed']}/{x['dismiss_cases']}",
            f"{x['injection_followed']}/{x['injection_cases']}",
        ])
    return "## Decisions\n\n" + _table(headers, rows)


def _baseline_note(report):
    names = sorted(n for n, x in report["investigators"].items() if x["kind"] == "baseline")
    if not names:
        return None
    return (f"Baselines ({', '.join(names)}) are rules written for this simulator's thresholds: they read every event "
            "up to the page without tools. Models get only general instructions and must request their evidence.")


def _effort_table(report):
    headers = ["investigator", "mean turns", "mean tool calls", "tokens", "cost usd", "latency s"]
    rows = [[name, x["mean_turns"], x["mean_tool_calls"], x["tokens"], _usd(x["cost_microusd"]),
             f"{x['latency_ms'] / 1000:.1f}"] for name, x in sorted(report["investigators"].items())]
    return "## Effort\n\n" + _table(headers, rows)


def _wrong_decisions(report):
    rows = []
    for entry in report["incidents"]:
        for name in sorted(entry["results"]):
            result = entry["results"][name]
            if not result["resolved"]:
                action = result["action"]
                rows.append([name, entry["id"], result["expected"], result["decision"],
                             action["service"] if action is not None else None])
    if not rows:
        return "## Wrong decisions\n\nNone."
    return "## Wrong decisions\n\n" + _table(["investigator", "incident", "expected", "decision", "service"], rows)


def render_investigation_markdown(report):
    """Render an investigation report as Markdown."""
    meta = report["meta"]
    limits = meta["limits"]
    header = (
        f"suite `{meta['suite_sha256'][:12]}` \u00b7 scenario {meta['scenario_id']} `{meta['scenario_sha256'][:12]}` "
        f"\u00b7 replaykit {meta['replaykit_version']} \u00b7 seeds {', '.join(str(s) for s in meta['seeds'])} "
        f"\u00b7 limits {limits['turns']} turns, {limits['tool_calls']} tool calls, {limits['tokens']} tokens, "
        f"{limits['model_ms']} ms"
    )
    sections = [
        f"# Investigation {meta['suite_id']}",
        header,
        _decisions_table(report),
        _baseline_note(report),
        _effort_table(report),
        _wrong_decisions(report),
        "## Findings\n\n" + "\n".join(f"- {line}" for line in report["findings"]),
    ]
    return "\n\n".join(section for section in sections if section is not None) + "\n"
