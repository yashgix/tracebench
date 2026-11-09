import unittest

from replaykit.inject import inject
from replaykit.model import Diagnosis
from replaykit.replay import RunResult
from replaykit.score import brier, score

SCENARIO = {
    "id": "scn-1",
    "duration": 600,
    "services": ["api", "db"],
    "calls": {"api": ["db"]},
    "events": [],
}


def make_incident(duration=600, start=120, fault_type="bad_deploy", service="db", seed=1):
    return inject(
        dict(SCENARIO, duration=duration),
        {"type": fault_type, "service": service, "start": start},
        seed,
    )


def make_run(incident, service="db", fault_type="bad_deploy", confidence=0.8, at=240, evidence=()):
    if at is None:
        return RunResult()
    diagnosis = Diagnosis(service, fault_type, confidence, tuple(evidence))
    return RunResult(diagnosis=diagnosis, diagnosed_at=at, calls=at // 60)


def event_ids(incident, injected=None, before=None):
    return [
        event["id"]
        for event in incident.events
        if (injected is None or (event["id"] in incident.truth.injected_ids) == injected)
        and (before is None or event["t"] <= before)
    ]


def injected_id_at(incident, t):
    for event in incident.events:
        if event["t"] == t and event["id"] in incident.truth.injected_ids:
            return event["id"]
    raise AssertionError(f"no injected event at t={t}")


class ShapeTest(unittest.TestCase):
    def test_has_exact_keys(self):
        incident = make_incident()
        result = score(incident, make_run(incident, evidence=event_ids(incident, True, 240)))
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
            },
        )

    def test_undiagnosed_run(self):
        incident = make_incident()
        result = score(incident, make_run(incident, at=None))
        self.assertFalse(result["diagnosed"])
        self.assertFalse(result["service_correct"])
        self.assertFalse(result["type_correct"])
        self.assertFalse(result["root_cause_correct"])
        self.assertFalse(result["evidence_valid"])
        self.assertEqual(result["evidence_precision"], 0.0)
        self.assertIsNone(result["delay"])
        self.assertEqual(result["points"], 0.0)


class PerfectTest(unittest.TestCase):
    def setUp(self):
        self.incident = make_incident()
        self.run = make_run(self.incident, at=240, evidence=event_ids(self.incident, injected=True, before=240))
        self.result = score(self.incident, self.run)

    def test_flags(self):
        self.assertTrue(self.result["diagnosed"])
        self.assertTrue(self.result["service_correct"])
        self.assertTrue(self.result["type_correct"])
        self.assertTrue(self.result["root_cause_correct"])
        self.assertTrue(self.result["evidence_valid"])

    def test_precision_and_delay(self):
        self.assertEqual(self.result["evidence_precision"], 1.0)
        self.assertEqual(self.result["delay"], 120)

    def test_points_include_timeliness(self):
        self.assertEqual(self.result["points"], 95.0)


class PointsTest(unittest.TestCase):
    def setUp(self):
        self.incident = make_incident()
        self.evidence = event_ids(self.incident, injected=True, before=240)[:1]

    def test_wrong_service(self):
        result = score(self.incident, make_run(self.incident, service="api", evidence=self.evidence))
        self.assertFalse(result["service_correct"])
        self.assertTrue(result["type_correct"])
        self.assertFalse(result["root_cause_correct"])
        self.assertEqual(result["points"], 30.0)

    def test_wrong_type(self):
        result = score(self.incident, make_run(self.incident, fault_type="config_error", evidence=self.evidence))
        self.assertEqual(result["points"], 50.0)

    def test_negative_delay_scores_zero_timeliness(self):
        early = event_ids(self.incident, before=60)[0]
        result = score(self.incident, make_run(self.incident, at=60, evidence=(early,)))
        self.assertEqual(result["delay"], -60)
        self.assertTrue(result["evidence_valid"])
        self.assertEqual(result["points"], 80.0)

    def test_points_round_to_two_places(self):
        result = score(self.incident, make_run(self.incident, at=130, evidence=self.evidence))
        self.assertEqual(result["points"], 99.58)


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.incident = make_incident()

    def test_no_evidence_is_invalid(self):
        result = score(self.incident, make_run(self.incident, at=240))
        self.assertFalse(result["evidence_valid"])
        self.assertEqual(result["evidence_precision"], 0.0)
        self.assertEqual(result["delay"], 120)
        self.assertEqual(result["points"], 0.0)

    def test_future_evidence_is_invalid(self):
        future = injected_id_at(self.incident, 300)
        result = score(self.incident, make_run(self.incident, at=240, evidence=(future,)))
        self.assertFalse(result["evidence_valid"])
        self.assertEqual(result["evidence_precision"], 1.0)
        self.assertEqual(result["points"], 0.0)

    def test_unknown_id_is_invalid(self):
        result = score(self.incident, make_run(self.incident, at=240, evidence=("ev-9999",)))
        self.assertFalse(result["evidence_valid"])
        self.assertEqual(result["evidence_precision"], 0.0)
        self.assertEqual(result["points"], 0.0)

    def test_precision_rounds_to_four_places(self):
        good = event_ids(self.incident, injected=True, before=240)[:1]
        bad = event_ids(self.incident, injected=False, before=240)[:2]
        result = score(self.incident, make_run(self.incident, at=240, evidence=good + bad))
        self.assertTrue(result["evidence_valid"])
        self.assertEqual(result["evidence_precision"], 0.3333)
        self.assertEqual(result["points"], 95.0)


class BrierTest(unittest.TestCase):
    def test_empty_is_none(self):
        self.assertIsNone(brier([]))

    def test_perfect_predictions(self):
        self.assertEqual(brier([(1.0, True)]), 0.0)
        self.assertEqual(brier([(0.0, False)]), 0.0)
        self.assertEqual(brier([(0.0, True), (1.0, False)]), 1.0)

    def test_mean_of_squared_errors(self):
        self.assertEqual(brier([(0.6, True), (0.6, False)]), 0.26)

    def test_rounds_to_four_places(self):
        pairs = [(0.9, True), (0.9, True), (0.9, False)]
        self.assertEqual(brier(pairs), 0.2767)


if __name__ == "__main__":
    unittest.main()
