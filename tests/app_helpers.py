"""Shared fixtures for the milestone-4 app tests."""

import difflib
import unittest
from functools import cache
from pathlib import Path

from replaykit.app import cases, world
from replaykit.app.bundle import bundle
from replaykit.app.incident import run_case

REPO = Path.home() / "Projects" / "deeptrace-shopapp"
FIX = (Path(__file__).resolve().parents[1] / "replaykit" / "app" / "protected" / "leak.patch").read_text()
needs_repo = unittest.skipUnless(REPO.exists(), "needs the shop app repository")


@cache
def leak_incident(seed=1):
    run = cases.case("leak", seed)
    return bundle(REPO, run, run_case(REPO, run))


def source(commit, path):
    return dict(world.blobs(REPO, commit))[Path(path)].decode()


def edit(commit, path, old, new):
    """A unified diff that replaces *old* with *new* in *path* at *commit*."""
    before = source(commit, path)
    if before.count(old) != 1:
        raise AssertionError(f"{old!r} must occur once in {path}")
    after = before.replace(old, new)
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}"))
