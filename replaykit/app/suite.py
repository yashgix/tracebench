"""An app suite (SPEC-4 B6) in three steps: investigate every incident, validate each patch on the trusted side, and
score the investigation with those validations."""

import dataclasses
import hashlib
import json
import re
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
from .patch import GIT_ENV, REPLAY_KEYS, RESULT_KEYS, ROOT, _run, check_ids, check_patch, protected_result, \
    replay_result, validate_patch, verdict
from .report import findings, summaries
from .tools import CodeEvidence

SUITE_KEYS = ("id", "shop_commit", "seeds", "limits", "investigators", "cases")
GIVEN = Path(__file__).resolve().parent / "investigator"
COMMIT = re.compile(r"[0-9a-f]{40}")
CASE_IDS = {spec["id"]: name for name, spec in CASES.items()}
VALIDATION_KEYS = (*RESULT_KEYS, "patch_sha256")
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
    done = _run(["git", "--git-dir", str(Path(repo) / ".git"), *args], repo, GIT_ENV)
    if done is None:
        raise ValueError("git timed out on the shop repository")
    return done


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
            "replaykit_version": __version__, "cases": list(suite["cases"]), "seeds": list(suite["seeds"]),
            "limits": dict(suite["limits"]),
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
    for name in meta["cases"]:
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
    parts = incident_id.split(":") if isinstance(incident_id, str) else []
    if len(parts) != 3 or parts[0] != "shop-app" or parts[1] not in CASE_IDS or not parts[2].isdigit():
        raise ValueError(f"unknown incident id {incident_id!r}")
    return CASE_IDS[parts[1]], int(parts[2])


def _patch_sha256(decision):
    return hashlib.sha256(decision["patch"].encode()).hexdigest()


def patches(report):
    """(incident id, investigator, decision, deployed commits) for every patch decision in *report*."""
    return [(entry["id"], name, result["decision"], entry["deployed"]) for entry in report["incidents"]
            for name, result in sorted(entry["results"].items())
            if result["decision"] is not None and result["decision"]["act"] == "patch"]


def validate_app(report, repo, runner):
    """Validate every patch in *report* (the verifier's) through *runner*: {incident id: {investigator: result}},
    each result tied to its patch by hash."""
    validations = {}
    for incident_id, name, decision, deployed in patches(report):
        case_name, seed = _case_of(incident_id)
        result = validate_patch(repo, case(case_name, seed), decision, deployed[decision["service"]],
                                CASES[case_name]["protected"], runner)
        validations.setdefault(incident_id, {})[name] = {**result, "patch_sha256": _patch_sha256(decision)}
    return validations


def _check_report(report):
    """Raise unless *report* is an investigation of exactly its suite's cases × seeds, every investigator in each."""
    meta = report.get("meta") if isinstance(report, dict) else None
    if not isinstance(meta, dict) or not isinstance(report.get("incidents"), list):
        raise ValueError("an investigation must have meta and incidents")
    ids = [f"shop-app:{CASES[name]['id']}:{seed}" for name in meta["cases"] for seed in meta["seeds"]]
    if [entry.get("id") if isinstance(entry, dict) else None for entry in report["incidents"]] != ids:
        raise ValueError(f"an investigation must hold exactly the incidents {ids}")
    for entry in report["incidents"]:
        results = entry.get("results")
        if not isinstance(results, dict) or set(results) != set(meta["investigators"]) or not all(
                map(_complete, results.values())):
            raise ValueError(f"incident {entry['id']} must hold one complete record per investigator")


def _complete(record):
    return (isinstance(record, dict) and set(record) == set(RECORD_KEYS)
            and (record["decision"] is None or isinstance(record["decision"], dict))
            and isinstance(record["outcome"], str) and isinstance(record["observed"], list)
            and all(isinstance(i, str) for i in record["observed"]) and isinstance(record["transcript"], list)
            and _ints(*(record[k] for k in RECORD_KEYS if k not in ("decision", "outcome", "observed", "transcript"))))


def _ints(*values):
    return all(_is_int(v) and v >= 0 for v in values)


def _replay_shaped(r):
    if not (isinstance(r, dict) and set(r) == set(REPLAY_KEYS) and replay_result(r) == r):
        return False
    if r["page"] is None:  # the form of a replay that gave no result
        return True
    end = r["pool_in_use_end"]
    return _ints(r["web_errors"]) and (end is None or _ints(end)) and r["clean"] == (not r["page"] and end == 0)


def _shaped(v, ids):
    """Whether *v* has the types of a `validate_patch` result for the checks *ids*, and its parts agree."""
    if not (isinstance(v, dict) and set(v) == set(VALIDATION_KEYS) and isinstance(v["files"], list)
            and all(isinstance(f, str) for f in v["files"]) and _ints(v["changed_lines"])
            and isinstance(v["applies"], bool) and isinstance(v["valid"], bool)
            and (v["error"] is None or isinstance(v["error"], str))
            and v["own_tests"] in (None, "pass", "fail", "timeout")):
        return False
    p, r = v["protected"], v["replay"]
    protected = p is None or (isinstance(p, dict) and set(p) == {"run", "failed"} and _ints(p["run"])
                              and isinstance(p["failed"], list) and protected_result(p, ids) == p)
    replay = r is None or _replay_shaped(r)
    runs = (v["own_tests"], p, r)
    ran = None not in runs if v["applies"] else runs == (None, None, None)
    return protected and replay and ran


def _measured(decision):
    """[files, changed lines] as `validate_patch` records them for *decision*'s patch."""
    try:
        return list(check_patch(decision["patch"]))
    except ValueError:
        return [[], 0]


def _check_validation(v, decision, case_name):
    """Raise unless *v* is a complete validation of *decision*'s patch whose verdict agrees with its runs."""
    if not _shaped(v, check_ids(ROOT, CASES[case_name]["protected"])):
        raise ValueError("a validation does not have the shape of a patch validation")
    if v["patch_sha256"] != _patch_sha256(decision) or [v["files"], v["changed_lines"]] != _measured(decision):
        raise ValueError("a validation is not of its decision's patch")
    if v["valid"] != (verdict(v) and v["applies"] and v["error"] is None):
        raise ValueError("a validation's verdict does not match its runs")


def _check_validations(report, validations):
    if not isinstance(validations, dict) or not all(isinstance(v, dict) for v in validations.values()):
        raise ValueError("validations must map incident ids to investigators")
    expected = patches(report)
    given = {(incident_id, name) for incident_id, by_name in validations.items() for name in by_name}
    if given != {(incident_id, name) for incident_id, name, _, _ in expected}:
        raise ValueError(f"validations must cover exactly the {len(expected)} patch decisions")
    for incident_id, name, decision, _ in expected:
        _check_validation(validations[incident_id][name], decision, _case_of(incident_id)[0])


def score_app_report(report, validations):
    """Score investigation *report* with *validations*: per incident and per investigator, against the truth."""
    _check_report(report)
    _check_validations(report, validations)
    incidents = []
    for entry in report["incidents"]:
        truth = CASES[_case_of(entry["id"])[0]]["truth"]
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
