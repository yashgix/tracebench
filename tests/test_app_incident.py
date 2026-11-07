"""Milestone 4: the shop app's leak case comes from running its real code."""

import json
import unittest
from pathlib import Path

from replaykit.app.incident import run_case

REPO = Path.home() / "Projects" / "deeptrace-shopapp"
GOOD, LEAK = "92b1aeb", "ee7027c"


def case(redeploy, seed=1):
    return {"id": "leak", "seed": seed, "baseline": GOOD, "duration_ms": 20 * 60_000,
            "deploys": [{"at_ms": 10 * 60_000, "service": "inventory", "commit": redeploy}]}


@unittest.skipUnless(REPO.exists(), "needs the shop app repository")
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
