"""Baseline agents used to compare an agent's diagnoses against simple heuristics."""

from .model import Diagnosis

POOL_MESSAGE = "connection pool exhausted (50/50 in use)"
AGENT_NAMES = ("last_deploy", "threshold", "topology")


class LastDeployAgent:
    """Diagnose bad_deploy from the most recent deploy seen."""

    def __init__(self):
        self._latest = None

    def observe(self, events, now):
        for event in events:
            if event["kind"] != "deploy":
                continue
            order = (event["t"], event["id"])
            if self._latest is None or order > self._latest[:2]:
                self._latest = (event["t"], event["id"], event["service"])
        if self._latest is None:
            return None
        return Diagnosis(
            self._latest[2],
            "bad_deploy",
            0.6,
            (self._latest[1],),
        )


class ThresholdAgent:
    """Diagnose from the first metric threshold that fires."""

    def __init__(self):
        self._metrics = {}  # service -> metric name -> (t, id, value)
        self._deploys = {}  # service -> (t, id)

    def observe(self, events, now):
        for event in events:
            kind = event["kind"]
            service = event["service"]
            if kind == "metric":
                name = event["attrs"]["name"]
                metrics = self._metrics.setdefault(service, {})
                current = metrics.get(name)
                if current is None or event["t"] >= current[0]:
                    metrics[name] = (event["t"], event["id"], event["attrs"]["value"])
            elif kind == "deploy":
                order = (event["t"], event["id"])
                current = self._deploys.get(service)
                if current is None or order > current:
                    self._deploys[service] = order

        picked = self._pick("memory_mb", 900)
        if picked is not None:
            return Diagnosis(picked[0], "memory_leak", 0.7, (picked[1],))

        picked = self._pick("latency_ms", 1500)
        if picked is not None:
            return Diagnosis(picked[0], "dependency_timeout", 0.7, (picked[1],))

        picked = self._pick("error_rate", 0.2)
        if picked is not None:
            service, metric_id = picked
            if service in self._deploys:
                evidence = (metric_id, self._deploys[service][1])
                return Diagnosis(service, "bad_deploy", 0.7, evidence)
            return Diagnosis(service, "config_error", 0.7, (metric_id,))
        return None

    def _pick(self, name, threshold):
        candidates = []
        for service, metrics in self._metrics.items():
            entry = metrics.get(name)
            if entry is not None and entry[2] >= threshold:
                candidates.append((service, entry[1], entry[2]))
        if not candidates:
            return None
        service, metric_id, _ = min(candidates, key=lambda item: (-item[2], item[0]))
        return service, metric_id


class TopologyAgent:
    """Diagnose the deepest anomalous service in the call graph."""

    def __init__(self, calls):
        self._calls = {caller: tuple(callees) for caller, callees in (calls or {}).items()}
        self._metrics = {}  # service -> metric name -> (t, id, value)
        self._deploys = {}  # service -> (t, id)
        self._pool_logs = {}  # service -> (t, id)

    def observe(self, events, now):
        for event in events:
            kind = event["kind"]
            service = event["service"]
            if kind == "metric":
                name = event["attrs"]["name"]
                metrics = self._metrics.setdefault(service, {})
                entry = (event["t"], event["id"], event["attrs"]["value"])
                current = metrics.get(name)
                if current is None or entry[:2] > current[:2]:
                    metrics[name] = entry
            elif kind == "deploy":
                entry = (event["t"], event["id"])
                current = self._deploys.get(service)
                if current is None or entry > current:
                    self._deploys[service] = entry
            elif kind == "log":
                attrs = event["attrs"]
                if attrs["level"] == "warn" and attrs["message"] == POOL_MESSAGE:
                    entry = (event["t"], event["id"])
                    current = self._pool_logs.get(service)
                    if current is None or entry > current:
                        self._pool_logs[service] = entry

        anomalous = {service for service in self._metrics if self._is_anomalous(service)}
        if not anomalous:
            return None

        candidates = [
            service for service in anomalous if not any(callee in anomalous for callee in self._calls.get(service, ()))
        ]
        if not candidates:
            candidates = list(anomalous)
        service = min(candidates)

        metrics = self._metrics[service]
        memory = metrics.get("memory_mb")
        latency = metrics.get("latency_ms")
        if memory is not None and memory[2] >= 900:
            evidence = (memory[1],)
            fault_type = "memory_leak"
        elif latency is not None and latency[2] >= 1500:
            evidence = (latency[1],)
            if service in self._pool_logs:
                fault_type = "pool_exhaustion"
                evidence += (self._pool_logs[service][1],)
            else:
                fault_type = "dependency_timeout"
        else:
            error = metrics["error_rate"]
            if service in self._deploys:
                fault_type = "bad_deploy"
                evidence = (error[1], self._deploys[service][1])
            else:
                fault_type = "config_error"
                evidence = (error[1],)
        return Diagnosis(service, fault_type, 0.8, evidence)

    def _is_anomalous(self, service):
        metrics = self._metrics[service]
        memory = metrics.get("memory_mb")
        latency = metrics.get("latency_ms")
        error = metrics.get("error_rate")
        return (
            (memory is not None and memory[2] >= 900)
            or (latency is not None and latency[2] >= 1500)
            or (error is not None and error[2] >= 0.2)
        )


def make_agent(name, calls=None):
    """Return a fresh agent instance for *name*."""
    if name == "last_deploy":
        return LastDeployAgent()
    if name == "threshold":
        return ThresholdAgent()
    if name == "topology":
        return TopologyAgent(calls)
    raise ValueError(f"unknown agent {name!r}")
