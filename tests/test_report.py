"""Tests for the breakdown and Markdown report helpers (milestone 2)."""

import unittest

from replaykit.report import agents_table, breakdown, render_markdown


def result(correct, diagnosed_type, decoy_blamed=False, noise_cited=0):
    return {
        "root_cause_correct": correct,
        "diagnosed_type": diagnosed_type,
        "decoy_blamed": decoy_blamed,
        "noise_cited": noise_cited,
    }


def incident(incident_id, fault_type, results, service="db", start=120):
    return {
        "id": incident_id,
        "truth": {
            "service": service,
            "fault_type": fault_type,
            "start": start,
            "cascade": False,
            "decoy_service": None,
        },
        "results": results,
    }


def agent_summary(runs, accuracy, points, brier, diagnosed=None):
    return {
        "runs": runs,
        "diagnosed": runs if diagnosed is None else diagnosed,
        "root_cause_accuracy": accuracy,
        "mean_points": points,
        "mean_delay": None,
        "brier": brier,
        "errors": 0,
    }


def make_report():
    return {
        "meta": {
            "suite_id": "demo-v1",
            "suite_sha256": "a" * 64,
            "scenario_id": "scn",
            "scenario_sha256": "0123456789abcdef" * 4,
            "replaykit_version": "0.2.0",
            "seeds": [1, 2],
            "step": 60,
        },
        "agents": {
            "alpha": agent_summary(3, 0.6667, 63.33, 0.25),
            "beta": agent_summary(3, 0.3333, 30.0, 0.4123, diagnosed=2),
        },
        "incidents": [
            incident(
                "i1",
                "bad_deploy",
                {
                    "alpha": result(True, "bad_deploy"),
                    "beta": result(False, "config_error", True, 2),
                },
            ),
            incident(
                "i2",
                "bad_deploy",
                {
                    "alpha": result(True, "bad_deploy"),
                    "beta": result(False, None, noise_cited=1),
                },
            ),
            incident(
                "i3",
                "memory_leak",
                {
                    "alpha": result(False, None),
                    "beta": result(True, "memory_leak"),
                },
            ),
        ],
    }


class BreakdownTest(unittest.TestCase):
    def setUp(self):
        self.data = breakdown(make_report())

    def test_keys(self):
        self.assertEqual(
            set(self.data),
            {"by_fault_type", "confusion", "decoy_blamed", "noise_cited"},
        )

    def test_by_fault_type(self):
        self.assertEqual(
            self.data["by_fault_type"],
            {
                "alpha": {
                    "bad_deploy": {"runs": 2, "correct": 2},
                    "memory_leak": {"runs": 1, "correct": 0},
                },
                "beta": {
                    "bad_deploy": {"runs": 2, "correct": 0},
                    "memory_leak": {"runs": 1, "correct": 1},
                },
            },
        )

    def test_fault_types_follow_fault_types_order_and_skip_absent_ones(self):
        self.assertEqual(list(self.data["by_fault_type"]["alpha"]), ["bad_deploy", "memory_leak"])

    def test_confusion(self):
        self.assertEqual(
            self.data["confusion"],
            {
                "alpha": {
                    "bad_deploy": {"bad_deploy": 2},
                    "memory_leak": {"none": 1},
                },
                "beta": {
                    "bad_deploy": {"config_error": 1, "none": 1},
                    "memory_leak": {"memory_leak": 1},
                },
            },
        )

    def test_noise_totals(self):
        self.assertEqual(self.data["decoy_blamed"], {"alpha": 0, "beta": 1})
        self.assertEqual(self.data["noise_cited"], {"alpha": 0, "beta": 3})

    def test_agents_are_sorted_everywhere(self):
        for key in ("by_fault_type", "confusion", "decoy_blamed", "noise_cited"):
            with self.subTest(key=key):
                self.assertEqual(list(self.data[key]), ["alpha", "beta"])

    def test_missing_results_are_skipped(self):
        report = make_report()
        del report["incidents"][0]["results"]["beta"]
        data = breakdown(report)
        self.assertEqual(data["by_fault_type"]["beta"]["bad_deploy"], {"runs": 1, "correct": 0})
        self.assertEqual(data["decoy_blamed"]["beta"], 0)


class AgentsTableTest(unittest.TestCase):
    def test_renders_sorted_rows_with_dashes_for_none(self):
        report = {
            "agents": {
                "zeta": {
                    "runs": 2,
                    "root_cause_accuracy": 0.5,
                    "mean_points": 40.0,
                    "brier": None,
                },
                "alpha": {
                    "runs": 1,
                    "root_cause_accuracy": 1.0,
                    "mean_points": 95.0,
                    "brier": 0.04,
                },
            }
        }
        self.assertEqual(
            agents_table(report),
            "| agent | runs | root cause accuracy | mean points | brier |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| alpha | 1 | 1.0 | 95.0 | 0.04 |\n"
            "| zeta | 2 | 0.5 | 40.0 | - |",
        )


class RenderMarkdownTest(unittest.TestCase):
    def test_full_report(self):
        expected = (
            "\n\n".join(
                [
                    "# Suite demo-v1",
                    "suite `aaaaaaaaaaaa` \u00b7 scenario scn `0123456789ab` "
                    "\u00b7 replaykit 0.2.0 \u00b7 seeds 1, 2 \u00b7 step 60",
                    "## Agents\n\n"
                    "| agent | runs | root cause accuracy | mean points | brier |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    "| alpha | 3 | 0.6667 | 63.33 | 0.25 |\n"
                    "| beta | 3 | 0.3333 | 30.0 | 0.4123 |",
                    "## Accuracy by fault type\n\n"
                    "| agent | bad_deploy | memory_leak |\n"
                    "| --- | --- | --- |\n"
                    "| alpha | 2/2 | 0/1 |\n"
                    "| beta | 0/2 | 1/1 |",
                    "## Misdiagnoses\n\n"
                    "| agent | truth | diagnosed | runs |\n"
                    "| --- | --- | --- | --- |\n"
                    "| alpha | memory_leak | none | 1 |\n"
                    "| beta | bad_deploy | config_error | 1 |\n"
                    "| beta | bad_deploy | none | 1 |",
                    "## Decoys and noise\n\n"
                    "| agent | decoy blamed | noise cited |\n"
                    "| --- | --- | --- |\n"
                    "| alpha | 0 | 0 |\n"
                    "| beta | 1 | 3 |",
                ]
            )
            + "\n"
        )
        self.assertEqual(render_markdown(make_report()), expected)

    def test_section_order(self):
        text = render_markdown(make_report())
        headings = [
            "# Suite demo-v1",
            "## Agents",
            "## Accuracy by fault type",
            "## Misdiagnoses",
            "## Decoys and noise",
        ]
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))

    def test_ends_with_a_single_newline(self):
        text = render_markdown(make_report())
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_brier_none_prints_a_dash(self):
        report = make_report()
        report["agents"]["beta"]["brier"] = None
        row = next(line for line in render_markdown(report).splitlines() if line.startswith("| beta |"))
        self.assertTrue(row.endswith("| - |"))

    def test_no_misdiagnoses_prints_none(self):
        report = make_report()
        for entry in report["incidents"]:
            fault_type = entry["truth"]["fault_type"]
            for outcome in entry["results"].values():
                outcome["diagnosed_type"] = fault_type
        text = render_markdown(report)
        self.assertIn("## Misdiagnoses\n\nNone.\n\n## Decoys and noise", text)
        self.assertNotIn("| agent | truth | diagnosed | runs |", text)

    def test_ignores_a_stale_breakdown(self):
        report = make_report()
        report["breakdown"] = {
            "by_fault_type": {
                "alpha": {"bad_deploy": {"runs": 9, "correct": 9}},
                "beta": {"bad_deploy": {"runs": 9, "correct": 0}},
            },
            "confusion": {},
            "decoy_blamed": {"alpha": 0, "beta": 0},
            "noise_cited": {"alpha": 0, "beta": 0},
        }
        text = render_markdown(report)
        self.assertIn("| alpha | 2/2 | 0/1 |", text)
        self.assertIn("| beta | 0/2 | 1/1 |", text)
        self.assertIn("## Misdiagnoses\n\n| agent | truth | diagnosed | runs |", text)
        self.assertNotIn("| alpha | 9/9 | 0/0 |", text)


if __name__ == "__main__":
    unittest.main()
