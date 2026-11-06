"""Deterministic fault injection: scenario + fault + seed -> Incident."""

import json
import random
from dataclasses import dataclass

from .model import FAULT_TYPES

FAULT_KEYS = ("type", "service", "start")
SAMPLE_SECONDS = 60
ALERT_RULES = {
    "bad_deploy": "error_rate_high",
    "config_error": "error_rate_high",
    "dependency_timeout": "latency_high",
    "memory_leak": "memory_high",
}


@dataclass(frozen=True)
class Truth:
    """The injected root cause and the ids of the events the fault produced."""

    service: str
    fault_type: str
    start: int
    injected_ids: frozenset


@dataclass
class Incident:
    """A scenario plus an injected fault, ready to be replayed."""

    id: str
    scenario_id: str
    duration: int
    services: list
    calls: dict
    events: list
    truth: Truth


def validate_fault(scenario, fault):
    """Raise ValueError unless *fault* is a well-formed fault for *scenario*."""
    if not isinstance(fault, dict):
        raise ValueError("fault must be a JSON object")
    if set(fault) != set(FAULT_KEYS):
        raise ValueError(f"fault must have exactly the keys {sorted(FAULT_KEYS)}")
    if fault["type"] not in FAULT_TYPES:
        raise ValueError(f"unknown fault type {fault['type']!r}")
    if fault["service"] not in scenario["services"]:
        raise ValueError(f"unknown fault service {fault['service']!r}")
    start = fault["start"]
    if not isinstance(start, int) or isinstance(start, bool):
        raise ValueError("fault start must be an int")
    if not 0 < start < scenario["duration"]:
        raise ValueError(f"fault start must be in 1..{scenario['duration'] - 1}")


def _event(t, kind, service, attrs):
    return {"id": "", "t": t, "kind": kind, "service": service, "attrs": attrs}


def _metric(t, service, name, value):
    return _event(t, "metric", service, {"name": name, "value": value})


def _version(rng):
    return "r" + "".join(rng.choice("0123456789abcdef") for _ in range(6))


def _sample_entries(rng, scenario, fault, samples, leak_positions):
    """Return (event, injected) pairs for every generated metric, in rng order."""
    fault_type = fault["type"]
    target = fault["service"]
    start = fault["start"]
    callers = [name for name, callees in scenario["calls"].items() if target in callees]
    entries = []
    oom_time = None
    for t in samples:
        for service in scenario["services"]:
            error_rate = rng.uniform(0.0, 0.02)
            latency = rng.uniform(80.0, 120.0)
            memory = rng.uniform(400.0, 420.0)
            error_hit = latency_hit = memory_hit = False
            if service == target:
                if fault_type in ("bad_deploy", "config_error") and t >= start:
                    error_rate, error_hit = rng.uniform(0.25, 0.5), True
                elif fault_type == "dependency_timeout" and t >= start:
                    latency, latency_hit = rng.uniform(2000.0, 5000.0), True
                elif fault_type == "memory_leak" and t >= start:
                    memory, memory_hit = 420 + 40 * (leak_positions[t] + 1), True
                    if memory >= 1000 and oom_time is None:
                        oom_time = t
                    if oom_time is not None:
                        error_rate, error_hit = rng.uniform(0.1, 0.3), True
            elif service in callers and t >= start + SAMPLE_SECONDS:
                if fault_type in ("bad_deploy", "config_error"):
                    error_rate, error_hit = rng.uniform(0.05, 0.15), True
                elif fault_type == "dependency_timeout":
                    latency, latency_hit = rng.uniform(800.0, 1500.0), True
                    error_rate, error_hit = rng.uniform(0.05, 0.15), True
            entries.append((_metric(t, service, "error_rate", error_rate), error_hit))
            entries.append((_metric(t, service, "latency_ms", latency), latency_hit))
            entries.append((_metric(t, service, "memory_mb", memory), memory_hit))
    return entries, oom_time


def _fault_entries(rng, fault, samples, duration, oom_time):
    """Return the extra events the fault adds: deploys, logs and the alert."""
    fault_type = fault["type"]
    target = fault["service"]
    start = fault["start"]
    entries = []
    if fault_type == "bad_deploy":
        entries.append((_event(start, "deploy", target, {"version": _version(rng)}), True))
    elif fault_type == "config_error":
        entry = _event(start, "log", target, {"level": "info", "message": "config reloaded"})
        entries.append((entry, True))

    messages = {
        "bad_deploy": ("error", "request failed status=500"),
        "config_error": ("error", "invalid configuration key"),
        "dependency_timeout": ("warn", "query exceeded 2000ms"),
    }
    if fault_type in messages:
        level, message = messages[fault_type]
        for t in samples:
            if t >= start:
                entries.append((_event(t, "log", target, {"level": level, "message": message}), True))
    elif oom_time is not None:
        entry = _event(oom_time, "log", target, {"level": "error", "message": "OOMKilled"})
        entries.append((entry, True))

    alert_time = oom_time if fault_type == "memory_leak" else start + 2 * SAMPLE_SECONDS
    if alert_time is not None and alert_time <= duration:
        attrs = {"rule": ALERT_RULES[fault_type], "severity": "page"}
        entries.append((_event(alert_time, "alert", target, attrs), True))
    return entries


def inject(scenario, fault, seed):
    """Build a deterministic incident from *scenario*, *fault* and *seed*."""
    validate_fault(scenario, fault)
    rng = random.Random(seed)
    duration = scenario["duration"]
    samples = list(range(0, duration + 1, SAMPLE_SECONDS))
    leak_positions = {}
    if fault["type"] == "memory_leak":
        leak_positions = {t: i for i, t in enumerate(s for s in samples if s >= fault["start"])}

    entries, oom_time = _sample_entries(rng, scenario, fault, samples, leak_positions)
    entries.extend(_fault_entries(rng, fault, samples, duration, oom_time))
    for event in scenario["events"]:
        background = _event(event["t"], event["kind"], event["service"], dict(event["attrs"]))
        entries.append((background, False))

    entries.sort(
        key=lambda entry: (
            entry[0]["t"],
            entry[0]["kind"],
            entry[0]["service"],
            json.dumps(entry[0]["attrs"], sort_keys=True),
        )
    )
    events = []
    injected_ids = set()
    for index, (event, injected) in enumerate(entries, start=1):
        event["id"] = f"ev-{index:04d}"
        events.append(event)
        if injected:
            injected_ids.add(event["id"])

    return Incident(
        id=f"{scenario['id']}:{fault['type']}:{fault['service']}:{seed}",
        scenario_id=scenario["id"],
        duration=duration,
        services=list(scenario["services"]),
        calls={name: list(callees) for name, callees in scenario["calls"].items()},
        events=events,
        truth=Truth(
            service=fault["service"],
            fault_type=fault["type"],
            start=fault["start"],
            injected_ids=frozenset(injected_ids),
        ),
    )
