import json
import unittest

from replaykit.inject import inject
from replaykit.model import Diagnosis
from replaykit.replay import RunResult, replay


def make_scenario(duration=600):
    return {
        "id": "scn-1",
        "duration": duration,
        "services": ["api", "db"],
        "calls": {"api": ["db"]},
        "events": [
            {
                "id": "ev-bg",
                "t": 0,
                "kind": "log",
                "service": "api",
                "attrs": {"level": "info", "message": "startup"},
            }
        ],
    }


def make_incident(duration=600, start=120, fault_type="bad_deploy", service="db", seed=1):
    fault = {"type": fault_type, "service": service, "start": start}
    return inject(make_scenario(duration), fault, seed)


class Recorder:
    """Agent that records its calls, optionally raising or diagnosing."""

    def __init__(self, diagnose_at=None, action=None):
        self.calls = []
        self.diagnose_at = diagnose_at
        self.action = action
        self.diagnosis = Diagnosis("db", "bad_deploy", 0.9, ("ev-0001",))

    def observe(self, events, now):
        self.calls.append((now, events))
        if self.action == "raise":
            raise RuntimeError("boom")
        if self.action == "invalid":
            return {"service": "db"}
        if self.diagnose_at is not None and now >= self.diagnose_at:
            return self.diagnosis
        return None


class StepTest(unittest.TestCase):
    def test_rejects_bad_step(self):
        for step in (0, -60, 60.0, True, "60"):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    replay(make_incident(), Recorder(), step)

    def test_calls_on_multiples_of_step(self):
        agent = Recorder()
        result = replay(make_incident(duration=600), agent, 60)
        self.assertEqual([now for now, _ in agent.calls], list(range(60, 601, 60)))
        self.assertEqual(result.calls, 10)

    def test_default_step_is_sixty(self):
        agent = Recorder()
        replay(make_incident(duration=180), agent)
        self.assertEqual([now for now, _ in agent.calls], [60, 120, 180])

    def test_final_call_lands_on_duration(self):
        agent = Recorder()
        result = replay(make_incident(duration=90, start=30), agent, 60)
        self.assertEqual([now for now, _ in agent.calls], [60, 90])
        self.assertEqual(result.calls, 2)

    def test_step_larger_than_duration(self):
        agent = Recorder()
        replay(make_incident(duration=100, start=30), agent, 60)
        self.assertEqual([now for now, _ in agent.calls], [60, 100])


class WindowTest(unittest.TestCase):
    def test_every_event_delivered_once_in_order(self):
        incident = make_incident()
        agent = Recorder()
        replay(incident, agent, 60)
        previous = -1
        seen = []
        for now, batch in agent.calls:
            for event in batch:
                self.assertTrue(previous < event["t"] <= now)
                seen.append(event["id"])
            previous = now
        self.assertEqual(seen, [event["id"] for event in incident.events])

    def test_first_call_contains_all_events_up_to_step(self):
        incident = make_incident()
        agent = Recorder()
        replay(incident, agent, 60)
        first = agent.calls[0][1]
        self.assertTrue(first)
        self.assertTrue(all(event["t"] <= 60 for event in first))
        self.assertIn(0, {event["t"] for event in first})

    def test_no_truth_or_extra_keys_in_events(self):
        agent = Recorder()
        replay(make_incident(), agent)
        for _, batch in agent.calls:
            for event in batch:
                self.assertEqual(set(event), {"id", "t", "kind", "service", "attrs"})
                self.assertNotIn("truth", event)


class CopyTest(unittest.TestCase):
    def test_agent_cannot_mutate_incident(self):
        incident = make_incident()
        before = json.dumps(incident.events, sort_keys=True)

        class Mutator:
            def observe(self, events, now):
                for event in events:
                    event["t"] = -1
                    event["attrs"]["value"] = -1
                    event["attrs"]["message"] = "changed"
                return None

        replay(incident, Mutator())
        self.assertEqual(json.dumps(incident.events, sort_keys=True), before)


class DiagnosisTest(unittest.TestCase):
    def test_stops_at_first_diagnosis(self):
        agent = Recorder(diagnose_at=120)
        result = replay(make_incident(), agent)
        self.assertIs(result.diagnosis, agent.diagnosis)
        self.assertEqual(result.diagnosed_at, 120)
        self.assertEqual(result.calls, 2)
        self.assertIsNone(result.error)

    def test_no_diagnosis_runs_to_the_end(self):
        result = replay(make_incident(), Recorder())
        self.assertIsNone(result.diagnosis)
        self.assertIsNone(result.diagnosed_at)
        self.assertIsNone(result.error)
        self.assertEqual(result.calls, 10)

    def test_returns_run_result(self):
        self.assertIsInstance(replay(make_incident(), Recorder()), RunResult)


class ErrorTest(unittest.TestCase):
    def test_exception_is_recorded(self):
        result = replay(make_incident(), Recorder(action="raise"))
        self.assertEqual(result.error, "RuntimeError: boom")
        self.assertIsNone(result.diagnosis)
        self.assertIsNone(result.diagnosed_at)
        self.assertEqual(result.calls, 1)

    def test_invalid_return_is_recorded(self):
        result = replay(make_incident(), Recorder(action="invalid"))
        self.assertEqual(result.error, "invalid diagnosis")
        self.assertIsNone(result.diagnosis)
        self.assertEqual(result.calls, 1)

    def test_error_on_later_call_keeps_count(self):
        class LateBoom(Recorder):
            def observe(self, events, now):
                if now >= 180:
                    self.calls.append((now, events))
                    raise ValueError("late")
                return super().observe(events, now)

        agent = LateBoom()
        result = replay(make_incident(), agent)
        self.assertEqual(result.error, "ValueError: late")
        self.assertEqual(result.calls, len(agent.calls))


if __name__ == "__main__":
    unittest.main()
