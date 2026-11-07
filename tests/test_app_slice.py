"""Milestone 4 slice rehearsal: scripted models investigate the real leak end to end through the v4 loop."""

import json
import unittest
from pathlib import Path

from app_helpers import EMPTY, FIX, REPO, calls, edit, needs_repo, result, scripted
from replaykit.app import actions, cases, tools
from replaykit.app.evaluate import run_app_case

GIVEN = Path(__file__).resolve().parents[1] / "replaykit" / "app" / "investigator"


def decide(act, evidence, patch="", **fields):
    return {**EMPTY, "act": act, "service": "inventory", "fault_type": "resource_leak", "commit": cases.LEAK,
            "file": "shop/inventory.py", "function": "Inventory.reserve", "confidence": 90, "evidence": evidence,
            "summary": "reserve leaks a connection on every failed stock check", "patch": patch, **fields}


INVESTIGATE = (
    calls(("alerts", "*", "-"), ("topology", "-", "-")),
    calls(("traces", "web", "error"), ("logs", "inventory", "error")),
    calls(("metrics", "inventory", "db.client.connections.usage"), ("deploys", "inventory", "-")),
    calls(("commits", "inventory", "-"), ("diff", "-", cases.LEAK)),
)


def evidence(packet):
    return [result(packet, "logs")["logs"][0][0], result(packet, "deploys")["deploys"][-1][0],
            result(packet, "metrics")["points"][-1][0]]


def honest(patch=FIX):
    return honest_with(evidence, patch)


def honest_with(cite, patch=FIX):
    return scripted(*INVESTIGATE, lambda packet: decide("patch", cite(packet), patch))


@needs_repo
class SliceRehearsalTest(unittest.TestCase):
    def run_model(self, model):
        return run_app_case(REPO, "leak", 1, model)

    def test_an_honest_investigation_resolves_the_leak(self):
        outcome = self.run_model(honest())
        self.assertEqual({key: outcome[key] for key in ("decision", "decision_correct", "root_cause_correct",
                                                         "location_correct", "evidence_valid", "patch_valid",
                                                         "resolved", "turns", "tool_calls")},
                         {"decision": "patch", "decision_correct": True, "root_cause_correct": True,
                          "location_correct": True, "evidence_valid": True, "patch_valid": True, "resolved": True,
                          "turns": 5, "tool_calls": 8})
        self.assertTrue(outcome["validation"]["replay"]["clean"])

    def test_same_run_same_bytes(self):
        self.assertEqual(json.dumps(self.run_model(honest())), json.dumps(self.run_model(honest())))

    def test_a_wrong_fix_is_not_resolved(self):
        wrong = edit(cases.LEAK, "shop/config.py", "POOL_SIZE = 5", "POOL_SIZE = 50")
        outcome = self.run_model(honest(wrong))
        self.assertTrue(outcome["root_cause_correct"])
        self.assertEqual((outcome["patch_valid"], outcome["resolved"]), (False, False))

    def test_citing_an_unread_commit_is_invalid_evidence(self):
        guess = scripted(INVESTIGATE[0], lambda packet: decide("patch", [result(packet, "alerts")["alerts"][0][0]],
                                                                FIX))
        outcome = self.run_model(guess)
        self.assertTrue(outcome["root_cause_correct"])
        self.assertEqual((outcome["evidence_valid"], outcome["resolved"]), (False, False))

    def test_citing_an_unseen_event_is_invalid_evidence(self):
        outcome = self.run_model(honest_with(lambda packet: [*evidence(packet), "ev-0002"]))
        self.assertEqual((outcome["root_cause_correct"], outcome["evidence_valid"], outcome["resolved"]),
                         (True, False, False))

    def test_a_diagnosis_without_a_fix_is_the_wrong_decision(self):
        outcome = self.run_model(scripted(*INVESTIGATE, lambda packet: decide("diagnose", evidence(packet))))
        self.assertEqual((outcome["decision_correct"], outcome["location_correct"], outcome["resolved"]),
                         (False, True, False))

    def test_a_rejected_patch_gets_one_more_try(self):
        bad = FIX.replace("+            try:", "+            import os")
        seen = []

        def retry(packet):
            seen.append(packet["history"][-1])
            return decide("patch", evidence(packet), FIX)

        outcome = self.run_model(scripted(*INVESTIGATE, lambda p: decide("patch", evidence(p), bad), retry))
        self.assertEqual((outcome["turns"], outcome["resolved"]), (6, True))
        self.assertEqual(seen[0]["act"], "invalid")
        self.assertIn("must not import", seen[0]["error"])

    def test_two_invalid_replies_end_the_run(self):
        bad = decide("patch", ["ev-0001"], "--- a/tests/test_pool.py\n+++ b/tests/test_pool.py\n@@ -1 +1 @@\n-a\n+b\n")
        outcome = self.run_model(scripted(bad, bad))
        self.assertEqual((outcome["outcome"], outcome["decision"], outcome["validation"]),
                         ("invalid_output", None, None))


class SchemaTest(unittest.TestCase):
    def test_schema_matches_the_parser(self):
        schema = json.loads((GIVEN / "action.schema.json").read_text())
        properties = schema["properties"]
        self.assertEqual(schema["required"], list(actions.ACTION_KEYS))
        self.assertEqual(properties["act"]["enum"], list(actions.ACTS))
        self.assertEqual(properties["fault_type"]["enum"], [*actions.FAULT_TYPES, "-"])
        self.assertEqual(properties["calls"]["items"]["properties"]["tool"]["enum"], list(tools.TOOLS))
        self.assertEqual(properties["calls"]["items"]["required"], list(actions.CALL_KEYS))

    def test_prompt_names_every_tool_metric_and_fault_type(self):
        prompt = (GIVEN / "prompt.txt").read_text()
        for name in (*tools.TOOLS, *tools.METRICS, *actions.FAULT_TYPES):
            self.assertIn(name, prompt)


if __name__ == "__main__":
    unittest.main()
