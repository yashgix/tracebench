"""The model client for live runs: one HTTP call per turn to a local broker that holds the credentials."""

import json
import urllib.request

from .investigate import REPLY_KEYS, Reply
from .model import _is_int

TIMEOUT = 900
STOPPED = Reply("stopped", None, 0, 0, 0, 0)


def _post(url, body, headers, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _reply(raw):
    answer = json.loads(raw)
    if not isinstance(answer, dict) or set(answer) != set(REPLY_KEYS) or not isinstance(answer["kind"], str):
        raise ValueError("malformed broker answer")
    if any(not _is_int(answer[key]) or answer[key] < 0 for key in REPLY_KEYS[2:]):
        raise ValueError("malformed broker answer")
    if answer["action"] is not None and not isinstance(answer["action"], dict):
        raise ValueError("malformed broker answer")
    return Reply(*(answer[key] for key in REPLY_KEYS))


class BrokerModel:
    """A model reached through the broker at *url*."""

    def __init__(self, name, url, token, post=_post):
        self.name, self.url, self.token, self.post = name, url.rstrip("/"), token, post

    def __call__(self, incident_id, turn, packet):
        body = json.dumps({"model": self.name, "incident": incident_id, "turn": turn, "packet": packet}).encode()
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        try:
            return _reply(self.post(f"{self.url}/turn", body, headers, TIMEOUT))
        except (OSError, ValueError, UnicodeDecodeError):
            return STOPPED
