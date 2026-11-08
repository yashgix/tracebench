"""The M4 report (SPEC-4 B6): diagnosis, escalation and dismissal, and remediation counted apart, per investigator."""

from ..report import _table, _usd


def _count(rows, test):
    return sum(1 for r in rows if test(r))


def _validation(r, key):
    v = r["validation"]
    return v is not None and bool(key(v))


def summary(rows):
    """Totals for one investigator's scored results *rows*."""
    n, patch_cases = len(rows), [r for r in rows if r["expected"] == "patch"]
    proposed = [r for r in rows if r["decision"] == "patch"]
    return {
        "incidents": n, "resolved": _count(rows, lambda r: r["resolved"]),
        "decision_correct": _count(rows, lambda r: r["decision_correct"]),
        "patch_cases": len(patch_cases),
        "root_cause_correct": _count(patch_cases, lambda r: r["root_cause_correct"]),
        "location_correct": _count(patch_cases, lambda r: r["location_correct"]),
        "evidence_valid": _count(rows, lambda r: r["evidence_valid"]),
        "invalid_evidence": _count(rows, lambda r: r["decision"] is not None and not r["evidence_valid"]),
        "escalate_cases": _count(rows, lambda r: r["expected"] == "escalate"),
        "escalated": _count(rows, lambda r: r["expected"] == r["decision"] == "escalate"),
        "false_escalations": _count(rows, lambda r: r["decision"] == "escalate" != r["expected"]),
        "dismiss_cases": _count(rows, lambda r: r["expected"] == "dismiss"),
        "dismissed": _count(rows, lambda r: r["expected"] == r["decision"] == "dismiss"),
        "false_dismissals": _count(rows, lambda r: r["decision"] == "dismiss" != r["expected"]),
        "patches": len(proposed),
        "patch_applies": _count(proposed, lambda r: _validation(r, lambda v: v["applies"])),
        "own_tests_pass": _count(proposed, lambda r: _validation(r, lambda v: v["own_tests"] == "pass")),
        "protected_pass": _count(proposed, lambda r: _validation(
            r, lambda v: v["protected"] is not None and not v["protected"]["failed"])),
        "replay_clean": _count(proposed, lambda r: _validation(r, lambda v: v["replay"] and v["replay"]["clean"])),
        "patch_valid": _count(patch_cases, lambda r: r["patch_valid"]),
        "patches_where_none_needed": _count(proposed, lambda r: r["expected"] != "patch"),
        "validation_timeouts": _count(proposed, lambda r: _validation(r, lambda v: v["own_tests"] == "timeout")),
        "budget_exhausted": _count(rows, lambda r: r["outcome"] == "budget_exhausted"),
        "no_decision": _count(rows, lambda r: r["decision"] is None),
        "model_stopped": _count(rows, lambda r: r["outcome"].startswith("model_")),
        "mean_turns": round(sum(r["turns"] for r in rows) / n, 1),
        "mean_tool_calls": round(sum(r["tool_calls"] for r in rows) / n, 1),
        "tokens": sum(r["tokens"] for r in rows), "cost_microusd": sum(r["cost_microusd"] for r in rows),
        "latency_ms": sum(r["latency_ms"] for r in rows),
    }


def summaries(scored):
    """{investigator: {kind, totals}} of a scored app report."""
    kinds = scored["meta"]["investigators"]
    return {name: {"kind": kind, **summary([entry["results"][name] for entry in scored["incidents"]])}
            for name, kind in kinds.items()}


def findings(s):
    """Plain sentences about the totals *s*, in a fixed order."""
    names = sorted(s)
    best = min(names, key=lambda n: (-s[n]["resolved"], n))
    lines = [f"Most resolved: {best}, {s[best]['resolved']} of {s[best]['incidents']}."]
    paid = [n for n in names if s[n]["cost_microusd"] > 0 and s[n]["resolved"] > 0]
    if paid:
        cheap = min(paid, key=lambda n: (s[n]["cost_microusd"] / s[n]["resolved"], n))
        lines.append(f"Cheapest per resolved incident: {cheap}, "
                     f"${_usd(s[cheap]['cost_microusd'] / s[cheap]['resolved'])}.")
    for name in names:
        x = s[name]
        lines.append(f"{name}: root cause {x['root_cause_correct']} of {x['patch_cases']}, valid fix "
                     f"{x['patch_valid']} of {x['patch_cases']}, {x['patches']} patches proposed.")
    sentences = (("patches_where_none_needed", "{name} proposed a patch in {k} incidents that needed none."),
                 ("false_escalations", "{name} escalated {k} incidents it should have handled."),
                 ("false_dismissals", "{name} dismissed {k} real incidents."),
                 ("invalid_evidence", "{name} cited evidence it had not seen in {k} incidents."),
                 ("budget_exhausted", "{name} ran out of budget in {k} incidents."),
                 ("model_stopped", "{name} got no answer from its model in {k} incidents (lane stopped or error)."),
                 ("validation_timeouts", "{name} had {k} patch validations time out."))
    for key, sentence in sentences:
        lines += [sentence.format(name=n, k=s[n][key]) for n in names if s[n][key]]
    return lines


def _rows(s, cells):
    return [[name, x["kind"], *cells(x)] for name, x in sorted(s.items())]


def _decisions(s):
    headers = ["investigator", "kind", "resolved", "right decision", "escalated", "false escalations", "dismissed",
               "false dismissals"]
    return "## Decisions\n\n" + _table(headers, _rows(s, lambda x: [
        f"{x['resolved']}/{x['incidents']}", f"{x['decision_correct']}/{x['incidents']}",
        f"{x['escalated']}/{x['escalate_cases']}", x["false_escalations"], f"{x['dismissed']}/{x['dismiss_cases']}",
        x["false_dismissals"]]))


def _diagnosis(s):
    headers = ["investigator", "kind", "root cause", "file and function", "evidence valid", "invalid evidence"]
    return ("## Diagnosis (incidents that need a fix)\n\n" + _table(headers, _rows(s, lambda x: [
        f"{x['root_cause_correct']}/{x['patch_cases']}", f"{x['location_correct']}/{x['patch_cases']}",
        f"{x['evidence_valid']}/{x['incidents']}", x["invalid_evidence"]])))


def _remediation(s):
    headers = ["investigator", "kind", "patches", "applies", "own tests", "protected checks", "replay clean",
               "valid fix", "patch not needed"]
    return ("## Remediation (validated on the trusted side)\n\n" + _table(headers, _rows(s, lambda x: [
        x["patches"], x["patch_applies"], x["own_tests_pass"], x["protected_pass"], x["replay_clean"],
        f"{x['patch_valid']}/{x['patch_cases']}", x["patches_where_none_needed"]])))


def _effort(s):
    headers = ["investigator", "kind", "mean turns", "mean tool calls", "tokens", "cost usd", "latency s"]
    return "## Effort\n\n" + _table(headers, _rows(s, lambda x: [
        x["mean_turns"], x["mean_tool_calls"], x["tokens"], _usd(x["cost_microusd"]), f"{x['latency_ms'] / 1000:.1f}"]))


def _incidents(scored):
    rows = []
    for entry in scored["incidents"]:
        for name in sorted(entry["results"]):
            r = entry["results"][name]
            action = r["action"] or {}
            rows.append([entry["id"], name, r["expected"], r["decision"], action.get("service"), action.get("commit"),
                         r["root_cause_correct"], r["patch_valid"] if r["decision"] == "patch" else None,
                         r["resolved"]])
    headers = ["incident", "investigator", "expected", "decision", "service", "commit", "root cause", "valid fix",
               "resolved"]
    return "## Each incident\n\n" + _table(headers, rows)


def _baseline_note(s):
    names = sorted(n for n, x in s.items() if x["kind"] == "baseline")
    if not names:
        return None
    return (f"Baselines ({', '.join(names)}) are rules, not models: they read every event and all code in the bundle "
            "without tools, and their thresholds were picked for this simulator. Neither names a function, so neither "
            "resolves a fix inside a function; compare them on root cause and on valid fixes.")


def render_app_markdown(report):
    """Render a scored app report as Markdown."""
    meta, s = report["meta"], report["investigators"]
    limits = meta["limits"]
    header = (f"suite `{meta['suite_sha256'][:12]}` · shop `{meta['shop_commit'][:7]}` · replaykit "
              f"{meta['replaykit_version']} · seeds {', '.join(map(str, meta['seeds']))} · limits "
              f"{limits['turns']} turns, {limits['tool_calls']} tool calls, {limits['tokens']} tokens, "
              f"{limits['model_ms']} ms · prompt `{meta['prompt_sha256'][:12]}`")
    sections = [f"# App investigation {meta['suite_id']}", header, _decisions(s), _diagnosis(s), _remediation(s),
                _baseline_note(s), _effort(s), _incidents(report),
                "## Findings\n\n" + "\n".join(f"- {line}" for line in report["findings"])]
    return "\n\n".join(section for section in sections if section is not None) + "\n"
