"""Milestone 4: the shop app's leak case comes from running its real code."""

import json
import unittest

from app_helpers import REPO, needs_repo
from replaykit.app.cases import GOOD, LEAK
from replaykit.app.cases import case as leak_case
from replaykit.app.incident import run_case


def case(redeploy, seed=1):
    run = leak_case("leak", seed)
    return run | {"deploys": [d | {"commit": redeploy} for d in run["deploys"]]}


@needs_repo
class LeakCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.leak = run_case(REPO, case(LEAK))

    def test_the_leak_pages_after_the_deploy(self):
        page = self.leak["page"]
        self.assertEqual((page["service"], page["rule"]), ("web", "error_rate_high"))
        self.assertGreater(page["time_ms"], 10 * 60_000)
        timeouts = [log for log in self.leak["logs"] if log["service"] == "inventory" and "pool" in log["body"]]
        self.assertTrue(timeouts)
        self.assertGreater(timeouts[0]["time_ms"], 10 * 60_000)

    def test_the_pool_fills_up(self):
        usage = [g["value"] for g in self.leak["gauges"] if g["name"] == "db.client.connections.usage"]
        self.assertEqual(max(usage), 5)

    def test_same_case_same_bytes(self):
        self.assertEqual(json.dumps(run_case(REPO, case(LEAK))), json.dumps(self.leak))

    def test_reverting_the_defect_removes_the_page(self):
        self.assertIsNone(run_case(REPO, case(GOOD))["page"])

    def test_another_seed_also_pages(self):
        self.assertIsNotNone(run_case(REPO, case(LEAK, seed=2))["page"])


if __name__ == "__main__":
    unittest.main()
