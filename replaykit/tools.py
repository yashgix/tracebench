"""Evidence tools an investigator calls: incident data up to the page, never truth or later events."""

from .model import LOG_LEVELS, METRIC_NAMES

TOOLS = ("topology", "overview", "alerts", "metrics", "logs", "deploys")
LAST = 20


def _trigger(events):
    pages = [event for event in events if event["kind"] == "alert" and event["attrs"]["severity"] == "page"]
    return min(pages, key=lambda event: (event["t"], event["id"])) if pages else None


class Evidence:
    """Tool access to one incident as of the time of its first page."""

    METRICS, LEVELS = METRIC_NAMES, LOG_LEVELS

    def __init__(self, incident):
        page = _trigger(incident.events)
        self.now = page["t"] if page else incident.duration
        self.trigger = (
            {"id": page["id"], "t": page["t"], "service": page["service"], "rule": page["attrs"]["rule"]}
            if page
            else None
        )
        self.observed = {page["id"]} if page else set()
        self._services = list(incident.services)
        self._calls = {name: list(callees) for name, callees in incident.calls.items()}
        visible = [event for event in incident.events if event["t"] <= self.now]
        self._events = sorted(visible, key=lambda event: (event["t"], event["id"]))

    def run(self, call):
        """Run one tool call and return its result; event ids in it become observed."""
        tool = call["tool"]
        try:
            result, ids = self._dispatch(tool, call)
        except ValueError as exc:
            return {"tool": tool, "error": str(exc)}
        self.observed.update(ids)
        return result

    def _dispatch(self, tool, call):
        if tool == "topology":
            return {"tool": tool, "services": list(self._services), "calls": dict(self._calls)}, []
        if tool == "overview":
            return self._overview()
        if tool == "metrics":
            return self._metrics(self._service(call["service"]), call["metric"])
        if tool in ("alerts", "logs", "deploys"):
            return self._listing(tool, self._service(call["service"], wildcard=True), call["level"])
        raise ValueError(f"unknown tool {tool!r}")

    def _service(self, service, wildcard=False):
        if service == "*" and wildcard:
            return service
        if service not in self._services:
            raise ValueError(f"unknown service {service!r}")
        return service

    def _of(self, kind, service):
        return [e for e in self._events if e["kind"] == kind and service in ("*", e["service"])]

    def _overview(self):
        latest = {}
        for event in self._of("metric", "*"):
            point = [event["id"], event["t"], round(event["attrs"]["value"], 3)]
            latest.setdefault(event["service"], {})[event["attrs"]["name"]] = point
        ids = [point[0] for metrics in latest.values() for point in metrics.values()]
        return {"tool": "overview", "services": latest}, ids

    def _metrics(self, service, metric):
        if metric not in self.METRICS:
            raise ValueError(f"unknown metric {metric!r}")
        events = [e for e in self._of("metric", service) if e["attrs"]["name"] == metric][-LAST:]
        points = [[e["id"], e["t"], round(e["attrs"]["value"], 3)] for e in events]
        return {"tool": "metrics", "service": service, "metric": metric, "points": points}, [p[0] for p in points]

    def _listing(self, tool, service, level):
        if tool == "alerts":
            rows = [
                [e["id"], e["t"], e["service"], e["attrs"]["rule"], e["attrs"]["severity"]]
                for e in self._of("alert", service)
            ][-LAST:]
            return {"tool": tool, "service": service, "alerts": rows}, [row[0] for row in rows]
        if tool == "deploys":
            rows = [[e["id"], e["t"], e["service"], e["attrs"]["version"]] for e in self._of("deploy", service)]
            return {"tool": tool, "service": service, "deploys": rows}, [row[0] for row in rows]
        if level != "*" and level not in self.LEVELS:
            raise ValueError(f"unknown level {level!r}")
        rows = [self._log_row(e) for e in self._of("log", service) if level in ("*", e["attrs"]["level"])][-LAST:]
        return {"tool": tool, "service": service, "level": level, "logs": rows}, [row[0] for row in rows]

    @staticmethod
    def _log_row(event):
        return [event["id"], event["t"], event["service"], event["attrs"]["level"], event["attrs"]["message"]]
