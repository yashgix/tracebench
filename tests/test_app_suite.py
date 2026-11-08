"""SPEC-4 B6: an app suite end to end (investigate, validate, score), its replay, and the two rule baselines."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from app_helpers import REPO, RUNNER, calls, needs_repo
from test_app_cases import STEPS, TRUE, cite, incident, validate
from replaykit.__main__ import main
from replaykit.app import cases, world
from replaykit.app.baselines import _reverse, deepest_abnormal_commit, revert_last_deploy
from replaykit.app.patch import ROOT, _apply
from replaykit.app.suite import _case_of, investigate_app, load_app_suite, patches, score_app_report, validate_app
from replaykit.investigate import Reply
from replaykit.suite import RecordedModel

SUITE = ROOT / "suites" / "shop-app-v1.json"


def honest(incident_id, turn, packet):
    """A model that follows each case's scripted calls, then gives its true decision."""
    steps = STEPS[_case_of(incident_id)[0]]
    action = calls(*steps[turn - 1]) if turn <= len(steps) else TRUE[_case_of(incident_id)[0]](cite(packet))
    return Reply("ok", action, 1000, 100, 10, 50)


def suite_file(folder, **change):
    path = Path(folder) / "suite.json"
    path.write_text(json.dumps(json.loads(SUITE.read_text()) | change))
    return path


@needs_repo
class AppSuiteFileTest(unittest.TestCase):
    def test_the_pinned_suite_loads(self):
        suite = load_app_suite(SUITE, REPO)["suite"]
        self.assertEqual((len(suite["cases"]), suite["seeds"]), (6, [1, 2]))

    def test_bad_suites_are_refused(self):
        changes = {"no such commit": {"shop_commit": "0" * 40}, "short commit": {"shop_commit": "bfc0fd2"},
                   "unknown case": {"cases": ["leak", "nope"]}, "repeated seed": {"seeds": [1, 1]},
                   "missing limit": {"limits": {"turns": 15}}, "unknown key": {"extra": 1}}
        for name, change in changes.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    load_app_suite(suite_file(folder, **change), REPO)


@needs_repo
class AppPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.suite = suite_file(cls.folder.name, seeds=[1], cases=list(STEPS), investigators=["fake-model"])
        cls.loaded = load_app_suite(cls.suite, REPO)
        cls.report = investigate_app(cls.loaded, REPO, {"fake-model": honest})
        cls.validations = validate_app(cls.report, REPO, RUNNER)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_honest_answers_resolve_every_incident(self):
        totals = score_app_report(self.report, self.validations)["investigators"]["fake-model"]
        self.assertEqual((totals["resolved"], totals["patch_valid"], totals["patch_cases"]), (5, 3, 3))
        self.assertEqual((totals["escalated"], totals["dismissed"]), (1, 1))

    def test_a_stopped_model_is_counted_and_named(self):
        stopped = investigate_app(self.loaded, REPO, {"fake-model": lambda *_: Reply("stopped", None, 0, 0, 0, 0)})
        scored = score_app_report(stopped, {})
        self.assertEqual(scored["investigators"]["fake-model"]["model_stopped"], 5)
        self.assertIn("fake-model got no answer from its model in 5 incidents (lane stopped or error).",
                      scored["findings"])

    def test_the_investigation_holds_no_patch_result(self):
        self.assertFalse("validation" in json.dumps(self.report))
        self.assertEqual(len(patches(self.report)), 3)

    def test_a_replay_rebuilds_the_same_investigation(self):
        again = investigate_app(self.loaded, REPO, {"fake-model": RecordedModel(self.report, "fake-model")})
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.report, sort_keys=True))

    def test_a_changed_recording_is_refused(self):
        changed = copy.deepcopy(self.report)
        changed["incidents"][0]["results"]["fake-model"]["transcript"][1]["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match"):
            investigate_app(self.loaded, REPO, {"fake-model": RecordedModel(changed, "fake-model")})

    def test_validations_must_match_the_patches(self):
        some_id = patches(self.report)[0][0]
        one = self.validations[some_id]["fake-model"]

        def changed(edit):
            validations = copy.deepcopy(self.validations)
            edit(validations)
            return validations

        tries = {"missing": lambda v: v.pop(some_id),
                 "extra": lambda v: v.setdefault("shop-app:c5:1", {}).update({"fake-model": one}),
                 "verdict flipped": lambda v: v[some_id]["fake-model"].update(valid=not one["valid"]),
                 "wrong type": lambda v: v[some_id]["fake-model"].update(changed_lines="2"),
                 "unknown key": lambda v: v[some_id]["fake-model"].update(note=1),
                 "replay keys": lambda v: v[some_id]["fake-model"]["replay"].pop("page"),
                 "no checks ran": lambda v: v[some_id]["fake-model"].update(protected={"run": 0, "failed": []}),
                 "unknown check": lambda v: v[some_id]["fake-model"]["protected"].update(failed=["check_x.X.test"]),
                 "did not apply": lambda v: v[some_id]["fake-model"].update(applies=False, error="refused"),
                 "replay disagrees": lambda v: v[some_id]["fake-model"]["replay"].update(page=True,
                                                                                         pool_in_use_end=7),
                 "negative lines": lambda v: v[some_id]["fake-model"].update(changed_lines=-1),
                 "another patch": lambda v: v[some_id]["fake-model"].update(patch_sha256="0" * 64),
                 "other files": lambda v: v[some_id]["fake-model"].update(files=["shop/web.py"]),
                 "negative errors": lambda v: v[some_id]["fake-model"]["replay"].update(web_errors=-1)}
        for name, edit in tries.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                score_app_report(self.report, changed(edit))

    def test_the_investigation_must_cover_its_suite(self):
        def changed(edit):
            report = copy.deepcopy(self.report)
            edit(report)
            return report

        tries = {"truncated": lambda r: r["incidents"].pop(),
                 "repeated": lambda r: r["incidents"].append(r["incidents"][0]),
                 "unknown id": lambda r: r["incidents"][0].update(id="shop-app:c9:1"),
                 "missing investigator": lambda r: r["incidents"][0]["results"].pop("fake-model"),
                 "missing record key": lambda r: r["incidents"][0]["results"]["fake-model"].pop("observed"),
                 "decision not an object": lambda r: r["incidents"][0]["results"]["fake-model"].update(decision="x")}
        for name, edit in tries.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                score_app_report(changed(edit), self.validations)

    def test_the_commands_replay_and_score(self):
        folder = Path(self.folder.name)
        (folder / "inv.json").write_text(json.dumps(self.report, indent=2, sort_keys=True) + "\n")
        (folder / "val.json").write_text(json.dumps(self.validations))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["investigate-app", str(self.suite), "--repo", str(REPO), "--replay",
                                   str(folder / "inv.json"), "--out", str(folder / "again.json")]), 0)
            self.assertEqual(main(["score-app", str(folder / "again.json"), "--validations", str(folder / "val.json"),
                                   "--out", str(folder / "report.json"), "--md", str(folder / "report.md")]), 0)
        self.assertEqual((folder / "again.json").read_bytes(), (folder / "inv.json").read_bytes())
        self.assertIn("## Remediation", (folder / "report.md").read_text())

    def test_models_need_a_recording_or_the_broker(self):
        for extra in ([], ["--replay", "x.json", "--broker", "http://127.0.0.1:1"]):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["investigate-app", str(self.suite), "--repo", str(REPO), "--out",
                                       str(Path(self.folder.name) / "x.json"), *extra]), 2)


@needs_repo
class AppBaselineTest(unittest.TestCase):
    def test_what_each_rule_decides(self):
        revert = {"leak": ("patch", "inventory", cases.LEAK), "config": ("patch", "web", cases.PER_PRODUCT),
                  "decoy": ("patch", "web", cases.LOG_WORDING), "slow_query": ("patch", "inventory", cases.ANY_CASE),
                  "outside_lock": ("escalate", "-", "-"), "load_test": ("escalate", "-", "-")}
        deepest = {"leak": ("inventory", cases.LEAK), "config": ("web", cases.PER_PRODUCT),
                   "decoy": ("orders", cases.ORDER_CAP), "slow_query": ("inventory", cases.ANY_CASE),
                   "outside_lock": ("inventory", cases.LOWER_CASED), "load_test": ("web", cases.LOWER_CASED)}
        for name in cases.CASES:
            with self.subTest(name=name):
                said = revert_last_deploy(incident(name))
                self.assertEqual((said["act"], said["service"], said["commit"]), revert[name])
                said = deepest_abnormal_commit(incident(name))
                self.assertEqual((said["act"], said["service"], said["commit"]), ("diagnose", *deepest[name]))

    def test_a_revert_restores_the_parent(self):
        diff = incident("config").code["diffs"][cases.PER_PRODUCT]
        with tempfile.TemporaryDirectory() as folder:
            clone = Path(folder) / "repo"
            _apply(REPO, cases.PER_PRODUCT, _reverse(diff), clone)
            parent = dict(world.blobs(REPO, cases.LIMITER))[Path("shop/config.py")]
            self.assertEqual((clone / "shop/config.py").read_bytes(), parent)

    def test_reverts_are_judged_like_any_patch(self):
        self.assertTrue(validate("config", revert_last_deploy(incident("config"))["patch"])["valid"])
        failed = validate("slow_query", revert_last_deploy(incident("slow_query"))["patch"])["protected"]["failed"]
        self.assertEqual(failed, ["check_lookup.LookupChecks.test_any_case_finds_the_product"])
