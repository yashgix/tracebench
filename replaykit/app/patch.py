"""A model's patch is untrusted text. It is checked as text, applied to a fresh clone at the deployed commit, checked
again as code, and judged by the app's own tests, the case's protected checks and the same traffic, each in a separate
sandboxed process run by a runner (SPEC-4 B4; trusted side)."""

import ast
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import incident

MAX_CHANGED, MAX_BYTES, TIMEOUT_S = 40, 8000, 120
PATH = re.compile(r"shop/[a-z_]+\.py")
LOCKED = ("shop/pool.py", "shop/runtime.py", "shop/telemetry.py")  # what the checks and the replay measure with
DENIED = ("exec", "eval", "open", "compile", "globals", "locals", "vars", "getattr", "setattr", "delattr", "exit",
          "quit", "print", "breakpoint", "input", "help", "sys", "os", "builtins", "sqlite3", "subprocess")
INTERNALS = ("spans", "logs", "gauges", "in_use", "opened", "now", "_idle", "_waiters", "_queue", "_connect")
SQL = ("ATTACH", "DETACH", "VACUUM", "PRAGMA", "LOAD_EXTENSION")
FORBIDDEN = re.compile(rf"\bimport\b|__|\b(?:{'|'.join(DENIED)})\b|(?i:\b(?:{'|'.join(SQL)})\b)")
BREAKS = re.compile("[\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")  # line ends to Python, not to git
META = re.compile(r"diff --git a/(\S+) b/\1|index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?")
ROOT = Path(__file__).resolve().parents[2]
PROTECTED = Path("replaykit/app/protected")  # inside a replaykit tree
GIT_ENV = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_DATE": "2026-11-07T00:00:00Z", "GIT_COMMITTER_DATE": "2026-11-07T00:00:00Z"}


def _file(header, line):
    if not line.startswith(header):
        raise ValueError(f"expected a {header.strip()!r} line, got {line[:60]!r}")
    path = line[len(header):]
    if not PATH.fullmatch(path) or path in LOCKED:
        raise ValueError(f"a patch may change only shop/*.py outside {', '.join(LOCKED)}, not {path!r}")
    return path


def check_patch(text):
    """Return (files, changed lines) of a unified diff that only modifies allowed app files; raise ValueError."""
    if not text or len(text.encode()) > MAX_BYTES:
        raise ValueError(f"a patch must be 1 to {MAX_BYTES} bytes")
    if BREAKS.search(text):
        raise ValueError("a patch may end lines only with \\n")
    lines, files, changed, hunk, named, i = text.removesuffix("\n").split("\n"), set(), 0, False, None, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old, new = _file("--- a/", line), _file("+++ b/", lines[i + 1])
            if old != new or named not in (None, new):
                raise ValueError("a patch must name the same file in all its headers")
            named = None
            files.add(new)
            hunk, i = False, i + 2
            continue
        if line.startswith("@@") and files:
            hunk = True
        elif hunk and line[:1] in ("+", "-"):
            changed += 1
            if line[0] == "+" and FORBIDDEN.search(line[1:]):
                raise ValueError(f"added lines must not import or reach internals: {line[:60]!r}")
        elif meta := META.fullmatch(line):
            named = meta.group(1) or named
        elif not (hunk and (line[:1] == " " or line == "" or line == "\\ No newline at end of file")):
            raise ValueError(f"not a plain unified diff line: {line[:60]!r}")
        i += 1
    if not files or not changed:
        raise ValueError("a patch must change at least one line")
    if changed > MAX_CHANGED:
        raise ValueError(f"a patch may change at most {MAX_CHANGED} lines, not {changed}")
    return sorted(files), changed


def check_count(checks):
    return sum((ROOT / PROTECTED / name).read_text().count("    def test_") for name in checks)


def _run(args, cwd, env=None, stdin=None):
    try:
        return subprocess.run(args, cwd=cwd, env=env or {"PATH": "/usr/bin:/bin"}, input=stdin, capture_output=True,
                              text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None


class LocalRunner:
    """Runs patched code as this user under sandbox-exec: no network, no writes outside the job's folder. For tests and
    scripted rehearsals; a model's patch runs through a runner for the _arcexec account instead."""

    root = None  # where job folders are made (None: the system's temporary folder)
    replaykit = ROOT  # the replaykit tree whose protected checks and replay the jobs run

    def __call__(self, args, cwd, folder):
        """(exit code, stdout) of `python -B *args*`, or None on timeout."""
        profile = (f'(version 1)(allow default)(deny network*)(deny file-write*)'
                   f'(allow file-write* (subpath "{Path(folder).resolve()}") (literal "/dev/null"))')
        env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(folder)}
        done = _run(["/usr/bin/sandbox-exec", "-p", profile, sys.executable, "-B", *args], cwd, env)
        return None if done is None else (done.returncode, done.stdout)


def _apply(repo, base, patch, clone):
    """Commit *patch* on *base* in a fresh *clone* and return the new commit's short hash."""
    git = ["git", "-C", str(clone), "-c", "user.name=replaykit", "-c", "user.email=replaykit@example.invalid"]
    steps = [("clone", ["git", "clone", "-q", "--no-hardlinks", "--no-checkout", str(repo), str(clone)], None),
             ("checkout", [*git, "checkout", "-q", "--detach", base], None),
             ("apply", [*git, "apply", "--check", "--recount", "-"], patch),
             ("apply", [*git, "apply", "--recount", "-"], patch),
             ("commit", [*git, "commit", "-q", "-a", "-m", "candidate patch"], None),
             ("rev-parse", [*git, "rev-parse", "--short=7", "HEAD"], None)]
    for name, args, stdin in steps:
        done = _run(args, clone.parent, GIT_ENV, stdin)
        if done is None or done.returncode:
            raise ValueError(f"git {name} failed: {done.stderr.strip()[:300] if done else 'timeout'}")
    return done.stdout.strip()


def _flags(source):
    """What a patch may not add: imports, dunders, denied names and SQL, the harness's internals."""
    found = Counter()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found["import"] += 1
        elif isinstance(node, ast.Name) and (node.id in DENIED or node.id.startswith("__")):
            found[node.id] += 1
        elif isinstance(node, ast.Attribute) and (node.attr in INTERNALS or node.attr.startswith("__")):
            found["." + node.attr] += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(word for word in SQL if word in node.value.upper())
    return found


def _check_code(repo, base, clone, files):
    for path in files:
        before = subprocess.run(["git", "--git-dir", str(Path(repo) / ".git"), "show", f"{base}:{path}"],
                                capture_output=True, text=True, check=True, env=GIT_ENV).stdout
        try:
            added = _flags((clone / path).read_text()) - _flags(before)
        except SyntaxError as exc:
            raise ValueError(f"the patched {path} does not parse: {exc.msg}") from None
        if added:
            raise ValueError(f"the patch adds {', '.join(sorted(added))} to {path}")


def _own_tests(clone, runner):
    done = runner(["-m", "unittest", "discover", "-s", "tests"], clone, clone.parent)
    return "timeout" if done is None else ("pass" if done[0] == 0 else "fail")


def _result(done, keys):
    """The JSON object a check printed, or None unless it has exactly *keys*."""
    if done is None or done[0]:
        return None
    try:
        result = json.loads(done[1])
    except ValueError:
        return None
    return result if isinstance(result, dict) and set(result) == set(keys) else None


def protected_result(result, checks):
    """*result* if it is a complete run of every check in *checks*, else a failed one."""
    if result is None or result["run"] != check_count(checks) or not isinstance(result["failed"], list):
        return {"run": 0, "failed": ["incomplete"]}
    return result


def _protected(clone, checks, runner):
    done = runner(["-I", str(runner.replaykit / PROTECTED / "run.py"), str(clone), *checks], clone.parent, clone.parent)
    return protected_result(_result(done, ("run", "failed")), checks)


def _replay(clone, case, service, base, patched, runner):
    deploys = [d | {"commit": patched} if (d["service"], d["commit"]) == (service, base) else d
               for d in case["deploys"]]
    if deploys == case["deploys"]:
        deploys = [{"at_ms": 0, "service": service, "commit": patched}, *deploys]
    done = runner(["-m", "replaykit.app.patch", str(clone), json.dumps(case | {"deploys": deploys})], runner.replaykit,
                  clone.parent)
    result = _result(done, ("page", "pool_in_use_end", "web_errors", "clean"))
    if result is None or not isinstance(result["clean"], bool):
        return {"page": None, "pool_in_use_end": None, "web_errors": None, "clean": False}
    return result


def replay_summary(repo, case):
    """What the same traffic shows with *case*'s deploys: a page or not, and the pool after traffic stops."""
    raw = incident.run_case(repo, case)
    usage = [g["value"] for g in raw["gauges"] if g["name"] == "db.client.connections.usage"]
    end = usage[-1] if usage else None
    web_errors = sum(1 for s in raw["spans"] if s["service"] == "web" and s["kind"] == "server"
                     and s["status"] == "ERROR")
    return {"page": raw["page"] is not None, "pool_in_use_end": end, "web_errors": web_errors,
            "clean": raw["page"] is None and end == 0}


def validate_patch(repo, case, decision, base, checks, runner=LocalRunner()):
    """Judge *decision*'s patch, made against *base* (the commit deployed for its service), on *case* (a run
    description) with the protected *checks* (file names)."""
    result = {"files": [], "changed_lines": 0, "applies": False, "error": None, "own_tests": None,
              "protected": None, "replay": None, "valid": False}
    try:
        result["files"], result["changed_lines"] = check_patch(decision["patch"])
        with tempfile.TemporaryDirectory(dir=runner.root) as folder:
            clone = Path(folder) / "repo"
            patch = decision["patch"] if decision["patch"].endswith("\n") else decision["patch"] + "\n"
            patched = _apply(repo, base, patch, clone)
            _check_code(repo, base, clone, result["files"])
            result["applies"] = True
            result["own_tests"] = _own_tests(clone, runner)
            result["protected"] = _protected(clone, checks, runner)
            result["replay"] = _replay(clone, case, decision["service"], base, patched, runner)
    except ValueError as exc:
        result["error"] = str(exc)
        return result
    result["valid"] = (result["own_tests"] == "pass" and not result["protected"]["failed"]
                       and result["replay"]["clean"])
    return result


if __name__ == "__main__":
    print(json.dumps(replay_summary(Path(sys.argv[1]), json.loads(sys.argv[2]))))
