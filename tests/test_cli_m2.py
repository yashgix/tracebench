"""Tests for the `suite` subcommand and `run --agents topology` (milestone 2)."""

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from replaykit.__main__ import main
from replaykit.report import render_markdown

SCENARIO = {
    "id": "scn-cli-m2",
    "duration": 600,
    "services": ["api", "db"],
    "calls": {"api": ["db"]},
    "events": [],
}

HEADINGS = (
    "# Suite cli-suite",
    "## Agents",
    "## Accuracy by fault type",
    "## Misdiagnoses",
    "## Decoys and noise",
)


def write_json(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def call_main(*args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


class SuiteCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scenario_path = write_json(self.tmp.name, "scenario.json", SCENARIO)
        self.suite = {
            "id": "cli-suite",
            "scenario": "scenario.json",
            "scenario_sha256": sha256(self.scenario_path),
            "seeds": [1, 2],
            "step": 60,
            "agents": ["last_deploy", "threshold", "topology"],
            "cases": [
                {
                    "type": "bad_deploy",
                    "service": "db",
                    "start": 120,
                    "noise": {"decoy_deploy": True, "flapping_alerts": 2},
                },
                {
                    "type": "pool_exhaustion",
                    "service": "db",
                    "start": 240,
                    "cascade": True,
                },
            ],
        }
        self.suite_path = write_json(self.tmp.name, "suite.json", self.suite)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_success_prints_markdown_and_writes_report(self):
        out = self.path("report.json")
        code, stdout, stderr = call_main("suite", self.suite_path, "--out", out)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        with open(out, encoding="utf-8") as handle:
            raw = handle.read()
        self.assertTrue(raw.startswith('{\n  "agents": {'))
        self.assertTrue(raw.endswith("\n"))
        self.assertFalse(raw.endswith("\n\n"))
        report = json.loads(raw)
        self.assertEqual(set(report), {"agents", "incidents", "meta", "breakdown"})
        self.assertEqual(report["meta"]["suite_id"], "cli-suite")
        self.assertEqual(report["meta"]["seeds"], [1, 2])
        self.assertEqual(stdout, render_markdown(report))
        for heading in HEADINGS:
            self.assertIn(heading, stdout)

    def test_md_file_matches_stdout(self):
        md = self.path("report.md")
        code, stdout, stderr = call_main("suite", self.suite_path, "--md", md)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        with open(md, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), stdout)

    def test_missing_suite_file_exits_2(self):
        missing = self.path("missing.json")
        code, stdout, stderr = call_main("suite", missing)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("error: "))
        self.assertIn("missing.json", stderr)

    def test_broken_suite_json_exits_2(self):
        path = self.path("broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        code, stdout, stderr = call_main("suite", path)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("invalid JSON", stderr)

    def test_changed_scenario_exits_2(self):
        suite = dict(self.suite, scenario_sha256="0" * 64)
        path = write_json(self.tmp.name, "changed.json", suite)
        code, stdout, stderr = call_main("suite", path)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("scenario changed", stderr)

    def test_unknown_suite_agent_exits_2(self):
        suite = dict(self.suite, agents=["oracle"])
        path = write_json(self.tmp.name, "bad-agents.json", suite)
        code, stdout, stderr = call_main("suite", path)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("suite agents", stderr)


class RunTopologyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scenario_path = write_json(self.tmp.name, "scenario.json", SCENARIO)
        self.faults_path = write_json(
            self.tmp.name,
            "faults.json",
            [
                {
                    "type": "pool_exhaustion",
                    "service": "db",
                    "start": 120,
                    "cascade": True,
                }
            ],
        )

    def test_topology_agent_receives_the_call_graph(self):
        out = os.path.join(self.tmp.name, "report.json")
        code, stdout, stderr = call_main(
            "run",
            "--scenario",
            self.scenario_path,
            "--faults",
            self.faults_path,
            "--seeds",
            "1",
            "--agents",
            "topology",
            "--out",
            out,
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        with open(out, encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(list(report["agents"]), ["topology"])
        summary = report["agents"]["topology"]
        self.assertEqual(summary["root_cause_accuracy"], 1.0)
        self.assertEqual(summary["mean_points"], 100.0)
        self.assertEqual(summary["brier"], 0.04)
        result = report["incidents"][0]["results"]["topology"]
        self.assertTrue(result["root_cause_correct"])
        self.assertTrue(result["evidence_valid"])
        row = next(
            line for line in stdout.splitlines() if line.startswith("| topology |")
        )
        self.assertEqual(row, "| topology | 1 | 1.0 | 100.0 | 0.04 |")


if __name__ == "__main__":
    unittest.main()
