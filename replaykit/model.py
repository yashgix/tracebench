"""Telemetry scenario validation, loading, and the diagnosis type."""

import json
from dataclasses import dataclass

FAULT_TYPES = (
    "bad_deploy",
    "config_error",
    "dependency_timeout",
    "memory_leak",
    "pool_exhaustion",
)

EVENT_KINDS = ("metric", "log", "deploy", "alert")
METRIC_NAMES = ("error_rate", "latency_ms", "memory_mb")
LOG_LEVELS = ("info", "warn", "error")
SEVERITIES = ("page", "ticket")

SCENARIO_KEYS = ("id", "duration", "services", "calls", "events")
EVENT_KEYS = ("id", "t", "kind", "service", "attrs")
ATTRS_KEYS = {
    "metric": ("name", "value"),
    "log": ("level", "message"),
    "deploy": ("version",),
    "alert": ("rule", "severity"),
}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_keys(obj, keys, where):
    if set(obj) != set(keys):
        raise ValueError(f"{where} must have exactly the keys {sorted(keys)}")


def _validate_attrs(kind, attrs, where):
    if not isinstance(attrs, dict):
        raise ValueError(f"{where}: attrs must be an object")
    _check_keys(attrs, ATTRS_KEYS[kind], f"{where} attrs")
    if kind == "metric":
        if attrs["name"] not in METRIC_NAMES:
            raise ValueError(f"{where}: unknown metric name {attrs['name']!r}")
        if not _is_number(attrs["value"]):
            raise ValueError(f"{where}: metric value must be a number")
    elif kind == "log":
        if attrs["level"] not in LOG_LEVELS:
            raise ValueError(f"{where}: unknown log level {attrs['level']!r}")
        if not isinstance(attrs["message"], str):
            raise ValueError(f"{where}: log message must be a string")
    elif kind == "deploy":
        if not isinstance(attrs["version"], str):
            raise ValueError(f"{where}: deploy version must be a string")
    else:
        if not isinstance(attrs["rule"], str):
            raise ValueError(f"{where}: alert rule must be a string")
        if attrs["severity"] not in SEVERITIES:
            raise ValueError(f"{where}: unknown alert severity {attrs['severity']!r}")


def _validate_event(event, services, duration, where):
    if not isinstance(event, dict):
        raise ValueError(f"{where} must be an object")
    _check_keys(event, EVENT_KEYS, where)
    if not isinstance(event["id"], str) or not event["id"]:
        raise ValueError(f"{where}: id must be a non-empty string")
    where = f"event {event['id']}"
    if not _is_int(event["t"]):
        raise ValueError(f"{where}: t must be an int")
    if not 0 <= event["t"] <= duration:
        raise ValueError(f"{where}: t must be within 0..{duration}")
    if event["kind"] not in EVENT_KINDS:
        raise ValueError(f"{where}: unknown event kind {event['kind']!r}")
    if not isinstance(event["service"], str) or event["service"] not in services:
        raise ValueError(f"{where}: unknown service {event['service']!r}")
    _validate_attrs(event["kind"], event["attrs"], where)


def validate_scenario(scenario):
    """Raise ValueError unless *scenario* is a well-formed scenario object."""
    if not isinstance(scenario, dict):
        raise ValueError("scenario must be a JSON object")
    _check_keys(scenario, SCENARIO_KEYS, "scenario")
    if not isinstance(scenario["id"], str) or not scenario["id"]:
        raise ValueError("scenario id must be a non-empty string")
    duration = scenario["duration"]
    if not _is_int(duration) or duration <= 0:
        raise ValueError("scenario duration must be a positive int")
    services = scenario["services"]
    if not isinstance(services, list) or not services:
        raise ValueError("scenario services must be a non-empty list")
    if any(not isinstance(name, str) or not name for name in services):
        raise ValueError("scenario services must be non-empty strings")
    if len(set(services)) != len(services):
        raise ValueError("scenario services must be distinct")
    names = set(services)
    calls = scenario["calls"]
    if not isinstance(calls, dict):
        raise ValueError("scenario calls must be an object")
    for caller, callees in calls.items():
        if caller not in names:
            raise ValueError(f"calls: unknown caller {caller!r}")
        if not isinstance(callees, list):
            raise ValueError(f"calls: callees of {caller!r} must be a list")
        for callee in callees:
            if not isinstance(callee, str) or callee not in names:
                raise ValueError(f"calls: {caller!r} calls unknown service {callee!r}")
            if callee == caller:
                raise ValueError(f"calls: {caller!r} calls itself")
    events = scenario["events"]
    if not isinstance(events, list):
        raise ValueError("scenario events must be a list")
    seen = set()
    for index, event in enumerate(events):
        _validate_event(event, names, duration, f"event #{index}")
        if event["id"] in seen:
            raise ValueError(f"duplicate event id {event['id']!r}")
        seen.add(event["id"])


def load_scenario(path):
    """Read, validate and return the scenario at *path*, events sorted by (t, id)."""
    try:
        with open(path, encoding="utf-8") as handle:
            scenario = json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from None
    validate_scenario(scenario)
    scenario["events"].sort(key=lambda event: (event["t"], event["id"]))
    return scenario


@dataclass(frozen=True)
class Diagnosis:
    """An agent's guess at the root cause, citing event ids as evidence."""

    service: str
    fault_type: str
    confidence: float
    evidence: tuple[str, ...] = ()

    def __post_init__(self):
        if self.fault_type not in FAULT_TYPES:
            raise ValueError(f"unknown fault type {self.fault_type!r}")
        if not _is_number(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be a number in 0..1, got {self.confidence!r}")
        object.__setattr__(self, "evidence", tuple(self.evidence))
