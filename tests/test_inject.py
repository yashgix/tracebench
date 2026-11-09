import json
import unittest

from replaykit.inject import inject, validate_fault
from replaykit.model import validate_scenario


def scenario(duration=600, services=("api", "db"), calls=None):
    return {
        "id": "scn-1",
        "duration": duration,
        "services": list(services),
        "calls": {"api": ["db"]} if calls is None else calls,
        "events": [
            {
                "id": "ev-bg",
                "t": 30,
                "kind": "log",
                "service": "db",
                "attrs": {"level": "info", "message": "background"},
            }
        ],
    }


def fault(**overrides):
    base = {"type": "bad_deploy", "service": "api", "start": 120}
    base.update(overrides)
    return base


def metric_map(incident, service, name):
    return {
        event["t"]: event["attrs"]["value"]
        for event in incident.events
        if event["kind"] == "metric"
        and event["service"] == service
        and event["attrs"]["name"] == name
    }


def events_of(incident, kind, service):
    return [e for e in incident.events if e["kind"] == kind and e["service"] == service]


def fault_logs(incident, service, message):
    return [e for e in events_of(incident, "log", service) if e["attrs"]["message"] == message]


def alerts(incident, service):
    return events_of(incident, "alert", service)


def deploys(incident, service):
    return events_of(incident, "deploy", service)


class ValidateFaultTest(unittest.TestCase):
    def test_accepts_valid_fault(self):
        validate_fault(scenario(), fault())

    def test_accepts_boundary_start(self):
        incident = inject(scenario(), fault(start=1), 1)
        self.assertEqual(incident.truth.start, 1)

    def test_rejects_non_object(self):
        with self.assertRaises(ValueError):
            validate_fault(scenario(), ["bad_deploy", "api", 120])

    def test_rejects_missing_or_extra_keys(self):
        for bad in (
            {"type": "bad_deploy", "service": "api"},
            {"type": "bad_deploy", "service": "api", "start": 120, "extra": 1},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_fault(scenario(), bad)

    def test_rejects_unknown_type_or_service(self):
        with self.assertRaises(ValueError):
            validate_fault(scenario(), fault(type="dns_outage"))
        with self.assertRaises(ValueError):
            validate_fault(scenario(), fault(service="cache"))

    def test_rejects_bad_start(self):
        for start in (0, 600, -5, 600.0, "120", True, False):
            with self.subTest(start=start):
                with self.assertRaises(ValueError):
                    inject(scenario(), fault(start=start), 1)


class InjectShapeTest(unittest.TestCase):
    def test_incident_id_and_metadata(self):
        incident = inject(scenario(), fault(), 42)
        self.assertEqual(incident.id, "scn-1:bad_deploy:api:42")
        self.assertEqual(incident.scenario_id, "scn-1")
        self.assertEqual(incident.duration, 600)
        self.assertEqual(incident.services, ["api", "db"])
        self.assertEqual(incident.calls, {"api": ["db"]})

    def test_truth_fields(self):
        incident = inject(scenario(), fault(type="config_error", service="db", start=60), 5)
        self.assertEqual(incident.truth.service, "db")
        self.assertEqual(incident.truth.fault_type, "config_error")
        self.assertEqual(incident.truth.start, 60)
        self.assertIsInstance(incident.truth.injected_ids, frozenset)

    def test_events_have_exact_keys(self):
        incident = inject(scenario(), fault(type="dependency_timeout", service="db"), 5)
        for event in incident.events:
            self.assertEqual(set(event), {"id", "t", "kind", "service", "attrs"})
            self.assertIsInstance(event["id"], str)

    def test_incident_events_form_a_valid_scenario(self):
        incident = inject(scenario(), fault(service="db"), 1)
        validate_scenario(
            {
                "id": incident.scenario_id,
                "duration": incident.duration,
                "services": incident.services,
                "calls": incident.calls,
                "events": incident.events,
            }
        )

    def test_does_not_mutate_scenario(self):
        source = scenario()
        before = json.dumps(source, sort_keys=True)
        inject(source, fault(service="db"), 1)
        self.assertEqual(json.dumps(source, sort_keys=True), before)


class DeterminismTest(unittest.TestCase):
    def test_same_seed_same_incident(self):
        first = inject(scenario(), fault(type="memory_leak", service="db"), 7)
        second = inject(scenario(), fault(type="memory_leak", service="db"), 7)
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.truth, second.truth)
        self.assertEqual(first.id, second.id)

    def test_different_seed_differs(self):
        first = inject(scenario(), fault(service="db"), 1)
        second = inject(scenario(), fault(service="db"), 2)
        self.assertNotEqual(first.events, second.events)
        self.assertNotEqual(first.id, second.id)


class IdsAndOrderTest(unittest.TestCase):
    def test_ids_are_sequential_and_sorted(self):
        incident = inject(scenario(), fault(service="db"), 3)
        ids = [event["id"] for event in incident.events]
        self.assertEqual(ids, [f"ev-{i:04d}" for i in range(1, len(ids) + 1)])
        keys = [
            (e["t"], e["kind"], e["service"], json.dumps(e["attrs"], sort_keys=True))
            for e in incident.events
        ]
        self.assertEqual(keys, sorted(keys))

    def test_background_event_gets_new_id(self):
        incident = inject(scenario(), fault(service="db"), 3)
        background = [e for e in incident.events if e["attrs"].get("message") == "background"]
        self.assertEqual(len(background), 1)
        self.assertNotEqual(background[0]["id"], "ev-bg")
        self.assertNotIn(background[0]["id"], incident.truth.injected_ids)


class MetricGridTest(unittest.TestCase):
    def test_sample_times_and_counts(self):
        incident = inject(scenario(duration=180), fault(type="config_error", service="db"), 1)
        times = sorted({e["t"] for e in incident.events if e["kind"] == "metric"})
        self.assertEqual(times, [0, 60, 120, 180])
        metrics = [e for e in incident.events if e["kind"] == "metric"]
        self.assertEqual(len(metrics), 4 * 2 * 3)

    def test_partial_last_sample(self):
        incident = inject(scenario(duration=90), fault(type="config_error", service="db", start=30), 1)
        times = sorted({e["t"] for e in incident.events if e["kind"] == "metric"})
        self.assertEqual(times, [0, 60])

    def test_untouched_metrics_stay_in_baseline(self):
        incident = inject(scenario(), fault(type="dependency_timeout", service="db"), 9)
        memory = metric_map(incident, "api", "memory_mb")
        self.assertGreaterEqual(min(memory.values()), 400)
        self.assertLessEqual(max(memory.values()), 420)
        self.assertLessEqual(metric_map(incident, "api", "error_rate")[0], 0.02)
        latency = metric_map(incident, "api", "latency_ms")[0]
        self.assertTrue(80 <= latency <= 120)


class BadDeployTest(unittest.TestCase):
    def setUp(self):
        self.incident = inject(scenario(), fault(type="bad_deploy", service="db", start=120), 1)

    def test_deploy_event(self):
        found = deploys(self.incident, "db")
        self.assertEqual([e["t"] for e in found], [120])
        self.assertRegex(found[0]["attrs"]["version"], r"^r[0-9a-f]{6}$")

    def test_target_error_rate_raised_from_start(self):
        rates = metric_map(self.incident, "db", "error_rate")
        self.assertLessEqual(rates[0], 0.02)
        for t in (120, 180, 600):
            self.assertTrue(0.25 <= rates[t] <= 0.5)

    def test_error_logs_at_every_sample_from_start(self):
        found = fault_logs(self.incident, "db", "request failed status=500")
        self.assertEqual([e["t"] for e in found], list(range(120, 601, 60)))
        self.assertTrue(all(e["attrs"]["level"] == "error" for e in found))

    def test_caller_error_rate_lags_by_one_sample(self):
        rates = metric_map(self.incident, "api", "error_rate")
        self.assertLessEqual(rates[120], 0.02)
        self.assertTrue(0.05 <= rates[180] <= 0.15)

    def test_alert_at_start_plus_120(self):
        found = alerts(self.incident, "db")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["t"], 240)
        self.assertEqual(found[0]["attrs"], {"rule": "error_rate_high", "severity": "page"})

    def test_latency_and_memory_untouched(self):
        self.assertLessEqual(max(metric_map(self.incident, "db", "latency_ms").values()), 120)
        self.assertLessEqual(max(metric_map(self.incident, "db", "memory_mb").values()), 420)


class ConfigErrorTest(unittest.TestCase):
    def setUp(self):
        self.incident = inject(scenario(), fault(type="config_error", service="db", start=120), 2)

    def test_config_log_and_no_deploy(self):
        found = fault_logs(self.incident, "db", "config reloaded")
        self.assertEqual([e["t"] for e in found], [120])
        self.assertEqual(found[0]["attrs"]["level"], "info")
        self.assertEqual(deploys(self.incident, "db"), [])

    def test_error_rate_and_error_logs(self):
        self.assertTrue(0.25 <= metric_map(self.incident, "db", "error_rate")[120] <= 0.5)
        found = fault_logs(self.incident, "db", "invalid configuration key")
        self.assertEqual(len(found), 9)
        self.assertTrue(all(e["attrs"]["level"] == "error" for e in found))

    def test_caller_and_alert(self):
        self.assertTrue(0.05 <= metric_map(self.incident, "api", "error_rate")[180] <= 0.15)
        self.assertEqual(alerts(self.incident, "db")[0]["attrs"]["rule"], "error_rate_high")


class DependencyTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.incident = inject(scenario(), fault(type="dependency_timeout", service="db", start=120), 3)

    def test_target_latency_and_warn_logs(self):
        latency = metric_map(self.incident, "db", "latency_ms")
        self.assertLessEqual(latency[60], 120)
        self.assertTrue(2000 <= latency[120] <= 5000)
        found = fault_logs(self.incident, "db", "query exceeded 2000ms")
        self.assertEqual(len(found), 9)
        self.assertTrue(all(e["attrs"]["level"] == "warn" for e in found))

    def test_caller_latency_and_error_rate(self):
        latency = metric_map(self.incident, "api", "latency_ms")
        self.assertLessEqual(latency[120], 120)
        self.assertTrue(800 <= latency[180] <= 1500)
        self.assertTrue(0.05 <= metric_map(self.incident, "api", "error_rate")[180] <= 0.15)

    def test_alert_uses_latency_rule(self):
        found = alerts(self.incident, "db")
        self.assertEqual((found[0]["t"], found[0]["attrs"]["rule"]), (240, "latency_high"))


class MemoryLeakTest(unittest.TestCase):
    def setUp(self):
        self.incident = inject(
            scenario(duration=1200), fault(type="memory_leak", service="db", start=120), 4
        )
        self.memory = metric_map(self.incident, "db", "memory_mb")

    def test_memory_grows_by_40_per_sample(self):
        self.assertTrue(400 <= self.memory[60] <= 420)
        self.assertEqual(self.memory[120], 460)
        self.assertEqual(self.memory[180], 500)
        self.assertEqual(self.memory[600], 780)

    def test_oom_log_and_error_rate_after_oom(self):
        oom = fault_logs(self.incident, "db", "OOMKilled")
        self.assertEqual(len(oom), 1)
        self.assertEqual(oom[0]["t"], 960)
        rates = metric_map(self.incident, "db", "error_rate")
        self.assertLessEqual(rates[900], 0.02)
        self.assertTrue(0.1 <= rates[960] <= 0.3)

    def test_alert_memory_high_at_oom(self):
        found = alerts(self.incident, "db")
        self.assertEqual((found[0]["t"], found[0]["attrs"]["rule"]), (960, "memory_high"))

    def test_callers_and_latency_untouched(self):
        self.assertLessEqual(max(metric_map(self.incident, "api", "error_rate").values()), 0.02)
        self.assertLessEqual(max(metric_map(self.incident, "api", "memory_mb").values()), 420)


class AlertBoundaryTest(unittest.TestCase):
    def test_alert_skipped_when_after_duration(self):
        incident = inject(scenario(duration=300), fault(service="db", start=240), 1)
        self.assertEqual(alerts(incident, "db"), [])

    def test_alert_kept_at_exact_duration(self):
        incident = inject(scenario(duration=240), fault(service="db", start=120), 1)
        self.assertEqual([e["t"] for e in alerts(incident, "db")], [240])

    def test_memory_leak_without_oom_has_no_log_or_alert(self):
        incident = inject(scenario(duration=600), fault(type="memory_leak", service="db"), 1)
        self.assertEqual(alerts(incident, "db"), [])
        self.assertEqual(fault_logs(incident, "db", "OOMKilled"), [])


class InjectedIdsTest(unittest.TestCase):
    def test_bad_deploy_marks_every_generated_event(self):
        incident = inject(scenario(), fault(service="db"), 1)
        injected = incident.truth.injected_ids
        # 1 deploy + 9 error logs + 1 alert + 9 target metrics + 8 caller metrics
        self.assertEqual(len(injected), 1 + 9 + 1 + 9 + 8)
        self.assertIn(deploys(incident, "db")[0]["id"], injected)
        for event in fault_logs(incident, "db", "request failed status=500"):
            self.assertIn(event["id"], injected)
        self.assertIn(alerts(incident, "db")[0]["id"], injected)

    def test_unchanged_metrics_and_background_are_not_injected(self):
        incident = inject(scenario(), fault(service="db"), 1)
        injected = incident.truth.injected_ids
        for event in incident.events:
            if event["kind"] == "metric" and event["attrs"]["name"] == "memory_mb":
                self.assertNotIn(event["id"], injected)
            if event["attrs"].get("message") == "background":
                self.assertNotIn(event["id"], injected)

    def test_memory_leak_marks_memory_and_post_oom_error_rate(self):
        incident = inject(
            scenario(duration=1200), fault(type="memory_leak", service="db", start=120), 4
        )
        injected = incident.truth.injected_ids
        target_metrics = [
            e
            for e in incident.events
            if e["kind"] == "metric" and e["service"] == "db" and e["t"] >= 120
        ]
        memory = [e for e in target_metrics if e["attrs"]["name"] == "memory_mb"]
        self.assertEqual(len(memory), 19)
        for event in target_metrics:
            name = event["attrs"]["name"]
            if name == "memory_mb" or (name == "error_rate" and event["t"] >= 960):
                self.assertIn(event["id"], injected)
            else:
                self.assertNotIn(event["id"], injected)
        api_ids = {
            e["id"] for e in incident.events if e["service"] == "api" and e["kind"] == "metric"
        }
        self.assertFalse(injected & api_ids)


if __name__ == "__main__":
    unittest.main()
