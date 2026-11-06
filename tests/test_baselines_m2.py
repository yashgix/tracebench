import unittest

from replaykit.baselines import (
    AGENT_NAMES,
    POOL_MESSAGE,
    LastDeployAgent,
    ThresholdAgent,
    TopologyAgent,
    make_agent,
)


def metric(service, name, value, t=60, id=None):
    return {
        "id": id if id is not None else f"{service}-{name}-{t}",
        "t": t,
        "kind": "metric",
        "service": service,
        "attrs": {"name": name, "value": value},
    }


def deploy(service, t=60, id="ev-deploy"):
    return {
        "id": id,
        "t": t,
        "kind": "deploy",
        "service": service,
        "attrs": {"version": "r000001"},
    }


def log(service, level, message, t=60, id="ev-log"):
    return {
        "id": id,
        "t": t,
        "kind": "log",
        "service": service,
        "attrs": {"level": level, "message": message},
    }


class NoDiagnosisTest(unittest.TestCase):
    def test_returns_none_without_events(self):
        self.assertIsNone(TopologyAgent({}).observe([], 60))

    def test_returns_none_below_thresholds(self):
        events = [
            metric("api", "memory_mb", 899, id="ev-m"),
            metric("api", "latency_ms", 1499, id="ev-l"),
            metric("api", "error_rate", 0.199, id="ev-e"),
        ]
        self.assertIsNone(TopologyAgent({}).observe(events, 60))

    def test_only_pool_warn_logs_count(self):
        events = [
            log("api", "info", POOL_MESSAGE, id="ev-i"),
            log("api", "warn", "slow query", id="ev-w"),
            metric("api", "latency_ms", 2000, id="ev-l"),
        ]
        diagnosis = TopologyAgent({}).observe(events, 60)
        self.assertEqual(
            (diagnosis.fault_type, diagnosis.evidence),
            ("dependency_timeout", ("ev-l",)),
        )


class ThresholdTest(unittest.TestCase):
    def test_memory_threshold_is_inclusive(self):
        diagnosis = TopologyAgent({}).observe(
            [metric("db", "memory_mb", 900, id="ev-m")], 60
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.fault_type, diagnosis.confidence,
             diagnosis.evidence),
            ("db", "memory_leak", 0.8, ("ev-m",)),
        )

    def test_latency_threshold_is_inclusive(self):
        diagnosis = TopologyAgent({}).observe(
            [metric("db", "latency_ms", 1500, id="ev-l")], 60
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.fault_type, diagnosis.evidence),
            ("db", "dependency_timeout", ("ev-l",)),
        )

    def test_error_threshold_is_inclusive(self):
        diagnosis = TopologyAgent({}).observe(
            [metric("db", "error_rate", 0.2, id="ev-e")], 60
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.fault_type, diagnosis.evidence),
            ("db", "config_error", ("ev-e",)),
        )


class TypeTest(unittest.TestCase):
    def test_memory_outranks_latency_and_error(self):
        events = [
            metric("db", "memory_mb", 950, id="ev-m"),
            metric("db", "latency_ms", 5000, id="ev-l"),
            metric("db", "error_rate", 0.5, id="ev-e"),
        ]
        diagnosis = TopologyAgent({}).observe(events, 60)
        self.assertEqual(
            (diagnosis.fault_type, diagnosis.evidence),
            ("memory_leak", ("ev-m",)),
        )

    def test_latency_with_pool_log_is_pool_exhaustion(self):
        events = [
            metric("db", "latency_ms", 2000, id="ev-l"),
            metric("db", "error_rate", 0.5, id="ev-e"),
            log("db", "warn", POOL_MESSAGE, id="ev-p"),
        ]
        diagnosis = TopologyAgent({}).observe(events, 60)
        self.assertEqual(diagnosis.fault_type, "pool_exhaustion")
        self.assertEqual(diagnosis.evidence, ("ev-l", "ev-p"))

    def test_latency_without_pool_log_is_dependency_timeout(self):
        diagnosis = TopologyAgent({}).observe(
            [metric("db", "latency_ms", 2000, id="ev-l")], 60
        )
        self.assertEqual(
            (diagnosis.fault_type, diagnosis.evidence),
            ("dependency_timeout", ("ev-l",)),
        )

    def test_error_with_deploy_is_bad_deploy(self):
        events = [
            deploy("db", t=60, id="ev-d"),
            metric("db", "error_rate", 0.3, id="ev-e"),
        ]
        diagnosis = TopologyAgent({}).observe(events, 60)
        self.assertEqual(diagnosis.fault_type, "bad_deploy")
        self.assertEqual(diagnosis.evidence, ("ev-e", "ev-d"))

    def test_error_without_deploy_is_config_error(self):
        diagnosis = TopologyAgent({}).observe(
            [metric("db", "error_rate", 0.3, id="ev-e")], 60
        )
        self.assertEqual(
            (diagnosis.fault_type, diagnosis.evidence),
            ("config_error", ("ev-e",)),
        )

    def test_latest_deploy_is_cited(self):
        events = [
            deploy("db", t=60, id="ev-d1"),
            deploy("db", t=120, id="ev-d2"),
            metric("db", "error_rate", 0.3, t=120, id="ev-e"),
        ]
        diagnosis = TopologyAgent({}).observe(events, 120)
        self.assertEqual(diagnosis.evidence, ("ev-e", "ev-d2"))

    def test_latest_pool_log_is_cited(self):
        events = [
            log("db", "warn", POOL_MESSAGE, t=60, id="ev-p1"),
            log("db", "warn", POOL_MESSAGE, t=120, id="ev-p2"),
            metric("db", "latency_ms", 2000, t=120, id="ev-l"),
        ]
        diagnosis = TopologyAgent({}).observe(events, 120)
        self.assertEqual(diagnosis.evidence, ("ev-l", "ev-p2"))


class SelectionTest(unittest.TestCase):
    def test_picks_the_deepest_anomalous_service(self):
        events = [
            metric("api", "error_rate", 0.3, id="ev-a"),
            metric("db", "error_rate", 0.3, id="ev-d"),
        ]
        diagnosis = TopologyAgent({"api": ["db"]}).observe(events, 60)
        self.assertEqual(diagnosis.service, "db")

    def test_ignores_non_anomalous_callees(self):
        events = [
            metric("api", "error_rate", 0.3, id="ev-a"),
            metric("db", "error_rate", 0.01, id="ev-d"),
        ]
        diagnosis = TopologyAgent({"api": ["db"]}).observe(events, 60)
        self.assertEqual(diagnosis.service, "api")

    def test_three_level_chain_picks_the_leaf(self):
        events = [
            metric("api", "error_rate", 0.3, id="ev-a"),
            metric("db", "error_rate", 0.3, id="ev-d"),
            metric("cache", "error_rate", 0.3, id="ev-c"),
        ]
        diagnosis = TopologyAgent({"api": ["db"], "db": ["cache"]}).observe(events, 60)
        self.assertEqual(diagnosis.service, "cache")

    def test_cycle_falls_back_to_all_anomalous(self):
        events = [
            metric("a", "error_rate", 0.3, id="ev-a"),
            metric("b", "error_rate", 0.3, id="ev-b"),
        ]
        diagnosis = TopologyAgent({"a": ["b"], "b": ["a"]}).observe(events, 60)
        self.assertEqual(diagnosis.service, "a")

    def test_ties_sort_by_service_name(self):
        events = [
            metric("db", "memory_mb", 950, id="ev-d"),
            metric("api", "memory_mb", 950, id="ev-a"),
        ]
        diagnosis = TopologyAgent({}).observe(events, 60)
        self.assertEqual(
            (diagnosis.service, diagnosis.evidence), ("api", ("ev-a",))
        )


class StateTest(unittest.TestCase):
    def test_state_persists_across_calls(self):
        agent = TopologyAgent({"api": ["db"]})
        self.assertIsNone(
            agent.observe([metric("api", "error_rate", 0.1, t=60, id="ev-a1")], 60)
        )
        diagnosis = agent.observe(
            [
                metric("api", "error_rate", 0.3, t=120, id="ev-a2"),
                metric("db", "memory_mb", 950, t=120, id="ev-d"),
            ],
            120,
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.fault_type), ("db", "memory_leak")
        )

    def test_newer_metric_replaces_the_value(self):
        agent = TopologyAgent({})
        self.assertIsNone(
            agent.observe([metric("db", "memory_mb", 500, t=60, id="ev-m1")], 60)
        )
        diagnosis = agent.observe(
            [metric("db", "memory_mb", 950, t=120, id="ev-m2")], 120
        )
        self.assertEqual(diagnosis.evidence, ("ev-m2",))

    def test_older_metric_does_not_replace_the_latest(self):
        agent = TopologyAgent({})
        agent.observe([metric("db", "error_rate", 0.3, t=120, id="ev-new")], 120)
        diagnosis = agent.observe(
            [metric("db", "error_rate", 0.01, t=60, id="ev-old")], 180
        )
        self.assertEqual(
            (diagnosis.service, diagnosis.evidence), ("db", ("ev-new",))
        )


class RegistryTest(unittest.TestCase):
    def test_agent_names(self):
        self.assertEqual(AGENT_NAMES, ("last_deploy", "threshold", "topology"))

    def test_make_agent_returns_the_matching_class(self):
        self.assertIsInstance(make_agent("last_deploy"), LastDeployAgent)
        self.assertIsInstance(make_agent("threshold"), ThresholdAgent)
        self.assertIsInstance(make_agent("topology"), TopologyAgent)

    def test_make_agent_returns_fresh_instances(self):
        for name in AGENT_NAMES:
            with self.subTest(name=name):
                self.assertIsNot(make_agent(name), make_agent(name))

    def test_topology_receives_calls(self):
        agent = make_agent("topology", {"api": ["db"]})
        events = [
            metric("api", "error_rate", 0.3, id="ev-a"),
            metric("db", "error_rate", 0.3, id="ev-d"),
        ]
        self.assertEqual(agent.observe(events, 60).service, "db")

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            make_agent("oracle")


if __name__ == "__main__":
    unittest.main()
