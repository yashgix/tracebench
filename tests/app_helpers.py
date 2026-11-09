"""Shared fixtures for the milestone-4 app tests."""

import difflib
import os
import unittest
from functools import cache
from pathlib import Path

from replaykit.app import cases, world
from replaykit.app.bundle import bundle
from replaykit.app.incident import run_case
from replaykit.app.patch import LocalRunner
from replaykit.investigate import Reply

# The shop repository: $TRACEBENCH_SHOP, or a `tracebench-shop` clone next to this one.
REPO = Path(os.environ.get("TRACEBENCH_SHOP", Path(__file__).resolve().parents[2] / "tracebench-shop")).resolve()
FIX = (Path(__file__).resolve().parents[1] / "replaykit" / "app" / "protected" / "leak.patch").read_text()
needs_repo = unittest.skipUnless(REPO.exists(), "needs the shop app repository")
RUNNER = LocalRunner()


@cache
def leak_incident(seed=1):
    run = cases.case("leak", seed)
    return bundle(REPO, run, run_case(REPO, run))


def source(commit, path):
    return dict(world.blobs(REPO, commit))[Path(path)].decode()


def edit(commit, path, old, new, *more):
    """A unified diff that replaces *old* with *new* (and each further old, new pair) in *path* at *commit*."""
    before = after = source(commit, path)
    pairs = (old, new, *more)
    for old, new in zip(pairs[::2], pairs[1::2]):
        if after.count(old) != 1:
            raise AssertionError(f"{old!r} must occur once in {path}")
        after = after.replace(old, new)
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}"))


EMPTY = {
    "calls": [],
    "service": "-",
    "fault_type": "-",
    "commit": "-",
    "file": "-",
    "function": "-",
    "confidence": 0,
    "evidence": [],
    "summary": "checking",
    "patch": "",
}


def calls(*triples):
    return {**EMPTY, "act": "call", "calls": [dict(zip(("tool", "service", "arg"), t)) for t in triples]}


def result(packet, tool):
    """The latest result of *tool* in the packet's history."""
    return [r for entry in packet["history"] if entry["act"] == "call" for r in entry["results"] if r["tool"] == tool][
        -1
    ]


def scripted(*steps):
    """A model that answers turn n with steps[n-1](packet), or the action itself."""

    def model(incident_id, turn, packet):
        step = steps[turn - 1]
        return Reply("ok", step(packet) if callable(step) else step, 1000, 100, 10, 50)

    return model
