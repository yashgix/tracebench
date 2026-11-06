"""Breakdowns and Markdown summaries for pinned suite reports."""

from .model import FAULT_TYPES

__all__ = ["agents_table", "breakdown", "render_markdown"]


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
