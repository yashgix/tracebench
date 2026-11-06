"""Baseline agents used to compare an agent's diagnoses against simple heuristics."""

from .model import Diagnosis


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
