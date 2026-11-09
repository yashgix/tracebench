import unittest

from replaykit.inject import inject
from replaykit.model import Diagnosis
from replaykit.suite import run_suite

SCENARIO = {
    "id": "scn-suite",
    "duration": 600,
    "services": ["api", "db"],
    "calls": {"api": ["db"]},
    "events": [],
}


def make_incident(duration=600, start=120, fault_type="bad_deploy", service="db", seed=1):
    scenario = dict(SCENARIO, duration=duration)
    fault = {"type": fault_type, "service": service, "start": start}
    return inject(scenario, fault, seed)


def injected_id_at(incident, t):
    for event in incident.events:
        if event["t"] == t and event["id"] in incident.truth.injected_ids:
            return event["id"]
    raise AssertionError(f"no injected event at t={t}")


class NoDiagnosisAgent:
    def observe(self, events, now):
        return None


class FixedAgent:
    def __init__(self, diagnosis, at):
        self.diagnosis = diagnosis
        self.at = at

    def observe(self, events, now):
        if now >= self.at:
            return self.diagnosis
        return None


class SecondCallAgent:
    def __init__(self):
        self.calls = 0

    def observe(self, events, now):
        self.calls += 1
        if self.calls == 2:
            return Diagnosis("db", "bad_deploy", 0.5)
        return None


class RaisingAgent:
    def observe(self, events, now):
        raise RuntimeError("boom")


class ShapeTest(unittest.TestCase):
    def test_report_shape_and_sorted_names(self):
        incident = make_incident()
        report = run_suite(
            [incident],
            {"zeta": lambda: NoDiagnosisAgent(), "alpha": lambda: NoDiagnosisAgent()},
        )
        self.assertEqual(set(report), {"agents", "incidents"})
        self.assertEqual(list(report["agents"]), ["alpha", "zeta"])
        self.assertEqual([entry["id"] for entry in report["incidents"]], [incident.id])
        results = report["incidents"][0]["results"]
        self.assertEqual(list(results), ["alpha", "zeta"])
        expected = {
            "diagnosed",
            "service_correct",
            "type_correct",
            "root_cause_correct",
            "evidence_valid",
            "evidence_precision",
            "delay",
            "points",
            "error",
        }
        for name in ("alpha", "zeta"):
            self.assertEqual(set(results[name]), expected)
            self.assertIsNone(results[name]["error"])
        summary = report["agents"]["alpha"]
        self.assertEqual(
            set(summary),
            {
                "runs",
                "diagnosed",
                "root_cause_accuracy",
                "mean_points",
                "mean_delay",
                "brier",
                "errors",
            },
        )
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["diagnosed"], 0)
        self.assertEqual(summary["root_cause_accuracy"], 0.0)
        self.assertEqual(summary["mean_points"], 0.0)
        self.assertIsNone(summary["mean_delay"])
        self.assertIsNone(summary["brier"])
        self.assertEqual(summary["errors"], 0)

    def test_incidents_keep_given_order(self):
        first = make_incident(seed=1)
        second = make_incident(seed=2)
        report = run_suite([second, first], {"only": lambda: NoDiagnosisAgent()})
        self.assertEqual([entry["id"] for entry in report["incidents"]], [second.id, first.id])


class FactoryTest(unittest.TestCase):
    def test_each_incident_gets_a_fresh_agent(self):
        created = []

        def factory():
            agent = SecondCallAgent()
            created.append(agent)
            return agent

        report = run_suite([make_incident(seed=1), make_incident(seed=2)], {"second": factory})
        self.assertEqual(len(created), 2)
        self.assertIsNot(created[0], created[1])
        self.assertEqual([agent.calls for agent in created], [2, 2])
        self.assertEqual(report["agents"]["second"]["diagnosed"], 2)


class AggregateTest(unittest.TestCase):
    def test_scores_are_aggregated_per_agent(self):
        incident = make_incident()
        evidence = injected_id_at(incident, 240)
        report = run_suite(
            [incident],
            {
                "perfect": lambda: FixedAgent(Diagnosis("db", "bad_deploy", 0.8, (evidence,)), 240),
                "wrong": lambda: FixedAgent(Diagnosis("api", "config_error", 0.5, (evidence,)), 240),
                "silent": lambda: NoDiagnosisAgent(),
            },
        )
        agents = report["agents"]
        self.assertEqual(
            agents["perfect"],
            {
                "runs": 1,
                "diagnosed": 1,
                "root_cause_accuracy": 1.0,
                "mean_points": 95.0,
                "mean_delay": 120.0,
                "brier": 0.04,
                "errors": 0,
            },
        )
        self.assertEqual(
            agents["wrong"],
            {
                "runs": 1,
                "diagnosed": 1,
                "root_cause_accuracy": 0.0,
                "mean_points": 0.0,
                "mean_delay": None,
                "brier": 0.25,
                "errors": 0,
            },
        )
        self.assertEqual(
            agents["silent"],
            {
                "runs": 1,
                "diagnosed": 0,
                "root_cause_accuracy": 0.0,
                "mean_points": 0.0,
                "mean_delay": None,
                "brier": None,
                "errors": 0,
            },
        )

    def test_mean_delay_averages_correct_runs(self):
        incidents = [
            make_incident(start=120, seed=1),
            make_incident(start=60, seed=2),
        ]
        report = run_suite(
            incidents,
            {"fixed": lambda: FixedAgent(Diagnosis("db", "bad_deploy", 0.9), 240)},
        )
        summary = report["agents"]["fixed"]
        self.assertEqual(summary["diagnosed"], 2)
        self.assertEqual(summary["root_cause_accuracy"], 1.0)
        self.assertEqual(summary["mean_delay"], 150.0)
        self.assertEqual(summary["mean_points"], 0.0)
        self.assertEqual(summary["brier"], 0.01)


class ErrorTest(unittest.TestCase):
    def test_agent_errors_are_counted_and_reported(self):
        incident = make_incident()
        report = run_suite([incident], {"boom": lambda: RaisingAgent()})
        result = report["incidents"][0]["results"]["boom"]
        self.assertEqual(result["error"], "RuntimeError: boom")
        self.assertFalse(result["diagnosed"])
        summary = report["agents"]["boom"]
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["diagnosed"], 0)
        self.assertEqual(summary["root_cause_accuracy"], 0.0)
        self.assertEqual(summary["errors"], 1)
        self.assertIsNone(summary["brier"])


if __name__ == "__main__":
    unittest.main()
