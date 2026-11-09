"""A bounded investigation: a model picks evidence each turn, our code runs the tools, the model decides."""

import hashlib
import json
from dataclasses import dataclass, field

from .baselines import make_agent
from .model import FAULT_TYPES, _is_int
from .tools import Evidence

ACTION_KEYS = ("act", "calls", "service", "fault_type", "confidence", "evidence", "summary")
CALL_KEYS = ("tool", "service", "metric", "level")
ACTS = ("call", "diagnose", "escalate", "dismiss")
REPLY_KEYS = ("kind", "action", "input_tokens", "output_tokens", "cost_microusd", "latency_ms")


@dataclass(frozen=True)
class Limits:
    turns: int = 10
    tool_calls: int = 15
    tokens: int = 100_000
    model_ms: int = 300_000


@dataclass(frozen=True)
class Reply:
    """What a model returns for one turn."""

    kind: str
    action: dict | None
    input_tokens: int
    output_tokens: int
    cost_microusd: int
    latency_ms: int


@dataclass(frozen=True)
class Investigation:
    decision: dict | None
    outcome: str
    now: int
    turns: int
    tool_calls: int
    observed: frozenset
    input_tokens: int = 0
    output_tokens: int = 0
    cost_microusd: int = 0
    latency_ms: int = 0
    transcript: list = field(default_factory=list)


def _check_shape(action):
    if not isinstance(action, dict) or set(action) != set(ACTION_KEYS):
        raise ValueError(f"action must have exactly the keys {list(ACTION_KEYS)}")
    if action["act"] not in ACTS:
        raise ValueError(f"unknown act {action['act']!r}")
    if not all(isinstance(action[key], str) for key in ("service", "fault_type", "summary")):
        raise ValueError("service, fault_type and summary must be strings")
    if not _is_int(action["confidence"]) or not 0 <= action["confidence"] <= 100:
        raise ValueError("confidence must be an int in 0..100")
    evidence = action["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 8 or not all(isinstance(i, str) for i in evidence):
        raise ValueError("evidence must be a list of at most 8 strings")
    calls = action["calls"]
    if not isinstance(calls, list) or len(calls) > 3:
        raise ValueError("calls must be a list of at most 3 tool calls")
    for call in calls:
        if not isinstance(call, dict) or set(call) != set(CALL_KEYS):
            raise ValueError(f"a call must have exactly the keys {list(CALL_KEYS)}")
        if not all(isinstance(call[key], str) for key in CALL_KEYS):
            raise ValueError("call fields must be strings")


def parse_action(action, services):
    """Return *action* if it is a valid action for an incident with *services*; raise ValueError otherwise."""
    _check_shape(action)
    act = action["act"]
    if act == "call":
        if not action["calls"]:
            raise ValueError("a call turn needs 1 to 3 calls")
        return action
    if action["calls"]:
        raise ValueError(f"{act} must not have calls")
    if act == "diagnose":
        if action["service"] not in services:
            raise ValueError(f"unknown service {action['service']!r}")
        if action["fault_type"] not in FAULT_TYPES:
            raise ValueError(f"unknown fault type {action['fault_type']!r}")
        if not action["evidence"]:
            raise ValueError("a diagnosis needs 1 to 8 evidence ids")
    elif act == "escalate" and action["service"] != "-" and action["service"] not in services:
        raise ValueError(f"unknown service {action['service']!r}")
    return action


def packet_sha256(packet):
    return hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _Run:
    """The mutable state of one investigation."""

    def __init__(self, incident, limits, evidence):
        self.incident, self.limits = incident, limits
        self.evidence = evidence(incident)
        self.history, self.transcript = [], []
        self.turns = self.tool_calls = self.tokens_in = self.tokens_out = self.cost = self.latency = 0

    def exhausted(self):
        return (
            self.turns >= self.limits.turns
            or self.tokens_in + self.tokens_out >= self.limits.tokens
            or self.latency >= self.limits.model_ms
        )

    def ask(self, model):
        self.turns += 1
        packet = {"history": list(self.history), "now": self.evidence.now, "trigger": self.evidence.trigger}
        digest = packet_sha256(packet)
        reply = model(self.incident.id, self.turns, packet)
        self.tokens_in += reply.input_tokens
        self.tokens_out += reply.output_tokens
        self.cost += reply.cost_microusd
        self.latency += reply.latency_ms
        self.transcript.append(
            {
                "turn": self.turns,
                "packet_sha256": digest,
                "kind": reply.kind,
                "action": reply.action,
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
                "cost_microusd": reply.cost_microusd,
                "latency_ms": reply.latency_ms,
            }
        )
        return reply

    def note(self, entry):
        entry.update(
            turn=self.turns,
            turns_left=self.limits.turns - self.turns,
            tool_calls_left=self.limits.tool_calls - self.tool_calls,
        )
        self.history.append(entry)

    def run_calls(self, calls):
        results = []
        for call in calls:
            if self.tool_calls >= self.limits.tool_calls:
                results.append({"tool": call["tool"], "error": "tool budget exhausted"})
                continue
            self.tool_calls += 1
            results.append(self.evidence.run(call))
        self.note({"act": "call", "calls": calls, "results": results})

    def finish(self, decision, outcome):
        return Investigation(
            decision,
            outcome,
            self.evidence.now,
            self.turns,
            self.tool_calls,
            frozenset(self.evidence.observed),
            self.tokens_in,
            self.tokens_out,
            self.cost,
            self.latency,
            self.transcript,
        )


def investigate(incident, model, limits=Limits(), evidence=Evidence, parse=parse_action):
    """Let *model* investigate *incident* within *limits*, with *evidence*'s tools and *parse*'s action schema."""
    run = _Run(incident, limits, evidence)
    invalid = 0
    while not run.exhausted():
        reply = run.ask(model)
        if reply.kind not in ("ok", "invalid"):
            return run.finish(None, f"model_{reply.kind}")
        try:
            if reply.kind == "invalid":
                raise ValueError("the reply did not match the action schema")
            action = parse(reply.action, incident.services)
        except ValueError as exc:
            invalid += 1
            run.note({"act": "invalid", "error": str(exc)})
            if invalid == 2:
                return run.finish(None, "invalid_output")
            continue
        invalid = 0
        if action["act"] != "call":
            return run.finish(action, "decided")
        run.run_calls(action["calls"])
    return run.finish(None, "budget_exhausted")


def baseline_investigation(incident, name):
    """A milestone-2 baseline agent's answer as an investigation, from all events up to the page."""
    evidence = Evidence(incident)
    events = [dict(event) for event in incident.events if event["t"] <= evidence.now]
    diagnosis = make_agent(name, incident.calls).observe(events, evidence.now)
    if diagnosis is None:
        decision = {
            "act": "dismiss",
            "calls": [],
            "service": "-",
            "fault_type": "-",
            "confidence": 0,
            "evidence": [],
            "summary": "baseline",
        }
    else:
        decision = {
            "act": "diagnose",
            "calls": [],
            "service": diagnosis.service,
            "fault_type": diagnosis.fault_type,
            "confidence": round(diagnosis.confidence * 100),
            "evidence": list(diagnosis.evidence),
            "summary": "baseline",
        }
    return Investigation(decision, "decided", evidence.now, 0, 0, frozenset(e["id"] for e in events))
