"""Tests for pinned suites: load_suite and run_pinned (milestone 2)."""

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

from replaykit.suite import load_suite, run_pinned

SCENARIO = {
    "id": "scn-suite",
    "duration": 600,
    "services": ["api", "db"],
    "calls": {"api": ["db"]},
    "events": [],
}

CASE_DEPLOY = {"type": "bad_deploy", "service": "db", "start": 120}
CASE_CONFIG = {
    "type": "config_error",
    "service": "api",
    "start": 60,
    "noise": {"flapping_alerts": 2},
}
CASE_POOL = {
    "type": "pool_exhaustion",
    "service": "db",
    "start": 240,
    "cascade": True,
    "noise": {"decoy_deploy": True},
}


def write_json(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def suite_body(scenario_sha256, **overrides):
    suite = {
        "id": "suite-1",
        "scenario": "scenario.json",
        "scenario_sha256": scenario_sha256,
        "seeds": [1, 2],
        "step": 60,
        "agents": ["last_deploy", "threshold"],
        "cases": [dict(CASE_DEPLOY), dict(CASE_CONFIG), dict(CASE_POOL)],
    }
    suite.update(overrides)
    return suite


class LoadSuiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scenario_path = write_json(self.tmp.name, "scenario.json", SCENARIO)

    def write_suite(self, name="suite.json", **overrides):
        suite = suite_body(sha256(self.scenario_path), **overrides)
        return write_json(self.tmp.name, name, suite)

    def assertInvalid(self, suite):
        path = write_json(self.tmp.name, "suite.json", suite)
        with self.assertRaises(ValueError):
            load_suite(path)

    # --- happy path ------------------------------------------------------

    def test_returns_suite_scenario_and_hashes(self):
        path = self.write_suite()
        loaded = load_suite(path)
        self.assertEqual(
            set(loaded), {"suite", "scenario", "suite_sha256", "scenario_sha256"}
        )
        self.assertEqual(loaded["suite"], suite_body(sha256(self.scenario_path)))
        self.assertEqual(loaded["scenario"]["id"], "scn-suite")
        self.assertEqual(loaded["scenario"]["services"], ["api", "db"])
        self.assertEqual(loaded["suite_sha256"], sha256(path))
        self.assertEqual(loaded["scenario_sha256"], sha256(self.scenario_path))

    def test_scenario_path_is_relative_to_the_suite_file(self):
        nested = os.path.join(self.tmp.name, "nested")
        os.mkdir(nested)
        suite = suite_body(sha256(self.scenario_path), scenario="../scenario.json")
        path = write_json(nested, "suite.json", suite)
        self.assertEqual(load_suite(path)["scenario"]["id"], "scn-suite")

    # --- shape errors ----------------------------------------------------

    def test_rejects_missing_or_extra_keys(self):
        for key in ("id", "scenario", "scenario_sha256", "seeds", "step", "agents", "cases"):
            with self.subTest(key=key):
                suite = suite_body(sha256(self.scenario_path))
                del suite[key]
                self.assertInvalid(suite)
        extra = suite_body(sha256(self.scenario_path))
        extra["extra"] = 1
        self.assertInvalid(extra)

    def test_rejects_bad_id(self):
        for value in ("", 7, None):
            with self.subTest(value=value):
                self.assertInvalid(suite_body(sha256(self.scenario_path), id=value))

    def test_rejects_bad_scenario_path(self):
        for value in ("", 3, ["scenario.json"]):
            with self.subTest(value=value):
                self.assertInvalid(
                    suite_body(sha256(self.scenario_path), scenario=value)
                )

    def test_rejects_absolute_scenario_path(self):
        suite = suite_body(sha256(self.scenario_path), scenario=self.scenario_path)
        path = write_json(self.tmp.name, "suite.json", suite)
        with self.assertRaises(ValueError) as caught:
            load_suite(path)
        self.assertIn("relative", str(caught.exception))

    def test_rejects_bad_scenario_sha_format(self):
        for value in ("0" * 63, "A" * 64, "g" * 64, "", 5, "a" * 65):
            with self.subTest(value=value):
                self.assertInvalid(suite_body(value))

    def test_rejects_bad_seeds(self):
        for value in ([], "1", 1, [1, 1], [1.5], [True]):
            with self.subTest(value=value):
                self.assertInvalid(suite_body(sha256(self.scenario_path), seeds=value))

    def test_rejects_bad_step(self):
        for value in (0, -60, 60.0, True, "60"):
            with self.subTest(value=value):
                self.assertInvalid(suite_body(sha256(self.scenario_path), step=value))

    def test_rejects_bad_agents(self):
        for value in ([], "threshold", [1], ["oracle"], ["threshold", "threshold"]):
            with self.subTest(value=value):
                self.assertInvalid(suite_body(sha256(self.scenario_path), agents=value))

    def test_rejects_bad_cases(self):
        for value in (
            [],
            "cases",
            [{"type": "dns_outage", "service": "db", "start": 60}],
            [{"type": "bad_deploy", "service": "db", "start": 60, "cascade": True}],
        ):
            with self.subTest(value=value):
                self.assertInvalid(suite_body(sha256(self.scenario_path), cases=value))

    # --- file errors -----------------------------------------------------

    def test_rejects_missing_suite_file(self):
        with self.assertRaises(ValueError) as caught:
            load_suite(os.path.join(self.tmp.name, "nope.json"))
        self.assertIn("cannot read suite", str(caught.exception))

    def test_rejects_invalid_json(self):
        path = os.path.join(self.tmp.name, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(ValueError) as caught:
            load_suite(path)
        self.assertIn("invalid JSON", str(caught.exception))

    def test_rejects_missing_scenario_file(self):
        suite = suite_body(sha256(self.scenario_path), scenario="missing.json")
        path = write_json(self.tmp.name, "suite.json", suite)
        with self.assertRaises(ValueError) as caught:
            load_suite(path)
        self.assertIn("missing.json", str(caught.exception))

    def test_rejects_changed_scenario(self):
        path = write_json(
            self.tmp.name, "suite.json", suite_body("0" * 64)
        )
        with self.assertRaises(ValueError) as caught:
            load_suite(path)
        self.assertTrue(str(caught.exception).startswith("scenario changed"))

    def test_scenario_replaced_after_hashing_is_not_evaluated(self):
        path = self.write_suite()
        replacement = write_json(self.tmp.name, "b.json", dict(SCENARIO, id="scn-b"))
        real_open = open
        opened = []

        # Replace the file right after its first open, as a concurrent editor would.
        def open_then_replace(file, *args, **kwargs):
            handle = real_open(file, *args, **kwargs)
            if file == self.scenario_path and not opened:
                opened.append(file)
                os.replace(replacement, self.scenario_path)
            return handle

        expected_sha = sha256(self.scenario_path)
        with mock.patch("builtins.open", open_then_replace):
            loaded = load_suite(path)
        self.assertEqual(loaded["scenario"]["id"], "scn-suite")
        self.assertEqual(loaded["scenario_sha256"], expected_sha)


class RunPinnedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.scenario_path = write_json(cls.tmp.name, "scenario.json", SCENARIO)
        cls.suite_path = write_json(
            cls.tmp.name, "suite.json", suite_body(sha256(cls.scenario_path))
        )
        cls.report = run_pinned(cls.suite_path)

    def test_top_level_keys(self):
        self.assertEqual(
            set(self.report), {"agents", "incidents", "meta", "breakdown"}
        )
        self.assertEqual(list(self.report["agents"]), ["last_deploy", "threshold"])

    def test_meta(self):
        meta = self.report["meta"]
        self.assertEqual(
            set(meta),
            {
                "suite_id",
                "suite_sha256",
                "scenario_id",
                "scenario_sha256",
                "replaykit_version",
                "seeds",
                "step",
            },
        )
        self.assertEqual(meta["suite_id"], "suite-1")
        self.assertEqual(meta["suite_sha256"], sha256(self.suite_path))
        self.assertEqual(meta["scenario_id"], "scn-suite")
        self.assertEqual(meta["scenario_sha256"], sha256(self.scenario_path))
        self.assertEqual(meta["replaykit_version"], "0.2.0")
        self.assertEqual(meta["seeds"], [1, 2])
        self.assertEqual(meta["step"], 60)

    def test_incident_order_is_case_then_seed(self):
        self.assertEqual(
            [entry["id"] for entry in self.report["incidents"]],
            [
                "scn-suite:bad_deploy:db:1",
                "scn-suite:bad_deploy:db:2",
                "scn-suite:config_error:api:1",
                "scn-suite:config_error:api:2",
                "scn-suite:pool_exhaustion:db:1",
                "scn-suite:pool_exhaustion:db:2",
            ],
        )

    def test_truth_block(self):
        entries = self.report["incidents"]
        for entry in entries:
            self.assertEqual(
                set(entry["truth"]),
                {"service", "fault_type", "start", "cascade", "decoy_service"},
            )
        self.assertEqual(
            entries[0]["truth"],
            {
                "service": "db",
                "fault_type": "bad_deploy",
                "start": 120,
                "cascade": False,
                "decoy_service": None,
            },
        )
        self.assertEqual(
            entries[5]["truth"],
            {
                "service": "db",
                "fault_type": "pool_exhaustion",
                "start": 240,
                "cascade": True,
                "decoy_service": "api",
            },
        )

    def test_results_gain_noise_and_diagnosis_fields(self):
        for entry in self.report["incidents"]:
            self.assertEqual(list(entry["results"]), ["last_deploy", "threshold"])
            for result in entry["results"].values():
                self.assertEqual(
                    set(result),
                    {
                        "diagnosed",
                        "service_correct",
                        "type_correct",
                        "root_cause_correct",
                        "evidence_valid",
                        "evidence_precision",
                        "delay",
                        "points",
                        "error",
                        "decoy_blamed",
                        "noise_cited",
                        "diagnosed_service",
                        "diagnosed_type",
                    },
                )
                self.assertIsInstance(result["decoy_blamed"], bool)
                self.assertIsInstance(result["noise_cited"], int)

    def test_missing_diagnosis_is_none(self):
        result = self.report["incidents"][2]["results"]["last_deploy"]
        self.assertIsNone(result["diagnosed_service"])
        self.assertIsNone(result["diagnosed_type"])
        self.assertFalse(result["decoy_blamed"])
        self.assertEqual(result["noise_cited"], 0)

    def test_decoy_deploy_is_blamed(self):
        result = self.report["incidents"][4]["results"]["last_deploy"]
        self.assertEqual(result["diagnosed_service"], "api")
        self.assertTrue(result["decoy_blamed"])
        self.assertEqual(result["noise_cited"], 1)

    def test_breakdown_present_and_consistent(self):
        data = self.report["breakdown"]
        self.assertEqual(
            set(data),
            {"by_fault_type", "confusion", "decoy_blamed", "noise_cited"},
        )
        for agent in ("last_deploy", "threshold"):
            self.assertEqual(
                list(data["by_fault_type"][agent]),
                ["bad_deploy", "config_error", "pool_exhaustion"],
            )
            self.assertEqual(
                sum(
                    bucket["runs"]
                    for bucket in data["by_fault_type"][agent].values()
                ),
                6,
            )
            self.assertIn(agent, data["decoy_blamed"])
            self.assertIn(agent, data["noise_cited"])

    def test_report_round_trips_through_json(self):
        text = json.dumps(self.report, sort_keys=True)
        self.assertEqual(json.loads(text), self.report)

    def test_same_suite_file_gives_the_same_report(self):
        again = run_pinned(self.suite_path)
        self.assertEqual(
            json.dumps(again, sort_keys=True),
            json.dumps(self.report, sort_keys=True),
        )

    def test_topology_agent_receives_the_scenario_calls(self):
        suite = suite_body(
            sha256(self.scenario_path),
            seeds=[1],
            agents=["topology"],
            cases=[dict(CASE_DEPLOY)],
        )
        path = write_json(self.tmp.name, "topology-suite.json", suite)
        report = run_pinned(path)
        result = report["incidents"][0]["results"]["topology"]
        self.assertEqual(result["diagnosed_service"], "db")
        self.assertEqual(result["diagnosed_type"], "bad_deploy")


if __name__ == "__main__":
    unittest.main()
