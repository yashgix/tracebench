"""One app incident end to end: run the app, bundle what monitoring saw, let a model investigate with v4 tools and
actions, validate any patch on the trusted side, and score the result against the case's truth."""

from ..investigate import Limits, investigate
from .actions import parse_action
from .bundle import bundle
from .cases import CASES, case
from .incident import run_case
from .patch import LocalRunner, validate_patch
from .tools import CodeEvidence

LIMITS = Limits(turns=15, tool_calls=25, tokens=200_000, model_ms=600_000)


def _names_function(said, truth):
    return said in (truth, truth.rsplit(".", 1)[-1])


def score_app(truth, inv, validation):
    """Score investigation *inv* against *truth*; *validation* is the verifier's patch result, if any."""
    decision = inv.decision
    act = decision["act"] if decision is not None else None
    names_cause = act in ("diagnose", "patch")
    cited = decision["evidence"] if decision is not None else []
    evidence_valid = decision is not None and all(i in inv.observed for i in cited) and (
        not names_cause or (bool(cited) and {decision["commit"], decision["file"]} <= inv.observed))
    root_cause = names_cause and all(decision[key] == truth.get(key) for key in ("service", "fault_type", "commit"))
    location = root_cause and decision["file"] == truth["file"] and (
        truth["function"] is None or _names_function(decision["function"], truth["function"]))
    patch_valid = validation is not None and validation["valid"]
    decision_correct = act == truth["expected"]
    resolved = decision_correct and (truth["expected"] not in ("diagnose", "patch") or (location and evidence_valid))
    return {
        "expected": truth["expected"], "decision": act, "outcome": inv.outcome, "decision_correct": decision_correct,
        "root_cause_correct": root_cause, "location_correct": location, "evidence_valid": evidence_valid,
        "patch_valid": patch_valid, "resolved": resolved and (truth["expected"] != "patch" or patch_valid),
        "turns": inv.turns, "tool_calls": inv.tool_calls, "tokens": inv.input_tokens + inv.output_tokens,
        "cost_microusd": inv.cost_microusd, "latency_ms": inv.latency_ms,
    }


def run_app_case(repo, name, seed, model, limits=LIMITS, runner=LocalRunner()):
    """Investigate case *name* at *seed* of the app in *repo* with *model* and return the scored result; a patch is
    validated through *runner*."""
    run = case(name, seed)
    incident = bundle(repo, run, run_case(repo, run))
    inv = investigate(incident, model, limits, CodeEvidence, parse_action)
    decision, validation = inv.decision, None
    if decision is not None and decision["act"] == "patch":
        validation = validate_patch(repo, run, decision, incident.code["deployed"][decision["service"]],
                                    CASES[name]["protected"], runner)
    return {"id": incident.id, **score_app(CASES[name]["truth"], inv, validation), "now": inv.now,
            "action": decision, "validation": validation, "transcript": inv.transcript}
