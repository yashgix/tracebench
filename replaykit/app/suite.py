"""An app suite (SPEC-4 B6) in three steps: investigate every incident, validate each patch on the trusted side, and
score the investigation with those validations."""

import dataclasses
import hashlib
import json
import re
import subprocess
from pathlib import Path

from .. import __version__
from ..investigate import Investigation, Limits, investigate
from ..model import _is_int
from ..suite import MODEL_NAME, _read, _validate_names
from .actions import parse_action
from .baselines import NAMES as BASELINES, baseline_investigation
from .bundle import bundle
from .cases import CASES, case
from .evaluate import score_app
from .incident import run_case
from .patch import replay_result, validate_patch
from .report import findings, summaries
from .tools import CodeEvidence

SUITE_KEYS = ("id", "shop_commit", "seeds", "limits", "investigators", "cases")
GIVEN = Path(__file__).resolve().parent / "investigator"
COMMIT = re.compile(r"[0-9a-f]{40}")
VALIDATION_KEYS = ("files", "changed_lines", "applies", "error", "own_tests", "protected", "replay", "valid")
REPLAY_KEYS = ("page", "pool_in_use_end", "web_errors", "clean")
RECORD_KEYS = ("decision", "outcome", "now", "turns", "tool_calls", "observed", "input_tokens", "output_tokens",
               "cost_microusd", "latency_ms", "transcript")


def _check_shape(suite):
    if not isinstance(suite, dict) or set(suite) != set(SUITE_KEYS):
        raise ValueError(f"suite must have exactly the keys {sorted(SUITE_KEYS)}")
    if not isinstance(suite["id"], str) or not suite["id"]:
        raise ValueError("suite id must be a non-empty string")
    if not isinstance(suite["shop_commit"], str) or not COMMIT.fullmatch(suite["shop_commit"]):
        raise ValueError("suite shop_commit must be 40 lowercase hex characters")
    seeds = suite["seeds"]
    if not isinstance(seeds, list) or not seeds or not all(map(_is_int, seeds)) or len(set(seeds)) != len(seeds):
        raise ValueError("suite seeds must be a non-empty list of distinct ints")
    fields = list(Limits.__dataclass_fields__)
    limits = suite["limits"]
    if not isinstance(limits, dict) or set(limits) != set(fields) or any(
            not _is_int(limits[k]) or limits[k] <= 0 for k in fields):
        raise ValueError(f"suite limits must be positive ints with exactly the keys {sorted(fields)}")
    _validate_names(suite["investigators"], "investigators",
                    lambda name: name in BASELINES or MODEL_NAME.fullmatch(name) is not None)
    names = suite["cases"]
    if not isinstance(names, list) or not names or any(n not in CASES for n in names) or len(set(names)) != len(names):
        raise ValueError(f"suite cases must be distinct names from {sorted(CASES)}")


def _git(repo, *args):
    return subprocess.run(["git", "--git-dir", str(Path(repo) / ".git"), *args], capture_output=True, text=True)


def _check_repo(repo, pin):
    """The shop repository must hold the pinned commit, and every case commit must be in its history."""
    if _git(repo, "rev-parse", "--verify", "--quiet", f"{pin}^{{commit}}").stdout.strip() != pin:
        raise ValueError(f"the shop repository has no commit {pin}")
    for spec in CASES.values():
        for commit in {spec["baseline"], *(d["commit"] for d in spec["deploys"])}:
            if _git(repo, "merge-base", "--is-ancestor", commit, pin).returncode:
                raise ValueError(f"case commit {commit} is not in the history of {pin}")


def load_app_suite(path, repo):
    """Read and check an app suite against the shop repository *repo*; return it with its hash."""
    raw = _read(path, "suite")
    try:
        suite = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from None
    _check_shape(suite)
    _check_repo(repo, suite["shop_commit"])
    return {"suite": suite, "suite_sha256": hashlib.sha256(raw).hexdigest()}


def _record(inv):
    fields = dataclasses.asdict(inv)
    return {key: sorted(fields[key]) if key == "observed" else fields[key] for key in RECORD_KEYS}


def _meta(loaded):
    suite = loaded["suite"]
    return {"suite_id": suite["id"], "suite_sha256": loaded["suite_sha256"], "shop_commit": suite["shop_commit"],
            "replaykit_version": __version__, "seeds": list(suite["seeds"]), "limits": dict(suite["limits"]),
            "investigators": {n: "baseline" if n in BASELINES else "model" for n in sorted(suite["investigators"])},
            "prompt_sha256": hashlib.sha256((GIVEN / "prompt.txt").read_bytes()).hexdigest(),
            "schema_sha256": hashlib.sha256((GIVEN / "action.schema.json").read_bytes()).hexdigest()}


def investigate_app(loaded, repo, models):
    """Run every investigator on every incident of *loaded*; models come from *models* by name."""
    meta = _meta(loaded)
    missing = [n for n, kind in meta["investigators"].items() if kind == "model" and n not in models]
    if missing:
        raise ValueError(f"no model given for {missing}")
    limits = Limits(**meta["limits"])
    incidents = []
    for name in loaded["suite"]["cases"]:
        for seed in meta["seeds"]:
            run = case(name, seed)
            incident = bundle(repo, run, run_case(repo, run))
            results = {n: _record(baseline_investigation(incident, n) if kind == "baseline" else
                                  investigate(incident, models[n], limits, CodeEvidence, parse_action))
                       for n, kind in meta["investigators"].items()}
            incidents.append({"id": incident.id, "deployed": incident.code["deployed"], "results": results})
    return {"meta": meta, "incidents": incidents}


def _case_of(incident_id):
    """(case name, seed) of an incident id such as `shop-app:c4:2`."""
    _, spec_id, seed = incident_id.split(":")
    return next(name for name, spec in CASES.items() if spec["id"] == spec_id), int(seed)


def patches(report):
    """(incident id, investigator, decision, deployed commits) for every patch decision in *report*."""
    return [(entry["id"], name, result["decision"], entry["deployed"]) for entry in report["incidents"]
            for name, result in sorted(entry["results"].items())
            if result["decision"] is not None and result["decision"]["act"] == "patch"]


def validate_app(report, repo, runner):
    """Validate every patch in *report* (the verifier's) through *runner*: {incident id: {investigator: result}}."""
    validations = {}
    for incident_id, name, decision, deployed in patches(report):
        case_name, seed = _case_of(incident_id)
        validations.setdefault(incident_id, {})[name] = validate_patch(
            repo, case(case_name, seed), decision, deployed[decision["service"]], CASES[case_name]["protected"],
            runner)
    return validations


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _protected_shaped(p):
    return p is None or (isinstance(p, dict) and set(p) == {"run", "failed"} and _is_int(p["run"])
                         and _strings(p["failed"]))


def _replay_shaped(r):
    return r is None or (isinstance(r, dict) and set(r) == set(REPLAY_KEYS) and replay_result(r) is r)


def _check_validation(v):
    """Raise unless *v* is a complete `validate_patch` result whose `valid` agrees with its parts."""
    shaped = (isinstance(v, dict) and set(v) == set(VALIDATION_KEYS) and _strings(v["files"])
              and _is_int(v["changed_lines"]) and isinstance(v["applies"], bool) and isinstance(v["valid"], bool)
              and (v["error"] is None or isinstance(v["error"], str))
              and v["own_tests"] in (None, "pass", "fail", "timeout")
              and _protected_shaped(v["protected"]) and _replay_shaped(v["replay"]))
    if not shaped:
        raise ValueError("a validation does not have the shape of a patch validation")
    passed = (v["own_tests"] == "pass" and v["protected"] is not None and not v["protected"]["failed"]
              and v["replay"] is not None and v["replay"]["clean"])
    if v["valid"] != passed:
        raise ValueError("a validation's verdict does not match its runs")


def _check_validations(report, validations):
    expected = {(incident_id, name) for incident_id, name, _, _ in patches(report)}
    if not isinstance(validations, dict) or not all(isinstance(v, dict) for v in validations.values()):
        raise ValueError("validations must map incident ids to investigators")
    given = {(incident_id, name) for incident_id, by_name in validations.items() for name in by_name}
    if given != expected:
        raise ValueError(f"validations must cover exactly the {len(expected)} patch decisions")
    for by_name in validations.values():
        for v in by_name.values():
            _check_validation(v)


def score_app_report(report, validations):
    """Score investigation *report* with *validations*: per incident and per investigator, against the truth."""
    _check_validations(report, validations)
    incidents = []
    for entry in report["incidents"]:
        case_name, _ = _case_of(entry["id"])
        truth = CASES[case_name]["truth"]
        results = {}
        for name, record in entry["results"].items():
            fields = {k: frozenset(v) if k == "observed" else v for k, v in record.items()}
            validation = validations.get(entry["id"], {}).get(name)
            results[name] = {**score_app(truth, Investigation(**fields), validation), "action": record["decision"],
                             "validation": validation}
        incidents.append({"id": entry["id"], "expected": truth["expected"], "results": results})
    scored = {"meta": report["meta"], "incidents": incidents}
    scored["investigators"] = summaries(scored)
    scored["findings"] = findings(scored["investigators"])
    return scored
