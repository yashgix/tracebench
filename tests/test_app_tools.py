"""Milestone 4: the incident bundle and the investigator's code tools see only what existed at the page."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from app_helpers import FIX, REPO, leak_incident, needs_repo
from replaykit.app import cases
from replaykit.app.bundle import bundle
from replaykit.app.incident import run_case
from replaykit.app.tools import CodeEvidence

PROTECTED = Path(__file__).resolve().parents[1] / "replaykit" / "app" / "protected"


def call(evidence, tool, service="-", arg="-"):
    return evidence.run({"tool": tool, "service": service, "arg": arg})


@needs_repo
class BundleTest(unittest.TestCase):
    def setUp(self):
        self.incident = leak_incident()

    def test_window_ends_at_the_page(self):
        page = [e for e in self.incident.events if e["kind"] == "alert"]
        self.assertEqual([(e["t"], e["service"]) for e in page], [(self.incident.duration, "web")])
        self.assertTrue(all(e["t"] <= self.incident.duration for e in self.incident.events))
        self.assertTrue(all(s["end_ms"] <= self.incident.duration for s in self.incident.spans))
        ids = [e["id"] for e in self.incident.events]
        self.assertEqual(len(ids), len(set(ids)))

    def test_topology_comes_from_traces(self):
        self.assertEqual(self.incident.calls, {"web": ["inventory", "orders"], "orders": ["inventory"],
                                               "inventory": []})

    def test_same_case_same_bundle(self):
        run = cases.case("leak", 1)
        again = bundle(REPO, run, run_case(REPO, run))
        self.assertEqual(json.dumps(again.__dict__), json.dumps(self.incident.__dict__))

    def test_no_truth_fix_or_protected_check_in_the_bundle(self):
        text = json.dumps(self.incident.__dict__)
        truth = cases.CASES["leak"]["truth"]
        for secret in (truth["fault_type"], truth["function"], "protected", "check_inventory",
                       "test_many_conflicts_leave_the_pool_usable"):
            self.assertNotIn(secret, text)

    def test_a_deploy_after_the_page_is_out_of_reach(self):
        run = cases.case("leak", 1)
        raw = run_case(REPO, run)
        raw["deploys"].append({"time_ms": raw["page"]["time_ms"] + 1, "service": "web", "commit": cases.LEAK})
        incident = bundle(REPO, run, raw)
        self.assertEqual(incident.code["deployed"]["web"], cases.GOOD)
        self.assertEqual(json.dumps(incident.__dict__), json.dumps(self.incident.__dict__))

    def test_a_later_commit_is_out_of_reach(self):
        with tempfile.TemporaryDirectory() as folder:
            clone = Path(folder) / "shop"
            subprocess.run(["git", "clone", "-q", str(REPO), str(clone)], check=True)
            git = ["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                   "-c", "commit.gpgsign=false"]
            subprocess.run([*git, "apply", "-"], input=FIX, text=True, check=True)
            subprocess.run([*git, "commit", "-q", "-am", "inventory: release in finally"], check=True)
            later = subprocess.run([*git, "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                                   check=True).stdout.strip()
            run = cases.case("leak", 1)
            incident = bundle(clone, run, run_case(clone, run))
        self.assertNotIn(later, json.dumps(incident.__dict__))
        self.assertEqual(call(CodeEvidence(incident), "diff", arg=later)["error"], f"unknown commit {later!r}")


@needs_repo
class CodeToolsTest(unittest.TestCase):
    def setUp(self):
        self.evidence = CodeEvidence(leak_incident())

    def test_commits_follow_each_service_deployed_commit(self):
        inventory = call(self.evidence, "commits", "inventory")
        web = call(self.evidence, "commits", "web")
        self.assertEqual(inventory["deployed"], cases.LEAK)
        self.assertEqual(inventory["commits"][0][:4], [cases.LEAK, "2026-11-04T16:20:00-08:00", "Dev Patel",
                                                       "inventory: move the stock check into a helper"])
        self.assertEqual([row[0] for row in web["commits"]], [row[0] for row in inventory["commits"]][1:])
        self.assertIn(cases.LEAK, self.evidence.observed)

    def test_diff_shows_the_change_and_marks_its_files(self):
        result = call(self.evidence, "diff", arg=cases.LEAK)
        self.assertIn("-            finally:", result["diff"])
        self.assertIn("shop/inventory.py", self.evidence.observed)

    def test_source_reads_the_deployed_code_only(self):
        leaky = call(self.evidence, "source", "inventory", "shop/inventory.py:24-35")
        good = call(self.evidence, "source", "web", "shop/inventory.py:24-35")
        self.assertEqual((leaky["commit"], good["commit"]), (cases.LEAK, cases.GOOD))
        self.assertNotEqual(leaky["lines"], good["lines"])
        self.assertEqual(leaky["lines"][0][0], 24)
        for arg in ("../replaykit/app/cases.py", "/etc/passwd", "shop/inventory.py:9-3", "tests"):
            self.assertIn("error", call(self.evidence, "source", "inventory", arg), arg)

    def test_source_and_search_are_capped(self):
        self.assertLessEqual(len(call(self.evidence, "source", "inventory", "shop/inventory.py:1-500")["lines"]), 80)
        self.assertIn("error", call(self.evidence, "search", "inventory", "x"))
        hits = call(self.evidence, "search", "inventory", "self")
        self.assertEqual((len(hits["hits"]), hits["truncated"]), (20, True))

    def test_error_traces_show_the_whole_request(self):
        result = call(self.evidence, "traces", "web", "error")
        self.assertEqual(len(result["traces"]), 5)
        services = {row[2] for _, rows in result["traces"] for row in rows}
        self.assertEqual(services, {"web", "orders", "inventory"})
        self.assertTrue({row[0] for _, rows in result["traces"] for row in rows} <= self.evidence.observed)
        self.assertIn("error", call(self.evidence, "traces", "web", "all"))

    def test_telemetry_tools_take_one_arg(self):
        usage = call(self.evidence, "metrics", "inventory", "db.client.connections.usage")
        self.assertEqual(usage["points"][-1][2], 5)
        logs = call(self.evidence, "logs", "inventory", "error")
        self.assertTrue(logs["logs"][-1][4].startswith("database pool: no connection free"))
        self.assertIn("error", call(self.evidence, "metrics", "inventory", "error_rate"))


if __name__ == "__main__":
    unittest.main()
