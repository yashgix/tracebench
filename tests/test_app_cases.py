"""SPEC-4 B2: the five cases after the leak, each produced by running the app, with scripted investigations."""

import json
import unittest
from functools import cache
from pathlib import Path

from app_helpers import EMPTY, REPO, calls, edit, needs_repo, result, scripted
from replaykit.app import cases
from replaykit.app.bundle import bundle
from replaykit.app.evaluate import run_app_case
from replaykit.app.incident import run_case
from replaykit.app.patch import ROOT, validate_patch

FIXES = {name: (ROOT / "replaykit/app/protected" / f"{name}.patch").read_text()
         for name in ("config", "decoy", "slow_query")}
# The commit right after each defect: its fix on main.
FIXED = {"config": cases.PER_USER, "decoy": "4a4338c", "slow_query": cases.LOWER_CASED}


@cache
def incident(name, seed=1):
    run = cases.case(name, seed)
    return bundle(REPO, run, run_case(REPO, run))


def page_minute(name, deploys=None, seed=1):
    run = cases.case(name, seed) | ({"deploys": deploys} if deploys is not None else {})
    page = run_case(REPO, run)["page"]
    return page and page["time_ms"] // cases.MINUTE


def validate(name, patch):
    truth = cases.CASES[name]["truth"]
    base = incident(name).code["deployed"][truth["service"]]
    return validate_patch(REPO, cases.case(name, 1), {"service": truth["service"], "patch": patch}, base,
                          cases.CASES[name]["protected"])


@needs_repo
class CasesTest(unittest.TestCase):
    def test_each_case_pages_two_minutes_after_its_event(self):
        for name in ("config", "decoy", "slow_query", "outside_lock", "load_test"):
            for seed in (1, 2):
                with self.subTest(name=name, seed=seed):
                    self.assertEqual(page_minute(name, seed=seed), 12)

    def test_the_fix_commit_never_pages(self):
        for name, fixed in FIXED.items():
            deploys = [d | {"commit": fixed} if d["commit"] == cases.CASES[name]["truth"]["commit"] else d
                       for d in cases.CASES[name]["deploys"]]
            with self.subTest(name=name):
                self.assertIsNone(page_minute(name, deploys))

    def test_without_the_outside_event_nothing_pages(self):
        for name, key in (("outside_lock", "db_lock"), ("load_test", "load_test")):
            run = {k: v for k, v in cases.case(name, 1).items() if k != key}
            with self.subTest(name=name):
                self.assertIsNone(run_case(REPO, run)["page"])

    def test_ids_and_bundles_name_no_answer(self):
        for name, spec in cases.CASES.items():
            text = json.dumps(incident(name).__dict__)
            with self.subTest(name=name):
                self.assertEqual(incident(name).id, f"shop-app:{spec['id']}:1")
                self.assertNotIn(name, incident(name).id)
                own_fix = [FIXED[name]] if name in FIXED else []  # earlier cases' fixes are ordinary history
                for secret in ("protected", *spec["protected"], *own_fix):
                    self.assertFalse(secret in text, secret)

    def test_decoy_errors_start_before_the_web_deploy(self):
        events = incident("decoy").events
        web_deploy = [e["t"] for e in events if e["kind"] == "deploy" and e["attrs"]["version"] == cases.LOG_WORDING]
        first_error = min(e["t"] for e in events if e["kind"] == "log" and e["attrs"]["level"] == "error")
        self.assertEqual(len(web_deploy), 1)
        self.assertLess(first_error, web_deploy[0])

    def test_the_load_test_is_over_by_the_page(self):
        events = incident("load_test").events
        limited = [e["t"] for e in events if e["kind"] == "log" and "perf-loadtest" in e["attrs"]["message"]]
        self.assertTrue(limited)
        self.assertLess(max(limited), incident("load_test").duration)
        self.assertFalse(any(e["kind"] == "deploy" and e["t"] > 0 for e in events))


@needs_repo
class CasePatchTest(unittest.TestCase):
    def assertValid(self, name, patch, valid):
        outcome = validate(name, patch)
        self.assertEqual(outcome["valid"], valid, outcome)
        return outcome

    def test_reference_fixes_are_valid(self):
        for name, patch in FIXES.items():
            with self.subTest(name=name):
                self.assertTrue(self.assertValid(name, patch, True)["replay"]["clean"])

    def test_a_higher_product_limit_is_caught(self):
        wrong = edit(cases.PER_PRODUCT, "shop/config.py", "CHECKOUT_LIMIT = 5", "CHECKOUT_LIMIT = 1000")
        failed = self.assertValid("config", wrong, False)["protected"]["failed"]
        self.assertEqual(failed, ["check_limits.LimitChecks.test_one_busy_user_is_slowed_down"])

    def test_dropping_the_order_cap_is_caught(self):
        wrong = edit(cases.ORDER_CAP, "shop/orders.py", '''            if quantity > config.MAX_ORDER_ITEMS:
                raise ValueError(f"at most {config.MAX_ORDER_ITEMS} items per order")
''', "")
        failed = self.assertValid("decoy", wrong, False)["protected"]["failed"]
        self.assertEqual(failed, ["check_orders.OrderChecks.test_an_oversized_order_is_refused_as_a_client_error"])

    def test_a_longer_timeout_is_caught(self):
        wrong = edit(cases.ANY_CASE, "shop/config.py", "UPSTREAM_TIMEOUT_MS = 1500", "UPSTREAM_TIMEOUT_MS = 5000")
        outcome = self.assertValid("slow_query", wrong, False)
        self.assertEqual((outcome["own_tests"], outcome["replay"]["clean"]), ("fail", False))
        self.assertEqual(outcome["protected"]["failed"],
                         ["check_lookup.LookupChecks.test_a_lookup_in_a_large_catalogue_is_quick"])

    def test_dropping_the_any_case_lookup_is_caught(self):
        wrong = edit(cases.ANY_CASE, "shop/inventory.py", "WHERE lower(sku) = lower(?)", "WHERE sku = ?")
        failed = self.assertValid("slow_query", wrong, False)["protected"]["failed"]
        self.assertEqual(failed, ["check_lookup.LookupChecks.test_any_case_finds_the_product"])


def decision(act, service="-", fault_type="-", commit="-", file="-", function="-", patch=""):
    return lambda cite: {**EMPTY, "act": act, "service": service, "fault_type": fault_type, "commit": commit,
                         "file": file, "function": function, "confidence": 80, "evidence": cite, "summary": "s",
                         "patch": patch}


# Per case: the calls an investigator makes, and what to cite from their results.
STEPS = {
    "config": ((("deploys", "web", "-"), ("logs", "web", "warn")), (("commits", "web", "-"), ("diff", "-", cases.PER_PRODUCT))),
    "decoy": ((("deploys", "*", "-"), ("traces", "web", "error")), (("commits", "orders", "-"), ("diff", "-", cases.ORDER_CAP))),
    "slow_query": ((("deploys", "inventory", "-"), ("traces", "web", "slow")), (("commits", "inventory", "-"), ("diff", "-", cases.ANY_CASE))),
    "outside_lock": ((("deploys", "*", "-"), ("logs", "inventory", "error")), (("search", "inventory", "BEGIN"),)),
    "load_test": ((("deploys", "*", "-"), ("logs", "web", "warn")), (("metrics", "web", "http.server.request.count"),)),
}
TRUE = {
    "config": decision("patch", "web", "config_error", cases.PER_PRODUCT, "shop/config.py", patch=FIXES["config"]),
    "decoy": decision("patch", "orders", "code_defect", cases.ORDER_CAP, "shop/orders.py", "Orders.place",
                      FIXES["decoy"]),
    "slow_query": decision("patch", "inventory", "slow_query", cases.ANY_CASE, "shop/inventory.py", "stock",
                           FIXES["slow_query"]),
    "outside_lock": decision("escalate", "inventory"),
    "load_test": decision("dismiss"),
}


def cite(packet):
    """The first id of the first deploy, log or trace row the investigator received."""
    ids = []
    for tool, key in (("deploys", "deploys"), ("logs", "logs"), ("traces", "traces")):
        try:
            rows = result(packet, tool)[key]
        except IndexError:
            continue
        if rows:
            ids.append(rows[-1][0])
    return ids


def investigate(name, act):
    return run_app_case(REPO, name, 1, scripted(*(calls(*step) for step in STEPS[name]), lambda p: act(cite(p))))


@needs_repo
class CaseRehearsalTest(unittest.TestCase):
    def test_honest_investigations_resolve_every_case(self):
        for name, act in TRUE.items():
            with self.subTest(name=name):
                outcome = investigate(name, act)
                self.assertTrue(outcome["evidence_valid"], outcome["action"])
                self.assertTrue(outcome["resolved"], {k: v for k, v in outcome.items() if k != "transcript"})

    def test_blaming_the_decoy_deploy_is_wrong(self):
        outcome = investigate("decoy", decision("diagnose", "web", "code_defect", cases.LOG_WORDING, "shop/web.py"))
        self.assertEqual((outcome["root_cause_correct"], outcome["resolved"]), (False, False))

    def test_patching_or_dismissing_the_outside_lock_is_wrong(self):
        for act in (decision("dismiss"), decision("diagnose", "inventory", "code_defect", cases.LOWER_CASED,
                                                  "shop/inventory.py")):
            with self.subTest(act=act([])["act"]):
                self.assertFalse(investigate("outside_lock", act)["resolved"])

    def test_escalating_the_load_test_is_safe_but_not_resolved(self):
        outcome = investigate("load_test", decision("escalate", "web"))
        self.assertEqual((outcome["decision"], outcome["resolved"]), ("escalate", False))


if __name__ == "__main__":
    unittest.main()
