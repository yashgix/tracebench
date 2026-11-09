"""The investigator's tools for an app incident: M3's telemetry tools plus traces and git, all as of the page. They read
only the incident bundle, so truth, the reference fix and the protected tests are out of reach by construction."""

import re

from ..tools import LAST, Evidence
from ..tools import TOOLS as TELEMETRY_TOOLS

TOOLS = (*TELEMETRY_TOOLS, "traces", "commits", "diff", "source", "search")
METRICS = (
    "http.server.request.count",
    "http.server.error_rate",
    "http.server.duration.p95",
    "db.client.connections.usage",
    "db.client.connections.pending_requests",
)
TRACES, DIFF_LINES, SOURCE_LINES, HITS, TEXT = 5, 200, 80, 20, 200
RANGE = re.compile(r"([\w./-]+?)(?::(\d+)-(\d+))?")


class CodeEvidence(Evidence):
    METRICS = METRICS

    def __init__(self, incident):
        super().__init__(incident)
        self._spans = incident.spans
        self._code = incident.code

    @staticmethod
    def _log_row(event):
        return [
            event["id"],
            event["t"],
            event["service"],
            event["attrs"]["level"],
            event["attrs"]["message"],
            event["attrs"]["trace_id"],
        ]

    def _dispatch(self, tool, call):
        service, arg = call["service"], call["arg"]
        if tool in TELEMETRY_TOOLS:
            return super()._dispatch(tool, {"tool": tool, "service": service, "metric": arg, "level": arg})
        if tool == "traces":
            return self._traces(self._service(service), arg)
        if tool == "commits":
            return self._commits(self._service(service))
        if tool == "diff":
            return self._diff(arg)
        if tool == "source":
            return self._source(self._service(service), arg)
        if tool == "search":
            return self._search(self._service(service), arg)
        raise ValueError(f"unknown tool {tool!r}")

    def _traces(self, service, mode):
        own = [s for s in self._spans if s["service"] == service and s["kind"] == "server"]
        if mode == "error":
            picks = [s for s in own if s["status"] == "ERROR"]
        elif mode == "slow":
            picks = sorted(own, key=lambda s: (s["end_ms"] - s["start_ms"], s["end_ms"], s["span_id"]))
        else:
            raise ValueError(f"traces mode must be 'error' or 'slow', not {mode!r}")
        chosen = list(dict.fromkeys(s["trace_id"] for s in reversed(picks)))[:TRACES]
        traces = [[trace, [self._span_row(s) for s in self._spans if s["trace_id"] == trace]] for trace in chosen]
        ids = [*chosen, *(row[0] for _, rows in traces for row in rows)]
        return {"tool": "traces", "service": service, "mode": mode, "traces": traces}, ids

    @staticmethod
    def _span_row(span):
        return [
            span["span_id"],
            span["parent_span_id"],
            span["service"],
            span["name"],
            span["kind"],
            span["start_ms"],
            span["end_ms"] - span["start_ms"],
            span["status"],
            span["attributes"].get("http.status_code"),
        ]

    def _commits(self, service):
        deployed = self._code["deployed"][service]
        rows = [self._commit_row(short) for short in self._code["history"][deployed][:LAST]]
        return {"tool": "commits", "service": service, "deployed": deployed, "commits": rows}, [r[0] for r in rows]

    def _commit_row(self, short):
        commit = self._code["commits"][short]
        return [commit["hash"], commit["time"], commit["author"], commit["message"], commit["files"]]

    def _diff(self, short):
        if short not in self._code["diffs"]:
            raise ValueError(f"unknown commit {short!r}")
        lines = self._code["diffs"][short].splitlines()
        result = {
            "tool": "diff",
            "commit": self._commit_row(short),
            "truncated": len(lines) > DIFF_LINES,
            "diff": [line[:TEXT] for line in lines[:DIFF_LINES]],
        }
        return result, [short, *self._code["commits"][short]["files"]]

    def _tree(self, service):
        deployed = self._code["deployed"][service]
        return deployed, self._code["trees"][deployed]

    def _source(self, service, arg):
        match = RANGE.fullmatch(arg)
        if match is None:
            raise ValueError("source needs 'path' or 'path:start-end'")
        path, start, end = match.group(1), int(match.group(2) or 1), int(match.group(3) or SOURCE_LINES)
        commit, tree = self._tree(service)
        if path not in tree:
            raise ValueError(f"no file {path!r} at {commit}")
        if not 1 <= start <= end:
            raise ValueError("source range must have 1 <= start <= end")
        lines = tree[path].splitlines()
        end = min(end, start + SOURCE_LINES - 1, len(lines))
        rows = [[n, lines[n - 1][:TEXT]] for n in range(start, end + 1)]
        return {"tool": "source", "service": service, "commit": commit, "path": path, "lines": rows}, [path]

    def _search(self, service, pattern):
        if not 2 <= len(pattern) <= 80:
            raise ValueError("search needs a fixed string of 2 to 80 characters")
        commit, tree = self._tree(service)
        hits = [
            [path, n, line[:TEXT]]
            for path in sorted(tree)
            for n, line in enumerate(tree[path].splitlines(), 1)
            if pattern in line
        ]
        shown = hits[:HITS]
        result = {
            "tool": "search",
            "service": service,
            "commit": commit,
            "pattern": pattern,
            "hits": shown,
            "truncated": len(hits) > HITS,
        }
        return result, sorted({hit[0] for hit in shown})
