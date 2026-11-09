"""Action schema v4: M3's acts plus `patch`, code fields in a decision, and one `arg` per tool call."""

from ..model import _is_int
from .patch import check_patch

ACTION_KEYS = ("act", "calls", "service", "fault_type", "commit", "file", "function", "confidence", "evidence",
               "summary", "patch")
CALL_KEYS = ("tool", "service", "arg")
ACTS = ("call", "diagnose", "patch", "escalate", "dismiss")
FAULT_TYPES = ("resource_leak", "config_error", "slow_query", "code_defect")
TEXT_KEYS = ("service", "fault_type", "commit", "file", "function", "summary", "patch")


def _check_shape(action):
    if not isinstance(action, dict) or set(action) != set(ACTION_KEYS):
        raise ValueError(f"action must have exactly the keys {list(ACTION_KEYS)}")
    if action["act"] not in ACTS:
        raise ValueError(f"unknown act {action['act']!r}")
    if not all(isinstance(action[key], str) for key in TEXT_KEYS):
        raise ValueError(f"{', '.join(TEXT_KEYS)} must be strings")
    if not _is_int(action["confidence"]) or not 0 <= action["confidence"] <= 100:
        raise ValueError("confidence must be an int in 0..100")
    evidence = action["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 8 or not all(isinstance(i, str) for i in evidence):
        raise ValueError("evidence must be a list of at most 8 strings")
    calls = action["calls"]
    if not isinstance(calls, list) or len(calls) > 3:
        raise ValueError("calls must be a list of at most 3 tool calls")
    for call in calls:
        if not isinstance(call, dict) or set(call) != set(CALL_KEYS) or not all(isinstance(call[k], str)
                                                                                 for k in CALL_KEYS):
            raise ValueError(f"a call must have exactly the string keys {list(CALL_KEYS)}")


def parse_action(action, services):
    """Return *action* if it is a valid v4 action for an incident with *services*; raise ValueError otherwise."""
    _check_shape(action)
    act = action["act"]
    if act == "call":
        if not action["calls"]:
            raise ValueError("a call turn needs 1 to 3 calls")
    elif action["calls"]:
        raise ValueError(f"{act} must not have calls")
    if act != "patch" and action["patch"]:
        raise ValueError(f"{act} must have an empty patch")
    if act in ("diagnose", "patch"):
        if action["service"] not in services:
            raise ValueError(f"unknown service {action['service']!r}")
        if action["fault_type"] not in FAULT_TYPES:
            raise ValueError(f"unknown fault type {action['fault_type']!r}")
        if not action["evidence"]:
            raise ValueError(f"{act} needs 1 to 8 evidence ids")
    if act == "patch":
        check_patch(action["patch"])
    elif act == "escalate" and action["service"] != "-" and action["service"] not in services:
        raise ValueError(f"unknown service {action['service']!r}")
    return action
