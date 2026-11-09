import unittest

from replaykit.inject import inject
from replaykit.model import Diagnosis
from replaykit.replay import RunResult
from replaykit.score import noise_score

SCENARIO = {
    "id": "scn-noise",
    "duration": 600,
    "services": ["api", "db", "cache"],
    "calls": {"api": ["db"], "db": ["cache"]},
    "events": [],
}


def make_incident(noise=None, fault_type="config_error", service="db", start=120, seed=1):
    fault = {"type": fault_type, "service": service, "start": start}
    if noise is not None:
        fault["noise"] = noise
    return inject(SCENARIO, fault, seed)


def run_with(service="db", fault_type="config_error", evidence=()):
    return RunResult(
        diagnosis=Diagnosis(service, fault_type, 0.7, tuple(evidence)),
        diagnosed_at=240,
        calls=4,
    )


def injected_id(incident):
    return sorted(incident.truth.injected_ids)[0]


class NoiseScoreShapeTest(unittest.TestCase):
    def test_has_exact_keys(self):
        incident = make_incident()
        result = noise_score(incident, run_with())
        self.assertEqual(set(result), {"decoy_blamed", "noise_cited"})

    def test_no_diagnosis_scores_zero(self):
        incident = make_incident({"decoy_deploy": True, "flapping_alerts": 2})
        self.assertEqual(
            noise_score(incident, RunResult()),
            {"decoy_blamed": False, "noise_cited": 0},
        )


class DecoyTest(unittest.TestCase):
    def test_decoy_blamed(self):
        incident = make_incident({"decoy_deploy": True}, start=300)
        decoy = incident.truth.decoy_service
        self.assertIsNotNone(decoy)
        self.assertNotEqual(decoy, "db")
        result = noise_score(incident, run_with(service=decoy, fault_type="bad_deploy"))
        self.assertTrue(result["decoy_blamed"])

    def test_true_service_is_not_blamed(self):
        incident = make_incident({"decoy_deploy": True}, start=300)
        result = noise_score(incident, run_with(service="db"))
        self.assertFalse(result["decoy_blamed"])

    def test_without_decoy_nothing_is_blamed(self):
        incident = make_incident()
        self.assertIsNone(incident.truth.decoy_service)
        for service in ("db", "api", "cache"):
            with self.subTest(service=service):
                result = noise_score(incident, run_with(service=service))
                self.assertFalse(result["decoy_blamed"])


class NoiseCitedTest(unittest.TestCase):
    def test_counts_cited_noise_ids(self):
        incident = make_incident({"flapping_alerts": 4})
        noise_ids = sorted(incident.truth.noise_ids)
        self.assertEqual(len(noise_ids), 4)
        result = noise_score(incident, run_with(evidence=noise_ids[:3]))
        self.assertEqual(result["noise_cited"], 3)

    def test_decoy_deploy_id_is_noise(self):
        incident = make_incident({"decoy_deploy": True}, start=300)
        decoy = incident.truth.decoy_service
        decoy_id = next(
            event["id"] for event in incident.events if event["kind"] == "deploy" and event["service"] == decoy
        )
        self.assertIn(decoy_id, incident.truth.noise_ids)
        self.assertNotIn(decoy_id, incident.truth.injected_ids)
        self.assertEqual(noise_score(incident, run_with(evidence=(decoy_id,)))["noise_cited"], 1)

    def test_injected_and_unknown_ids_do_not_count(self):
        incident = make_incident({"flapping_alerts": 2})
        noise_id = sorted(incident.truth.noise_ids)[0]
        evidence = (injected_id(incident), noise_id, "ev-9999")
        result = noise_score(incident, run_with(evidence=evidence))
        self.assertEqual(result["noise_cited"], 1)

    def test_incident_without_noise_is_zero(self):
        incident = make_incident()
        self.assertEqual(incident.truth.noise_ids, frozenset())
        result = noise_score(incident, run_with(evidence=(injected_id(incident),)))
        self.assertEqual(result["noise_cited"], 0)

    def test_decoy_and_noise_can_both_trigger(self):
        incident = make_incident({"decoy_deploy": True, "flapping_alerts": 3}, start=300)
        noise_ids = sorted(incident.truth.noise_ids)
        run = run_with(
            service=incident.truth.decoy_service,
            fault_type="bad_deploy",
            evidence=noise_ids,
        )
        result = noise_score(incident, run)
        self.assertTrue(result["decoy_blamed"])
        self.assertEqual(result["noise_cited"], len(noise_ids))


if __name__ == "__main__":
    unittest.main()
