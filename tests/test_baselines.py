import unittest

from replaykit.baselines import LastDeployAgent, ThresholdAgent


def metric(service, name, value, t=0, id="ev-metric"):
    return {
        "id": id,
        "t": t,
        "kind": "metric",
        "service": service,
        "attrs": {"name": name, "value": value},
    }


def deploy(service, t=0, id="ev-deploy", version="r000001"):
    return {
        "id": id,
        "t": t,
        "kind": "deploy",
        "service": service,
        "attrs": {"version": version},
    }


def log(service, t=0, id="ev-log"):
    return {
        "id": id,
        "t": t,
        "kind": "log",
        "service": service,
        "attrs": {"level": "error", "message": "boom"},
    }


def alert(service, t=0, id="ev-alert"):
    return {
        "id": id,
        "t": t,
        "kind": "alert",
        "service": service,
        "attrs": {"rule": "error_rate_high", "severity": "page"},
    }


class LastDeployAgentTest(unittest.TestCase):
    def test_returns_none_until_a_deploy_is_seen(self):
        agent = LastDeployAgent()
        events = [
            metric("db", "error_rate", 0.9, id="ev-m"),
            log("db", id="ev-l"),
            alert("db", id="ev-a"),
        ]
        self.assertIsNone(agent.observe(events, 60))
        self.assertIsNone(agent.observe([], 120))

    def test_diagnoses_the_deploy(self):
        diagnosis = LastDeployAgent().observe(
            [deploy("db", t=120, id="ev-0007")], 120
        )
        self.assertEqual(diagnosis.service, "db")
        self.assertEqual(diagnosis.fault_type, "bad_deploy")
        self.assertEqual(diagnosis.confidence, 0.6)
        self.assertEqual(diagnosis.evidence, ("ev-0007",))

    def test_picks_the_most_recent_deploy(self):
        diagnosis = LastDeployAgent().observe(
            [
                deploy("api", t=60, id="ev-0001"),
                deploy("db", t=120, id="ev-0002"),
            ],
            120,
        )
        self.assertEqual((diagnosis.service, diagnosis.evidence), ("db", ("ev-0002",)))

    def test_breaks_time_ties_by_larger_id(self):
        diagnosis = LastDeployAgent().observe(
            [
                deploy("api", t=120, id="ev-0002"),
                deploy("db", t=120, id="ev-0003"),
            ],
            120,
        )
        self.assertEqual((diagnosis.service, diagnosis.evidence), ("db", ("ev-0003",)))

    def test_tracks_latest_deploy_across_calls(self):
        agent = LastDeployAgent()
        first = agent.observe([deploy("api", t=60, id="ev-0001")], 60)
        second = agent.observe([deploy("db", t=120, id="ev-0002")], 120)
        self.assertEqual((first.service, first.evidence), ("api", ("ev-0001",)))
        self.assertEqual((second.service, second.evidence), ("db", ("ev-0002",)))


class ThresholdAgentTest(unittest.TestCase):
    def test_returns_none_without_a_trigger(self):
        events = [
            metric("api", "memory_mb", 899, id="ev-m"),
            metric("api", "latency_ms", 1499, id="ev-l"),
            metric("api", "error_rate", 0.199, id="ev-e"),
            log("api", id="ev-log"),
            alert("api", id="ev-alert"),
        ]
        self.assertIsNone(ThresholdAgent().observe(events, 60))

    def test_memory_threshold_is_inclusive(self):
        diagnosis = ThresholdAgent().observe(
            [metric("db", "memory_mb", 900, id="ev-m1")], 60
        )
        self.assertEqual(diagnosis.service, "db")
        self.assertEqual(diagnosis.fault_type, "memory_leak")
        self.assertEqual(diagnosis.confidence, 0.7)
        self.assertEqual(diagnosis.evidence, ("ev-m1",))

    def test_memory_uses_highest_value(self):
        diagnosis = ThresholdAgent().observe(
            [
                metric("api", "memory_mb", 950, id="ev-m1"),
                metric("db", "memory_mb", 980, id="ev-m2"),
            ],
            60,
        )
        self.assertEqual((diagnosis.service, diagnosis.evidence), ("db", ("ev-m2",)))

    def test_memory_ties_sort_by_service(self):
        diagnosis = ThresholdAgent().observe(
            [
                metric("api", "memory_mb", 950, id="ev-m1"),
                metric("db", "memory_mb", 950, id="ev-m2"),
            ],
            60,
        )
        self.assertEqual((diagnosis.service, diagnosis.evidence), ("api", ("ev-m1",)))

    def test_latency_threshold_and_highest_value(self):
        diagnosis = ThresholdAgent().observe(
            [
                metric("api", "latency_ms", 1600, id="ev-l1"),
                metric("db", "latency_ms", 2000, id="ev-l2"),
            ],
            60,
        )
        self.assertEqual(diagnosis.service, "db")
        self.assertEqual(diagnosis.fault_type, "dependency_timeout")
        self.assertEqual(diagnosis.confidence, 0.7)
        self.assertEqual(diagnosis.evidence, ("ev-l2",))

    def test_latency_threshold_is_inclusive(self):
        diagnosis = ThresholdAgent().observe(
            [metric("db", "latency_ms", 1500, id="ev-l1")], 60
        )
        self.assertEqual((diagnosis.service, diagnosis.evidence), ("db", ("ev-l1",)))

    def test_latency_ties_sort_by_service(self):
        diagnosis = ThresholdAgent().observe(
            [
                metric("api", "latency_ms", 2000, id="ev-l1"),
                metric("db", "latency_ms", 2000, id="ev-l2"),
            ],
            60,
        )
        self.assertEqual((diagnosis.service, diagnosis.evidence), ("api", ("ev-l1",)))

    def test_memory_outranks_latency_and_error(self):
        diagnosis = ThresholdAgent().observe(
            [
                metric("api", "memory_mb", 950, id="ev-m"),
                metric("db", "latency_ms", 5000, id="ev-l"),
                metric("cache", "error_rate", 0.5, id="ev-e"),
            ],
            60,
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.fault_type, diagnosis.evidence),
            ("api", "memory_leak", ("ev-m",)),
        )

    def test_latency_outranks_error(self):
        diagnosis = ThresholdAgent().observe(
            [
                metric("api", "latency_ms", 2000, id="ev-l"),
                metric("db", "error_rate", 0.5, id="ev-e"),
            ],
            60,
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.fault_type, diagnosis.evidence),
            ("api", "dependency_timeout", ("ev-l",)),
        )

    def test_error_threshold_without_deploy_is_config_error(self):
        diagnosis = ThresholdAgent().observe(
            [metric("db", "error_rate", 0.2, id="ev-e")], 60
        )
        self.assertEqual(diagnosis.service, "db")
        self.assertEqual(diagnosis.fault_type, "config_error")
        self.assertEqual(diagnosis.confidence, 0.7)
        self.assertEqual(diagnosis.evidence, ("ev-e",))

    def test_error_with_deploy_is_bad_deploy_and_cites_deploy(self):
        diagnosis = ThresholdAgent().observe(
            [
                deploy("db", t=60, id="ev-d"),
                metric("db", "error_rate", 0.3, t=60, id="ev-e"),
            ],
            60,
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.fault_type, diagnosis.confidence, diagnosis.evidence),
            ("db", "bad_deploy", 0.7, ("ev-e", "ev-d")),
        )

    def test_deploy_for_another_service_does_not_change_type(self):
        agent = ThresholdAgent()
        self.assertIsNone(agent.observe([deploy("api", t=60, id="ev-d")], 60))
        diagnosis = agent.observe(
            [metric("db", "error_rate", 0.3, t=120, id="ev-e")], 120
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.fault_type, diagnosis.evidence),
            ("db", "config_error", ("ev-e",)),
        )

    def test_error_uses_latest_deploy_for_that_service(self):
        agent = ThresholdAgent()
        self.assertIsNone(agent.observe([deploy("db", t=60, id="ev-d1")], 60))
        diagnosis = agent.observe(
            [
                deploy("db", t=120, id="ev-d2"),
                metric("db", "error_rate", 0.3, t=120, id="ev-e"),
            ],
            120,
        )
        self.assertEqual(diagnosis.evidence, ("ev-e", "ev-d2"))

    def test_latest_deploy_ties_use_larger_id(self):
        diagnosis = ThresholdAgent().observe(
            [
                deploy("db", t=60, id="ev-d2"),
                deploy("db", t=60, id="ev-d1"),
                metric("db", "error_rate", 0.3, t=60, id="ev-e"),
            ],
            60,
        )
        self.assertEqual(diagnosis.evidence, ("ev-e", "ev-d2"))

    def test_latest_metric_value_and_id_are_used(self):
        agent = ThresholdAgent()
        self.assertIsNone(
            agent.observe([metric("db", "memory_mb", 500, t=60, id="ev-m1")], 60)
        )
        diagnosis = agent.observe(
            [metric("db", "memory_mb", 950, t=120, id="ev-m2")], 120
        )
        self.assertEqual(diagnosis.evidence, ("ev-m2",))
        self.assertIsNone(
            agent.observe([metric("db", "memory_mb", 500, t=180, id="ev-m3")], 180)
        )

    def test_error_uses_highest_value_and_ties_sort_by_service(self):
        highest = ThresholdAgent().observe(
            [
                metric("api", "error_rate", 0.3, id="ev-e1"),
                metric("db", "error_rate", 0.4, id="ev-e2"),
            ],
            60,
        )
        self.assertEqual((highest.service, highest.evidence), ("db", ("ev-e2",)))
        tied = ThresholdAgent().observe(
            [
                metric("api", "error_rate", 0.3, id="ev-e1"),
                metric("db", "error_rate", 0.3, id="ev-e2"),
            ],
            60,
        )
        self.assertEqual((tied.service, tied.evidence), ("api", ("ev-e1",)))
