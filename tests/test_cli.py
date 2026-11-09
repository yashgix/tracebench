import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from replaykit.__main__ import main

SCENARIO = {
    "id": "scn-cli",
    "duration": 600,
    "services": ["api", "db"],
    "calls": {"api": ["db"]},
    "events": [],
}


def fault(type_="bad_deploy", service="db", start=120):
    return {"type": type_, "service": service, "start": start}


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scenario = self._write_json("scenario.json", SCENARIO)
        self.faults = self._write_json("faults.json", [fault()])

    def _path(self, name):
        return os.path.join(self.tmp.name, name)

    def _write_json(self, name, data):
        path = self._path(name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def _write_text(self, name, text):
        path = self._path(name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def _run(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["run", *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def _args(self, **overrides):
        values = {
            "--scenario": self.scenario,
            "--faults": self.faults,
            "--seeds": "1",
            "--agents": "last_deploy,threshold",
        }
        values.update(overrides)
        args = []
        for key, value in values.items():
            args.extend((key, value))
        return args

    def test_success_prints_sorted_markdown_rows(self):
        code, out, err = self._run(*self._args())
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        lines = out.strip().splitlines()
        self.assertEqual(
            lines[0], "| agent | runs | root cause accuracy | mean points | brier |"
        )
        self.assertEqual(lines[1], "| --- | --- | --- | --- | --- |")
        names = [line.split("|")[1].strip() for line in lines[2:]]
        self.assertEqual(names, ["last_deploy", "threshold"])

    def test_brier_none_prints_dash(self):
        faults = self._write_json("config.json", [fault("config_error")])
        code, out, err = self._run(
            *self._args(**{"--faults": faults, "--agents": "last_deploy"})
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        row = out.strip().splitlines()[2]
        self.assertTrue(row.endswith("| - |"))

    def test_out_writes_report_json(self):
        out_path = self._path("report.json")
        code, out, err = self._run(*self._args(**{"--out": out_path}))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        with open(out_path, encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(set(report), {"agents", "incidents"})
        self.assertEqual(list(report["agents"]), ["last_deploy", "threshold"])
        self.assertEqual(report["incidents"][0]["id"], "scn-cli:bad_deploy:db:1")
        self.assertIn("| agent |", out)

    def test_faults_and_seeds_keep_given_order(self):
        faults = self._write_json(
            "ordered.json",
            [fault("bad_deploy", "db", 120), fault("config_error", "api", 60)],
        )
        out_path = self._path("ordered-report.json")
        code, _, err = self._run(
            *self._args(**{"--faults": faults, "--seeds": "1,2", "--out": out_path})
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        with open(out_path, encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(
            [entry["id"] for entry in report["incidents"]],
            [
                "scn-cli:bad_deploy:db:1",
                "scn-cli:bad_deploy:db:2",
                "scn-cli:config_error:api:1",
                "scn-cli:config_error:api:2",
            ],
        )

    def test_missing_scenario_is_an_error(self):
        missing = self._path("missing.json")
        code, out, err = self._run(*self._args(**{"--scenario": missing}))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "))
        self.assertIn("missing.json", err)

    def test_unknown_agent_is_an_error(self):
        code, out, err = self._run(
            *self._args(**{"--agents": "last_deploy,oracle"})
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("unknown agent 'oracle'", err)

    def test_bad_seed_is_an_error(self):
        code, out, err = self._run(*self._args(**{"--seeds": "1,two"}))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("invalid seed 'two'", err)

    def test_empty_seeds_is_an_error(self):
        code, out, err = self._run(*self._args(**{"--seeds": ""}))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("seeds must be", err)

    def test_bad_fault_is_an_error(self):
        faults = self._write_json("bad-fault.json", [fault("dns_outage")])
        code, out, err = self._run(*self._args(**{"--faults": faults}))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("unknown fault type 'dns_outage'", err)

    def test_faults_must_be_a_list(self):
        faults = self._write_json("not-list.json", fault())
        code, out, err = self._run(*self._args(**{"--faults": faults}))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("faults must be a JSON list", err)

    def test_invalid_faults_json_is_an_error(self):
        faults = self._write_text("broken.json", "{not json")
        code, out, err = self._run(*self._args(**{"--faults": faults}))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("invalid JSON", err)


if __name__ == "__main__":
    unittest.main()
