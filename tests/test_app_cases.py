"""SPEC-4 B2: the five cases after the leak, each produced by running the app, with scripted investigations."""

import json
import subprocess
import sys
import tempfile
import unittest
from functools import cache
from pathlib import Path

from app_helpers import EMPTY, REPO, RUNNER, calls, edit, needs_repo, result, scripted
from replaykit.app import cases
from replaykit.app.bundle import bundle
from replaykit.app.evaluate import run_app_case
from replaykit.app.incident import run_case
from replaykit.app.patch import PROTECTED, ROOT, _apply, check_patch, validate_patch

FIXES = {
    name: (ROOT / "replaykit/app/protected" / f"{name}.patch").read_text() for name in ("config", "decoy", "slow_query")
}
# Wrong fixes a reviewer tried: a clock that drops the scan's cost, and per_user(5000).
QUERY = "return await db.query(self.runtime, connection, sql, params)"
FAKE = (
    'return await db.query(type("Clock", (), {"sleep": lambda _, ms: self.runtime.sleep(config.QUERY_MS)})(), '
    'connection, sql, params)'
)
LOOKUP = 'rows = await self._query(connection, "SELECT stock FROM items WHERE lower(sku) = lower(?)", (sku,))\n'
FAKE_CLOCK = edit(cases.ANY_CASE, "shop/inventory.py", QUERY, FAKE)
# The reference fix with an extra full scan beside the indexed lookup: under a fake clock, or under an alias whose
# query fails in db.query's cost model and is swallowed.
ALIASED_SCAN = (
    'try:\n' + ' ' * 20 + 'await self._query(connection, "SELECT stock FROM items AS i WHERE lower(i.sku) = '
    'lower(?)", (sku,))\n' + ' ' * 16 + 'except ServiceError:\n' + ' ' * 20 + 'pass\n'
)
SCAN_ALIASED = edit(
    cases.ANY_CASE, "shop/inventory.py", LOOKUP, LOOKUP.replace("lower(sku)", "sku") + ' ' * 16 + ALIASED_SCAN
)
# A lookup kept in `self.known` by lower-cased code, and its entry dropped.
KNOWN = ("        self.running = True\n", "        self.running = True\n        self.known = {}\n")
CACHE_READ = (
    'if sku.lower() not in self.known:\n' + ' ' * 20 + 'self.known[sku.lower()] = await self._query('
    'connection, "SELECT stock FROM items WHERE sku = lower(?)", (sku,))\n'
)
CACHE_ROWS = " " * 16 + "rows = self.known[sku.lower()]\n"
CACHE_DROP = " " * 16 + "self.known.pop(sku.lower(), None)\n"
RESERVE_READ = " " * 16 + "stock = await self._read_stock(connection, sku)\n"
UPDATE = (
    " " * 16 + 'await self._query(connection, "UPDATE items SET stock = stock - ? WHERE sku = ?", (quantity, sku))\n'
)
SCAN_BESIDE = edit(
    cases.ANY_CASE,
    "shop/inventory.py",
    QUERY,
    FAKE,
    LOOKUP,
    LOOKUP.replace("lower(sku)", "sku")
    + ' ' * 16
    + 'await self._query(connection, "SELECT count(*) FROM items", ())\n',
)


def per_user(window_ms):
    return edit(
        cases.PER_PRODUCT,
        "shop/config.py",
        'CHECKOUT_LIMIT_KEY = "sku"\nCHECKOUT_LIMIT_WINDOW_MS = 60000',
        f'CHECKOUT_LIMIT_KEY = "user"\nCHECKOUT_LIMIT_WINDOW_MS = {window_ms}',
    )


WINDOW_CHECK = "check_limits.LimitChecks.test_the_limit_holds_for_one_second_then_starts_again"
INDEX_CHECK = "check_lookup.LookupChecks.test_a_lookup_reads_the_catalogue_through_its_index"
RESERVATION_CHECK = "check_lookup.LookupChecks.test_a_lookup_shows_the_stock_in_the_database"
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
    return validate_patch(
        REPO,
        cases.case(name, 1),
        {"service": truth["service"], "patch": patch},
        base,
        cases.CASES[name]["protected"],
        RUNNER,
    )


@needs_repo
class CasesTest(unittest.TestCase):
    def test_each_case_pages_two_minutes_after_its_event(self):
        for name in ("config", "decoy", "slow_query", "outside_lock", "load_test"):
            for seed in (1, 2):
                with self.subTest(name=name, seed=seed):
                    self.assertEqual(page_minute(name, seed=seed), 12)

    def test_the_fix_commit_never_pages(self):
        for name, fixed in FIXED.items():
            deploys = [
                d | {"commit": fixed} if d["commit"] == cases.CASES[name]["truth"]["commit"] else d
                for d in cases.CASES[name]["deploys"]
            ]
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

    def test_run_descriptions_carry_no_truth(self):
        for name in cases.CASES:
            self.assertFalse({"truth", "protected"} & set(cases.case(name, 1)), name)

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
        self.assertEqual(failed, ["check_limits.LimitChecks.test_one_busy_user_is_slowed_down", WINDOW_CHECK])

    def test_dropping_the_order_cap_is_caught(self):
        wrong = edit(
            cases.ORDER_CAP,
            "shop/orders.py",
            '''            if quantity > config.MAX_ORDER_ITEMS:
                raise ValueError(f"at most {config.MAX_ORDER_ITEMS} items per order")
''',
            "",
        )
        failed = self.assertValid("decoy", wrong, False)["protected"]["failed"]
        self.assertEqual(failed, ["check_orders.OrderChecks.test_an_oversized_order_is_refused_as_a_client_error"])

    def test_a_longer_timeout_is_caught(self):
        wrong = edit(cases.ANY_CASE, "shop/config.py", "UPSTREAM_TIMEOUT_MS = 1500", "UPSTREAM_TIMEOUT_MS = 5000")
        outcome = self.assertValid("slow_query", wrong, False)
        self.assertEqual((outcome["own_tests"], outcome["replay"]["clean"]), ("fail", False))
        self.assertEqual(
            outcome["protected"]["failed"],
            ["check_lookup.LookupChecks.test_a_lookup_in_a_large_catalogue_is_quick", INDEX_CHECK, RESERVATION_CHECK],
        )

    def test_a_per_product_limit_per_second_is_caught(self):
        wrong = edit(
            cases.PER_PRODUCT, "shop/config.py", "CHECKOUT_LIMIT_WINDOW_MS = 60000", "CHECKOUT_LIMIT_WINDOW_MS = 1000"
        )
        failed = self.assertValid("config", wrong, False)["protected"]["failed"]
        self.assertEqual(failed, ["check_limits.LimitChecks.test_many_customers_can_buy_one_product"])

    def test_a_per_user_window_other_than_one_second_is_caught(self):
        for window_ms in (5000, 500):
            with self.subTest(window_ms=window_ms):
                failed = self.assertValid("config", per_user(window_ms), False)["protected"]["failed"]
                self.assertEqual(failed, [WINDOW_CHECK])

    def test_a_fake_query_clock_is_refused_before_it_runs(self):
        outcome = self.assertValid("slow_query", FAKE_CLOCK, False)
        self.assertEqual((outcome["applies"], outcome["own_tests"]), (False, None))
        self.assertIn("clock", outcome["error"])

    def test_the_index_check_alone_catches_a_fake_query_clock(self):
        # Without the clock rule: the patch applied by hand, only check_lookup run against it.
        for name, patch in (
            ("fake clock", FAKE_CLOCK),
            ("scan beside the index", SCAN_BESIDE),
            ("aliased scan", SCAN_ALIASED),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                clone = Path(folder) / "repo"
                _apply(REPO, cases.ANY_CASE, patch, clone)
                done = subprocess.run(
                    [sys.executable, "-I", "-B", str(ROOT / PROTECTED / "run.py"), str(clone), "check_lookup.py"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(json.loads(done.stdout), {"run": 5, "failed": [INDEX_CHECK]})

    def test_a_range_lookup_that_walks_the_catalogue_is_caught(self):
        # Wrong fixes a reviewer tried: the plan says SEARCH, yet SQLite steps through every product.
        for bound in ("''", "'sku-'"):
            wrong = edit(
                cases.ANY_CASE,
                "shop/inventory.py",
                LOOKUP,
                LOOKUP.replace("WHERE lower(sku)", f"WHERE sku >= {bound} AND lower(sku)"),
            )
            with self.subTest(bound=bound):
                outcome = self.assertValid("slow_query", wrong, False)
                self.assertEqual((outcome["own_tests"], outcome["replay"]["clean"]), ("pass", True))
                self.assertEqual(outcome["protected"]["failed"], [INDEX_CHECK])

    def test_catalogue_work_moved_out_of_sight_is_caught(self):
        indent, walk = " " * 16, LOOKUP.replace("WHERE lower(sku)", "WHERE sku >= '' AND lower(sku)")
        exact = LOOKUP.replace("lower(sku) = lower(?)", "sku = ?")
        cache = (
            'if not self.known:\n' + indent + '    self.known = dict(await self._query(connection, '
            '"SELECT lower(sku), stock FROM items WHERE sku >= \'\'", ()))\n'
            + indent
            + 'rows = [(self.known[sku.lower()],)] if sku.lower() in self.known else []\n'
        )
        rebuild = (
            'await self._query(connection, "DROP INDEX IF EXISTS item_lower", ())\n'
            + indent
            + 'await self._query(connection, "CREATE INDEX IF NOT EXISTS item_lower ON items(lower(sku))", ())'
            + '\n'
            + indent
            + LOOKUP
        )
        upper = "if sku == sku.lower():\n" + indent + "    " + exact + indent + "else:\n" + indent + "    " + walk
        miss = LOOKUP.replace("lower(sku) = lower(?)", "sku = lower(?)") + indent + "if not rows:\n" + indent + "    "
        tries = {
            "walk once, then a cache": ((*KNOWN, LOOKUP, cache), [INDEX_CHECK, RESERVATION_CHECK]),
            "an index rebuilt on every lookup": ((LOOKUP, rebuild), [INDEX_CHECK]),
            "a walk only for a miss": ((LOOKUP, miss + walk), [INDEX_CHECK]),
            "a walk only for upper case": ((LOOKUP, upper), [INDEX_CHECK]),
            "exact first, then a walk": (
                (LOOKUP, exact + indent + "if not rows:\n" + indent + "    " + walk),
                [INDEX_CHECK],
            ),
        }
        for name, (pairs, expected) in tries.items():
            with self.subTest(name=name):
                outcome = self.assertValid("slow_query", edit(cases.ANY_CASE, "shop/inventory.py", *pairs), False)
                self.assertEqual(outcome["protected"]["failed"], expected)

    def test_a_stale_cache_is_caught(self):
        # A reviewer's wrong fix: each entry dropped after one second, so reads seconds apart look fresh.
        expire = " " * 20 + "self.runtime.spawn(self.forget(sku.lower()))\n"

        def kept_for(ms):
            return (
                *KNOWN,
                LOOKUP,
                CACHE_READ + expire + CACHE_ROWS,
                "    async def report_pool(self):\n",
                f"    async def forget(self, key):\n        await self.runtime.sleep({ms})\n"
                "        self.known.pop(key, None)\n\n    async def report_pool(self):\n",
            )

        def as_asked(text):
            return text.replace("sku.lower()", "sku")

        stored_late = (
            CACHE_READ.replace("self.known[sku.lower()] = await", "found = await")
            + " " * 20
            + "await self.runtime.sleep(10)\n"
            + " " * 20
            + "self.known[sku.lower()] = found\n"
        )
        tries = {
            "kept for ever": (*KNOWN, LOOKUP, CACHE_READ + CACHE_ROWS),
            "kept for a second": kept_for(1000),
            "kept for 50 ms": kept_for(50),  # the reviewer agent's: gone before reads 60 ms later
            # A reviewer's wrong fix: kept under the code as asked, so `SKU-007` outlives a reservation of
            # `sku-007`; dropped before the update, so a read overlapping the reservation keeps the old stock.
            "kept under the code as asked": (
                *KNOWN,
                LOOKUP,
                as_asked(CACHE_READ + CACHE_ROWS),
                UPDATE,
                UPDATE + as_asked(CACHE_DROP),
            ),
            "dropped before the update": (
                *KNOWN,
                LOOKUP,
                CACHE_READ + CACHE_ROWS,
                RESERVE_READ,
                CACHE_DROP + RESERVE_READ,
            ),
            # Read before the update, stored after the drop: caught only when no read filled the cache first.
            "stored late": (*KNOWN, LOOKUP, stored_late + CACHE_ROWS, UPDATE, UPDATE + CACHE_DROP),
            # A reviewer's wrong fix: two overlapping reservations both read 8 and both store 5; the database has 2.
            "set after the update": (
                *KNOWN,
                LOOKUP,
                CACHE_READ + CACHE_ROWS,
                UPDATE,
                UPDATE + " " * 16 + "self.known[sku.lower()] = [(stock - quantity,)]\n",
            ),
        }
        for name, pairs in tries.items():
            with self.subTest(name=name):
                failed = self.assertValid("slow_query", edit(cases.ANY_CASE, "shop/inventory.py", *pairs), False)[
                    "protected"
                ]["failed"]
                self.assertEqual(failed, [RESERVATION_CHECK])

    def test_a_reservation_that_loses_an_overlapping_one_is_caught(self):
        # Each writes back the stock it read, so the second of two overlapping reservations undoes the first.
        lost = UPDATE.replace("stock = stock - ?", "stock = ?").replace("(quantity, sku)", "(stock - quantity, sku)")
        patch = edit(cases.ANY_CASE, "shop/inventory.py", LOOKUP, LOOKUP.replace("lower(sku)", "sku"), UPDATE, lost)
        self.assertEqual(self.assertValid("slow_query", patch, False)["protected"]["failed"], [RESERVATION_CHECK])

    def test_a_lookup_that_skips_the_database_is_caught(self):
        stock = '[(int(sku.lower().split("-")[1]) % 50 + 1,)] if sku.lower().startswith("sku-") else []'
        wrong = edit(cases.ANY_CASE, "shop/inventory.py", LOOKUP, f"rows = {stock}\n")
        failed = self.assertValid("slow_query", wrong, False)["protected"]["failed"]
        self.assertIn(INDEX_CHECK, failed)
        self.assertIn("check_inventory.InventoryChecks.test_stock_lookup_returns_the_connection", failed)

    def test_correct_lookups_written_another_way_are_valid(self):
        exact = LOOKUP.replace("lower(sku)", "sku")
        fixes = {
            "keyword clock": (
                LOOKUP,
                exact,
                QUERY,
                "return await db.query(runtime=self.runtime, connection=connection, sql=sql, params=params)",
            ),
            "expression index": (
                LOOKUP,
                'await self._query(connection, "CREATE INDEX IF NOT EXISTS '
                'item_lower ON items(lower(sku))", ())\n' + ' ' * 16 + LOOKUP,
            ),
            "commented index": (
                LOOKUP,
                'await self._query(connection, "/* lookups */ CREATE INDEX IF NOT '
                'EXISTS item_lower ON items(lower(sku))", ())\n' + ' ' * 16 + LOOKUP,
            ),
            "a cache dropped after the update": (*KNOWN, LOOKUP, CACHE_READ + CACHE_ROWS, UPDATE, UPDATE + CACHE_DROP),
            # A reviewer's wrong fix: slow, but finished well inside 50 ms.
            "a lookup that waits 25 ms": (LOOKUP, "await self.runtime.sleep(25)\n" + " " * 16 + exact),
        }
        for name, pairs in fixes.items():
            with self.subTest(name=name):
                self.assertValid("slow_query", edit(cases.ANY_CASE, "shop/inventory.py", *pairs), True)

    def test_a_fix_in_two_files_needs_no_git_header_lines(self):
        lookup = edit(cases.ANY_CASE, "shop/inventory.py", LOOKUP, LOOKUP.replace("lower(sku)", "sku"))
        note = edit(cases.ANY_CASE, "shop/web.py", "class Web:\n", "# Product pages read stock by code.\nclass Web:\n")
        self.assertNotIn("diff --git", lookup + note)
        self.assertValid("slow_query", lookup + note, True)

    def test_dropping_the_any_case_lookup_is_caught(self):
        wrong = edit(cases.ANY_CASE, "shop/inventory.py", "WHERE lower(sku) = lower(?)", "WHERE sku = ?")
        failed = self.assertValid("slow_query", wrong, False)["protected"]["failed"]
        self.assertEqual(failed, [RESERVATION_CHECK, "check_lookup.LookupChecks.test_any_case_finds_the_product"])


class InstrumentTest(unittest.TestCase):
    """A patch may not change what the checks and the replay measure with, nor reach the interpreter."""

    def test_cost_model_and_reporting_settings_are_refused(self):
        for old, new in (
            ("SCAN_US_PER_ROW = 300", "SCAN_US_PER_ROW = 0"),
            ("QUERY_MS = 4", "QUERY_MS = 0"),
            ("POOL_REPORT_MS = 15000", "POOL_REPORT_MS = 10**9"),
        ):
            with self.subTest(new=new), self.assertRaisesRegex(ValueError, "what the checks measure"):
                check_patch(edit(cases.ANY_CASE, "shop/config.py", old, new))
        with self.assertRaisesRegex(ValueError, "shop/db.py"):
            check_patch(edit(cases.ANY_CASE, "shop/db.py", "for table in SCAN.findall(plan)", "for table in []"))

    def test_settings_changes_the_text_rule_cannot_see_are_refused(self):
        for added in ("        del config.POOL_REPORT_MS\n", "        config.QUERY_MS, _unused = 0, 1\n"):
            patch = edit(
                cases.ANY_CASE,
                "shop/inventory.py",
                "    async def report_pool(self):\n",
                "    async def report_pool(self):\n" + added,
            )
            check_patch(patch)  # passes as text
            outcome = validate("slow_query", patch)
            with self.subTest(added=added):
                self.assertEqual((outcome["applies"], outcome["valid"]), (False, False))
                self.assertIn("setting", outcome["error"])

    def test_handing_a_timer_another_clock_is_refused(self):
        query, method = "db.query(self.runtime, connection, sql, params)", "    async def report_pool(self):\n"
        tries = {
            "alias": (method, method + "        _unused = db.query\n"),
            "other clock": (query, "db.query(self.telemetry, connection, sql, params)"),
            "keyword": (query, "db.query(connection=connection, sql=sql, params=params, runtime=self.telemetry)"),
            "unpacked": (query, 'db.query(connection=connection, sql=sql, params=params, **{"runtime": self.runtime})'),
            "rebound runtime": (method, method + "        self.runtime = self.telemetry\n"),
            "pool clock": ("Pool(runtime, connect", "Pool(self.telemetry, connect"),
        }
        for name, (old, new) in tries.items():
            outcome = validate("slow_query", edit(cases.ANY_CASE, "shop/inventory.py", old, new))
            with self.subTest(name=name):
                self.assertEqual((outcome["applies"], outcome["own_tests"]), (False, None))
                self.assertIn("clock", outcome["error"])

    def test_non_ascii_added_lines_are_refused(self):
        patch = edit(cases.ORDER_CAP, "shop/orders.py", "items per order", "items per order \u2014 sorry")
        with self.assertRaisesRegex(ValueError, "ASCII"):
            check_patch(patch)

    def test_reaching_the_interpreter_through_modules_is_refused(self):
        forge = """    async def report_pool(self):
        _s = db.re.enum.\uff53\uff59\uff53
        _s.stdout.write("{}")
"""
        tries = {
            "fullwidth name": forge,
            "module chain": forge.replace("\uff53\uff59\uff53", "bltns"),
            "stdlib module": "    async def report_pool(self):\n        _unused = db.re\n",
            "step count off": "    async def report_pool(self):\n        self.pool.set_progress_handler(None, 0)\n",
            "a class called": "    async def report_pool(self):\n        _unused = type(self.pool)\n",
            "split SQL": '        connection.execute("ATT" + "ACH DATABASE ? AS x", ("/tmp/x",))\n',
        }
        for name, added in tries.items():
            patch = edit(
                cases.ANY_CASE,
                "shop/inventory.py",
                "    async def report_pool(self):\n",
                added if name != "split SQL" else "    async def report_pool(self):\n" + added,
            )
            outcome = validate("slow_query", patch)
            with self.subTest(name=name):
                self.assertFalse(outcome["valid"])
                self.assertIsNone(outcome["own_tests"], outcome["error"])  # refused before anything ran


def decision(act, service="-", fault_type="-", commit="-", file="-", function="-", patch=""):
    return lambda cite: {
        **EMPTY,
        "act": act,
        "service": service,
        "fault_type": fault_type,
        "commit": commit,
        "file": file,
        "function": function,
        "confidence": 80,
        "evidence": cite,
        "summary": "s",
        "patch": patch,
    }


# Per case: the calls an investigator makes, and what to cite from their results.
STEPS = {
    "config": (
        (("deploys", "web", "-"), ("logs", "web", "warn")),
        (("commits", "web", "-"), ("diff", "-", cases.PER_PRODUCT)),
    ),
    "decoy": (
        (("deploys", "*", "-"), ("traces", "web", "error")),
        (("commits", "orders", "-"), ("diff", "-", cases.ORDER_CAP)),
    ),
    "slow_query": (
        (("deploys", "inventory", "-"), ("traces", "web", "slow")),
        (("commits", "inventory", "-"), ("diff", "-", cases.ANY_CASE)),
    ),
    "outside_lock": ((("deploys", "*", "-"), ("logs", "inventory", "error")), (("search", "inventory", "BEGIN"),)),
    "load_test": ((("deploys", "*", "-"), ("logs", "web", "warn")), (("metrics", "web", "http.server.request.count"),)),
}
TRUE = {
    "config": decision("patch", "web", "config_error", cases.PER_PRODUCT, "shop/config.py", patch=FIXES["config"]),
    "decoy": decision(
        "patch", "orders", "code_defect", cases.ORDER_CAP, "shop/orders.py", "Orders.place", FIXES["decoy"]
    ),
    "slow_query": decision(
        "patch", "inventory", "slow_query", cases.ANY_CASE, "shop/inventory.py", "stock", FIXES["slow_query"]
    ),
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
    model = scripted(*(calls(*step) for step in STEPS[name]), lambda p: act(cite(p)))
    return run_app_case(REPO, name, 1, model, RUNNER)


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
        retry = edit(cases.LOWER_CASED, "shop/inventory.py", 'f"database error: {exc}"', 'f"database busy: {exc}"')
        for act in (
            decision("dismiss"),
            decision("diagnose", "inventory", "code_defect", cases.LOWER_CASED, "shop/inventory.py"),
            decision("patch", "inventory", "code_defect", cases.LOWER_CASED, "shop/inventory.py", "_query", retry),
        ):
            with self.subTest(act=act([])["act"]):
                self.assertFalse(investigate("outside_lock", act)["resolved"])

    def test_the_wrong_function_or_commit_is_not_the_root_cause(self):
        right = dict(
            zip(
                ("act", "service", "fault_type", "commit", "file", "function"),
                ("diagnose", "orders", "code_defect", cases.ORDER_CAP, "shop/orders.py", "Orders.place"),
            )
        )
        for change in ({"function": "Orders.__init__"}, {"commit": cases.LOG_WORDING}):
            outcome = investigate("decoy", decision(**(right | change)))
            with self.subTest(change=change):
                self.assertFalse(outcome["location_correct"])
        self.assertTrue(investigate("decoy", decision(**right))["location_correct"])

    def test_a_valid_patch_to_another_file_is_not_a_fix_of_the_named_one(self):
        named = decision("patch", "inventory", "slow_query", cases.ANY_CASE, "shop/config.py", "-", FIXES["slow_query"])
        outcome = investigate("slow_query", named)
        self.assertEqual(
            (outcome["validation"]["valid"], outcome["patch_valid"], outcome["resolved"]), (True, False, False)
        )

    def test_escalating_the_load_test_is_safe_but_not_resolved(self):
        outcome = investigate("load_test", decision("escalate", "web"))
        self.assertEqual((outcome["decision"], outcome["resolved"]), ("escalate", False))


if __name__ == "__main__":
    unittest.main()
