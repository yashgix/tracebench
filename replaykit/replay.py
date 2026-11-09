"""Replay an incident to an agent in time order, hiding truth and the future."""

import copy
from dataclasses import dataclass

from .model import Diagnosis


@dataclass
class RunResult:
    """What one agent did on one incident."""

    diagnosis: Diagnosis | None = None
    diagnosed_at: int | None = None
    error: str | None = None
    calls: int = 0


def _windows(duration, step):
    """Yield observe times: step, 2*step, ... plus a final call at duration."""
    now = step
    while now < duration:
        yield now
        now += step
    yield duration


def replay(incident, agent, step=60):
    """Feed *incident* to *agent* in time order and collect its first diagnosis."""
    if not isinstance(step, int) or isinstance(step, bool) or step <= 0:
        raise ValueError(f"step must be a positive int, got {step!r}")
    events = incident.events
    result = RunResult()
    index = 0
    for now in _windows(incident.duration, step):
        batch = []
        while index < len(events) and events[index]["t"] <= now:
            batch.append(copy.deepcopy(events[index]))
            index += 1
        result.calls += 1
        try:
            outcome = agent.observe(batch, now)
        except Exception as exc:  # the agent is untrusted code
            result.error = f"{type(exc).__name__}: {exc}"
            return result
        if outcome is None:
            continue
        if not isinstance(outcome, Diagnosis):
            result.error = "invalid diagnosis"
            return result
        result.diagnosis = outcome
        result.diagnosed_at = now
        return result
    return result
