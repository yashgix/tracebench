import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError

from replaykit.model import FAULT_TYPES, Diagnosis, load_scenario


def make_scenario(**overrides):
    scenario = {
        "id": "scn-1",
        "duration": 300,
        "services": ["api", "db"],
        "calls": {"api": ["db"]},
        "events": [
            {
                "id": "ev-b",
                "t": 60,
                "kind": "log",
                "service": "api",
                "attrs": {"level": "info", "message": "hello"},
            },
            {
                "id": "ev-a",
                "t": 0,
                "kind": "deploy",
                "service": "db",
                "attrs": {"version": "r000001"},
            },
            {
                "id": "ev-c",
                "t": 0,
                "kind": "metric",
                "service": "api",
                "attrs": {"name": "error_rate", "value": 0.01},
            },
        ],
    }
    scenario.update(overrides)
    return scenario


def make_event(**overrides):
    event = {
        "id": "ev-x",
        "t": 10,
        "kind": "metric",
        "service": "api",
        "attrs": {"name": "latency_ms", "value": 100},
    }
    event.update(overrides)
    return event


def with_events(*events):
    return make_scenario(events=list(events))


class LoadScenarioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def load(self, data):
        path = os.path.join(self.tmp.name, "scenario.json")
        with open(path, "w", encoding="utf-8") as handle:
            if isinstance(data, str):
                handle.write(data)
            else:
                json.dump(data, handle)
        return load_scenario(path)

    def assertInvalid(self, data):
        with self.assertRaises(ValueError):
            self.load(data)

    # --- happy path -------------------------------------------------------

    def test_sorts_events_by_t_then_id(self):
        loaded = self.load(make_scenario())
        self.assertEqual([e["id"] for e in loaded["events"]], ["ev-a", "ev-c", "ev-b"])

    def test_returns_other_fields_unchanged(self):
        loaded = self.load(make_scenario())
        self.assertEqual(loaded["id"], "scn-1")
        self.assertEqual(loaded["duration"], 300)
        self.assertEqual(loaded["services"], ["api", "db"])
        self.assertEqual(loaded["calls"], {"api": ["db"]})

    def test_accepts_every_event_kind(self):
        loaded = self.load(
            with_events(
                make_event(id="ev-1", kind="metric", attrs={"name": "memory_mb", "value": 400}),
                make_event(id="ev-2", kind="log", attrs={"level": "warn", "message": "slow"}),
                make_event(id="ev-3", kind="deploy", attrs={"version": "r123456"}),
                make_event(id="ev-4", kind="alert", attrs={"rule": "latency_high", "severity": "page"}),
            )
        )
        self.assertEqual(len(loaded["events"]), 4)

    def test_accepts_float_and_int_metric_values(self):
        loaded = self.load(
            with_events(
                make_event(id="ev-1", attrs={"name": "error_rate", "value": 0.5}),
                make_event(id="ev-2", attrs={"name": "latency_ms", "value": 120}),
            )
        )
        self.assertEqual(len(loaded["events"]), 2)

    def test_t_boundaries_are_inclusive(self):
        loaded = self.load(
            with_events(
                make_event(id="ev-1", t=0),
                make_event(id="ev-2", t=300),
            )
        )
        self.assertEqual([e["t"] for e in loaded["events"]], [0, 300])

    def test_calls_may_be_empty(self):
        loaded = self.load(make_scenario(calls={}))
        self.assertEqual(loaded["calls"], {})

    # --- file and shape errors -------------------------------------------

    def test_invalid_json(self):
        self.assertInvalid("{not json")

    def test_scenario_must_be_object(self):
        self.assertInvalid([])

    def test_missing_scenario_key(self):
        broken = make_scenario()
        del broken["calls"]
        self.assertInvalid(broken)

    def test_extra_scenario_key(self):
        self.assertInvalid(make_scenario(extra=1))

    def test_bad_scenario_id(self):
        self.assertInvalid(make_scenario(id=""))
        self.assertInvalid(make_scenario(id=7))

    def test_bad_duration(self):
        self.assertInvalid(make_scenario(duration=0))
        self.assertInvalid(make_scenario(duration=-60))
        self.assertInvalid(make_scenario(duration=60.0))
        self.assertInvalid(make_scenario(duration=True))

    def test_bad_services(self):
        self.assertInvalid(make_scenario(services=[]))
        self.assertInvalid(make_scenario(services="api"))
        self.assertInvalid(make_scenario(services=["api", "api"]))
        self.assertInvalid(make_scenario(services=["api", 3]))

    def test_bad_calls_container(self):
        self.assertInvalid(make_scenario(calls=[]))

    def test_calls_unknown_caller(self):
        self.assertInvalid(make_scenario(calls={"web": ["db"]}))

    def test_calls_unknown_callee(self):
        self.assertInvalid(make_scenario(calls={"api": ["cache"]}))

    def test_calls_self_reference(self):
        self.assertInvalid(make_scenario(calls={"api": ["api"]}))

    def test_calls_bad_callee_list(self):
        self.assertInvalid(make_scenario(calls={"api": "db"}))

    def test_events_must_be_list(self):
        self.assertInvalid(make_scenario(events={}))

    def test_event_must_be_object(self):
        self.assertInvalid(with_events("ev-1"))

    def test_event_missing_key(self):
        event = make_event()
        del event["service"]
        self.assertInvalid(with_events(event))

    def test_event_extra_key(self):
        self.assertInvalid(with_events(make_event(extra="x")))

    def test_event_bad_id(self):
        self.assertInvalid(with_events(make_event(id="")))
        self.assertInvalid(with_events(make_event(id=12)))

    def test_event_bad_t(self):
        self.assertInvalid(with_events(make_event(t="10")))
        self.assertInvalid(with_events(make_event(t=True)))

    def test_event_t_out_of_range(self):
        self.assertInvalid(with_events(make_event(t=-1)))
        self.assertInvalid(with_events(make_event(t=301)))

    def test_event_unknown_kind(self):
        self.assertInvalid(with_events(make_event(kind="trace")))

    def test_event_unknown_service(self):
        self.assertInvalid(with_events(make_event(service="cache")))

    def test_event_duplicate_id(self):
        self.assertInvalid(
            with_events(make_event(id="ev-1", t=0), make_event(id="ev-1", t=60))
        )

    def test_attrs_must_be_object(self):
        self.assertInvalid(with_events(make_event(attrs=[])))

    def test_attrs_extra_key(self):
        self.assertInvalid(
            with_events(make_event(attrs={"name": "error_rate", "value": 0.1, "unit": "x"}))
        )

    def test_attrs_missing_key(self):
        self.assertInvalid(with_events(make_event(attrs={"name": "error_rate"})))

    def test_metric_unknown_name(self):
        self.assertInvalid(with_events(make_event(attrs={"name": "disk_io", "value": 1})))

    def test_metric_bad_value(self):
        self.assertInvalid(with_events(make_event(attrs={"name": "error_rate", "value": "0.1"})))
        self.assertInvalid(with_events(make_event(attrs={"name": "error_rate", "value": True})))

    def test_log_bad_level(self):
        self.assertInvalid(
            with_events(make_event(kind="log", attrs={"level": "debug", "message": "x"}))
        )

    def test_log_bad_message(self):
        self.assertInvalid(
            with_events(make_event(kind="log", attrs={"level": "info", "message": 3}))
        )

    def test_deploy_bad_version(self):
        self.assertInvalid(with_events(make_event(kind="deploy", attrs={"version": 1})))

    def test_alert_bad_rule_and_severity(self):
        self.assertInvalid(
            with_events(make_event(kind="alert", attrs={"rule": 1, "severity": "page"}))
        )
        self.assertInvalid(
            with_events(make_event(kind="alert", attrs={"rule": "x", "severity": "urgent"}))
        )


class FaultTypesTest(unittest.TestCase):
    def test_fault_types_tuple(self):
        self.assertEqual(
            FAULT_TYPES,
            ("bad_deploy", "config_error", "dependency_timeout", "memory_leak", "pool_exhaustion"),
        )


class DiagnosisTest(unittest.TestCase):
    def test_accepts_valid_values(self):
        diagnosis = Diagnosis("api", "bad_deploy", 0.6, ("ev-0001",))
        self.assertEqual(diagnosis.service, "api")
        self.assertEqual(diagnosis.fault_type, "bad_deploy")
        self.assertEqual(diagnosis.confidence, 0.6)
        self.assertEqual(diagnosis.evidence, ("ev-0001",))

    def test_confidence_bounds_are_inclusive(self):
        Diagnosis("api", "memory_leak", 0.0, ())
        Diagnosis("api", "memory_leak", 1.0, ())

    def test_rejects_unknown_fault_type(self):
        with self.assertRaises(ValueError):
            Diagnosis("api", "dns_outage", 0.5)

    def test_rejects_confidence_out_of_range(self):
        for confidence in (-0.01, 1.01):
            with self.assertRaises(ValueError):
                Diagnosis("api", "bad_deploy", confidence)

    def test_rejects_non_numeric_confidence(self):
        with self.assertRaises(ValueError):
            Diagnosis("api", "bad_deploy", "high")

    def test_evidence_defaults_to_empty_tuple(self):
        self.assertEqual(Diagnosis("api", "config_error", 0.2).evidence, ())

    def test_evidence_is_coerced_to_tuple(self):
        diagnosis = Diagnosis("api", "config_error", 0.2, ["ev-1", "ev-2"])
        self.assertEqual(diagnosis.evidence, ("ev-1", "ev-2"))

    def test_is_frozen(self):
        diagnosis = Diagnosis("api", "bad_deploy", 0.5)
        with self.assertRaises(FrozenInstanceError):
            diagnosis.service = "db"


if __name__ == "__main__":
    unittest.main()
