"""Deterministic fault injection: scenario + fault + seed -> Incident."""

import json
import random
from collections import deque
from dataclasses import dataclass

from .model import FAULT_TYPES

FAULT_KEYS = ("type", "service", "start")
OPTIONAL_FAULT_KEYS = ("cascade", "noise")
CASCADE_TYPES = ("dependency_timeout", "pool_exhaustion")
NOISE_KEYS = ("decoy_deploy", "flapping_alerts")
NOISE_RULES = ("disk_usage_high", "cert_expiring", "cpu_throttled")
SAMPLE_SECONDS = 60
POOL_MESSAGE = "connection pool exhausted (50/50 in use)"
ALERT_RULES = {
    "bad_deploy": "error_rate_high",
    "config_error": "error_rate_high",
    "dependency_timeout": "latency_high",
    "memory_leak": "memory_high",
    "pool_exhaustion": "latency_high",
}


@dataclass(frozen=True)
class Truth:
    """The injected root cause and the ids of the events the fault produced."""

    service: str
    fault_type: str
    start: int
    injected_ids: frozenset
    noise_ids: frozenset = frozenset()
    decoy_service: str | None = None


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
    keys = set(fault)
    missing = set(FAULT_KEYS) - keys
    if missing:
        raise ValueError(f"fault must have exactly the keys {sorted(FAULT_KEYS)}")
    extra = keys - set(FAULT_KEYS) - set(OPTIONAL_FAULT_KEYS)
    if extra:
        raise ValueError(f"fault has unknown keys {sorted(extra)}")
    if fault["type"] not in FAULT_TYPES:
        raise ValueError(f"unknown fault type {fault['type']!r}")
    if fault["service"] not in scenario["services"]:
        raise ValueError(f"unknown fault service {fault['service']!r}")
    start = fault["start"]
    if not isinstance(start, int) or isinstance(start, bool):
        raise ValueError("fault start must be an int")
    if not 0 < start < scenario["duration"]:
        raise ValueError(f"fault start must be in 1..{scenario['duration'] - 1}")
    cascade = fault.get("cascade", False)
    if not isinstance(cascade, bool):
        raise ValueError("fault cascade must be a bool")
    if cascade and fault["type"] not in CASCADE_TYPES:
        raise ValueError(f"cascade is not supported for fault type {fault['type']!r}")
    noise = fault.get("noise", {})
    if not isinstance(noise, dict):
        raise ValueError("fault noise must be an object")
    unknown = set(noise) - set(NOISE_KEYS)
    if unknown:
        raise ValueError(f"fault noise has unknown keys {sorted(unknown)}")
    decoy = noise.get("decoy_deploy", False)
    if not isinstance(decoy, bool):
        raise ValueError("noise decoy_deploy must be a bool")
    flapping = noise.get("flapping_alerts", 0)
    if not isinstance(flapping, int) or isinstance(flapping, bool):
        raise ValueError("noise flapping_alerts must be an int")
    if not 0 <= flapping <= 20:
        raise ValueError("noise flapping_alerts must be in 0..20")
    if (decoy or flapping) and len(scenario["services"]) < 2:
        raise ValueError("noise requires a scenario with at least two services")


def _event(t, kind, service, attrs):
    return {"id": "", "t": t, "kind": kind, "service": service, "attrs": attrs}


def _metric(t, service, name, value):
    return _event(t, "metric", service, {"name": name, "value": value})


def _version(rng):
    return "r" + "".join(rng.choice("0123456789abcdef") for _ in range(6))


def _cascade_hops(scenario, target):
    reverse = {}
    for caller, callees in scenario["calls"].items():
        for callee in callees:
            reverse.setdefault(callee, []).append(caller)
    hops = {}
    queue = deque()
    for caller in reverse.get(target, []):
        if caller != target and caller not in hops:
            hops[caller] = 1
            queue.append(caller)
    while queue:
        service = queue.popleft()
        for caller in reverse.get(service, []):
            if caller != target and caller not in hops:
                hops[caller] = hops[service] + 1
                queue.append(caller)
    return hops


def _sample_entries(rng, scenario, fault, samples, leak_positions, cascade_hops):
    """Return (event, injected) pairs for every generated metric, in rng order."""
    fault_type = fault["type"]
    target = fault["service"]
    start = fault["start"]
    cascade = bool(fault.get("cascade", False))
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
                elif fault_type == "pool_exhaustion" and t >= start:
                    latency, latency_hit = rng.uniform(1500.0, 3000.0), True
                    if t >= start + SAMPLE_SECONDS:
                        error_rate, error_hit = rng.uniform(0.1, 0.3), True
                elif fault_type == "memory_leak" and t >= start:
                    memory, memory_hit = 420 + 40 * (leak_positions[t] + 1), True
                    if memory >= 1000 and oom_time is None:
                        oom_time = t
                    if oom_time is not None:
                        error_rate, error_hit = rng.uniform(0.1, 0.3), True
            elif cascade and cascade_hops.get(service) and t >= start:
                hop = cascade_hops[service]
                latency, latency_hit = (
                    rng.uniform(4000 * hop + 1000, 4000 * hop + 4000),
                    True,
                )
                error_rate, error_hit = rng.uniform(0.2, 0.4), True
            elif service in callers and t >= start + SAMPLE_SECONDS:
                if fault_type in ("bad_deploy", "config_error"):
                    error_rate, error_hit = rng.uniform(0.05, 0.15), True
                elif fault_type in ("dependency_timeout", "pool_exhaustion"):
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
    if fault_type == "pool_exhaustion":
        for t in samples:
            if t >= start:
                entries.append(
                    (
                        _event(t, "log", target, {"level": "warn", "message": POOL_MESSAGE}),
                        True,
                    )
                )
    elif fault_type in messages:
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


def _noise_entries(seed, scenario, fault, samples):
    noise = fault.get("noise", {})
    decoy = noise.get("decoy_deploy", False)
    flapping = noise.get("flapping_alerts", 0)
    if not decoy and not flapping:
        return [], None
    rng = random.Random(f"{seed}:noise")
    target = fault["service"]
    start = fault["start"]
    others = [name for name in scenario["services"] if name != target]
    entries = []
    decoy_service = None
    if decoy:
        decoy_service = rng.choice(others)
        entries.append(
            _event(max(0, start - 120), "deploy", decoy_service, {"version": _version(rng)})
        )
    for _ in range(flapping):
        rule = rng.choice(NOISE_RULES)
        service = rng.choice(others)
        t = rng.choice(samples)
        entries.append(_event(t, "alert", service, {"rule": rule, "severity": "ticket"}))
    return entries, decoy_service


def inject(scenario, fault, seed):
    """Build a deterministic incident from *scenario*, *fault* and *seed*."""
    validate_fault(scenario, fault)
    rng = random.Random(seed)
    duration = scenario["duration"]
    samples = list(range(0, duration + 1, SAMPLE_SECONDS))
    leak_positions = {}
    if fault["type"] == "memory_leak":
        leak_positions = {
            t: i for i, t in enumerate(s for s in samples if s >= fault["start"])
        }

    cascade_hops = {}
    if fault.get("cascade", False):
        cascade_hops = _cascade_hops(scenario, fault["service"])

    entries, oom_time = _sample_entries(
        rng, scenario, fault, samples, leak_positions, cascade_hops
    )
    entries.extend(_fault_entries(rng, fault, samples, duration, oom_time))

    noise_events, decoy_service = _noise_entries(seed, scenario, fault, samples)
    for event in noise_events:
        entries.append((event, False))

    for event in scenario["events"]:
        background = _event(
            event["t"], event["kind"], event["service"], dict(event["attrs"])
        )
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
    noise_ids = frozenset(event["id"] for event in noise_events)

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
            noise_ids=noise_ids,
            decoy_service=decoy_service,
        ),
    )
