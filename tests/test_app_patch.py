"""Milestone 4: patch rules, and validation that passes the reference fix and catches planted wrong fixes."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app_helpers import FIX, REPO, edit, needs_repo
from replaykit.app import cases, world
from replaykit.app.patch import PROTECTED, ROOT, check_patch, protected_result, validate_patch

LEAKY_RESERVE = """            connection = await self._connection()
            stock = await self._read_stock(connection, sku)
            self._check_available(sku, stock, quantity)
"""


def validate(patch, service="inventory", base=cases.LEAK):
    return validate_patch(REPO, cases.case("leak", 1), {"service": service, "patch": patch}, base,
                          cases.CASES["leak"]["protected"])


class CheckPatchTest(unittest.TestCase):
    def test_reference_fix_is_allowed(self):
        self.assertEqual(check_patch(FIX), (["shop/inventory.py"], 14))

    def test_rejected_patches(self):
        hunk = "@@ -1,1 +1,1 @@\n-a\n+b\n"
        rejected = {
            "tests": "--- a/tests/test_pool.py\n+++ b/tests/test_pool.py\n" + hunk,
            "locked telemetry": "--- a/shop/telemetry.py\n+++ b/shop/telemetry.py\n" + hunk,
            "new file": "--- /dev/null\n+++ b/shop/new.py\n" + hunk,
            "rename": "--- a/shop/web.py\n+++ b/shop/orders.py\n" + hunk,
            "mode change": "diff --git a/shop/web.py b/shop/web.py\nold mode 100644\nnew mode 100755\n",
            "binary": "--- a/shop/web.py\n+++ b/shop/web.py\nGIT binary patch\n",
            "an import": "--- a/shop/web.py\n+++ b/shop/web.py\n@@ -1,1 +1,1 @@\n-a\n+import unittest\n",
            "a dunder": "--- a/shop/web.py\n+++ b/shop/web.py\n@@ -1,1 +1,1 @@\n-a\n+x.__class__ = y\n",
            "too big": "--- a/shop/web.py\n+++ b/shop/web.py\n@@ -1,21 +1,21 @@\n" + "-a\n+b\n" * 21,
            "no change": "--- a/shop/web.py\n+++ b/shop/web.py\n",
            "prose": "Here is the fix:\n--- a/shop/web.py\n+++ b/shop/web.py\n" + hunk,
            "a lone CR": "--- a/shop/web.py\n+++ b/shop/web.py\n@@ -1,1 +1,2 @@\n a\n+X = 1\r-__import__('os')\n",
            "a form feed": "--- a/shop/web.py\n+++ b/shop/web.py\n@@ -1,1 +1,1 @@\n-a\n+X = 1\x0cY = 2\n",
            "SQL attach": "--- a/shop/web.py\n+++ b/shop/web.py\n@@ -1,1 +1,1 @@\n-a\n+c.execute(\"attach 'x' as e\")\n",
            "print and exit": "--- a/shop/web.py\n+++ b/shop/web.py\n@@ -1,1 +1,1 @@\n-a\n+print('{}') or exit()\n",
            "locked pool": "--- a/shop/pool.py\n+++ b/shop/pool.py\n" + hunk,
            "a misleading header": "diff --git a/shop/runtime.py b/shop/runtime.py\n" + FIX.split("\n", 1)[1],
            "empty": "",
        }
        for name, patch in rejected.items():
            with self.subTest(name), self.assertRaises(ValueError):
                check_patch(patch)


@needs_repo
class ProtectedChecksTest(unittest.TestCase):
    def run_checks(self, commit):
        with tempfile.TemporaryDirectory() as folder:
            world.export(REPO, commit, Path(folder))
            done = subprocess.run([sys.executable, "-I", "-B", str(ROOT / PROTECTED / "run.py"), folder,
                                   "check_inventory.py"],
                                  capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_fail_on_the_leak_and_pass_on_the_good_commit(self):
        leak = self.run_checks(cases.LEAK)
        self.assertEqual(leak["run"], 5)
        self.assertEqual({name.rsplit(".", 1)[1] for name in leak["failed"]},
                         {"test_out_of_stock_is_a_conflict_and_returns_the_connection",
                          "test_unknown_product_returns_the_connection", "test_many_conflicts_leave_the_pool_usable"})
        self.assertEqual(self.run_checks(cases.GOOD), {"run": 5, "failed": []})


@needs_repo
class ValidatePatchTest(unittest.TestCase):
    def assert_caught(self, result, *, own_tests="pass", protected=True, replay_clean=None):
        self.assertFalse(result["valid"])
        self.assertTrue(result["applies"])
        self.assertEqual(result["own_tests"], own_tests)
        self.assertEqual(bool(result["protected"]["failed"]), protected)
        if replay_clean is not None:
            self.assertEqual(result["replay"]["clean"], replay_clean)

    def test_reference_fix_is_valid(self):
        result = validate(FIX)
        self.assertEqual(result, {"files": ["shop/inventory.py"], "changed_lines": 14, "applies": True,
                                  "error": None, "own_tests": "pass", "protected": {"run": 5, "failed": []},
                                  "replay": {"page": False, "pool_in_use_end": 0, "web_errors": 0, "clean": True},
                                  "valid": True})

    def test_release_only_on_a_conflict_is_caught_by_the_protected_checks(self):
        patch = edit(cases.LEAK, "shop/inventory.py", LEAKY_RESERVE, LEAKY_RESERVE.replace(
            "            self._check_available", "            if stock < quantity:\n"
            "                self.pool.release(connection)\n            self._check_available"))
        self.assert_caught(validate(patch), replay_clean=True)

    def test_a_bigger_pool_is_caught(self):
        self.assert_caught(validate(edit(cases.LEAK, "shop/config.py", "POOL_SIZE = 5", "POOL_SIZE = 50")))

    def test_a_retry_is_caught(self):
        old = "            return await self.pool.acquire()\n"
        new = ("            try:\n                return await self.pool.acquire()\n"
               "            except PoolTimeout:\n                return await self.pool.acquire()\n")
        self.assert_caught(validate(edit(cases.LEAK, "shop/inventory.py", old, new)), replay_clean=False)

    def test_hiding_the_conflict_is_caught(self):
        patch = edit(cases.LEAK, "shop/inventory.py", 'raise ServiceError(409, f"out of stock: {sku}")', "return")
        self.assert_caught(validate(patch), own_tests="fail")

    def test_a_fix_in_the_wrong_service_leaves_the_page(self):
        patch = edit(cases.GOOD, "shop/web.py", "status = 200", "status = 200  # served")
        result = validate(patch, service="web", base=cases.GOOD)
        self.assert_caught(result, protected=False, replay_clean=False)
        self.assertTrue(result["replay"]["page"])

    def test_a_patch_that_does_not_apply(self):
        result = validate(edit(cases.GOOD, "shop/inventory.py", "    async def reserve", "    async def reserve "))
        self.assertEqual((result["applies"], result["valid"]), (False, False))
        self.assertTrue(result["error"].startswith("git apply failed"))

    def test_reaching_the_harness_is_refused_after_apply(self):
        patch = edit(cases.LEAK, "shop/inventory.py", "            self._check_available(sku, stock, quantity)\n",
                     "            self.telemetry.spans.clear()\n            self._check_available(sku, stock, quantity)\n")
        check_patch(patch)
        result = validate(patch)
        self.assertEqual((result["applies"], result["own_tests"], result["valid"]), (False, None, False))
        self.assertEqual(result["error"], "the patch adds .spans to shop/inventory.py")

    def test_patched_code_cannot_write_outside_its_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "escaped.db"
            sql = f"connection.execute(\"ATT\" + \"ACH DATABASE '{target}' AS e\")\n"
            patch = edit(cases.LEAK, "shop/inventory.py", "            self._check_available(sku, stock, quantity)\n",
                         "            " + sql + "            self._check_available(sku, stock, quantity)\n")
            result = validate(patch)
            self.assertFalse(target.exists())
        self.assertTrue(result["applies"])
        self.assertFalse(result["valid"])

    def test_an_incomplete_protected_run_fails(self):
        checks = cases.CASES["leak"]["protected"]
        self.assertEqual(protected_result({"run": 0, "failed": []}, checks), {"run": 0, "failed": ["incomplete"]})
        self.assertEqual(protected_result(None, checks), {"run": 0, "failed": ["incomplete"]})
        self.assertEqual(protected_result({"run": 5, "failed": []}, checks), {"run": 5, "failed": []})

    def test_a_forbidden_patch_never_runs(self):
        result = validate(FIX.replace("+            try:", "+            import os"))
        self.assertEqual((result["applies"], result["own_tests"]), (False, None))


if __name__ == "__main__":
    unittest.main()
