"""Protected checks (never shown to the investigator): product codes match in any case, quickly, in a big catalogue."""

import unittest

from _shop import shop, statements, statuses

CATALOGUE = {f"sku-{i:03d}": i % 50 + 1 for i in range(6000)}
STEP_BUDGET = 200


class LookupChecks(unittest.TestCase):
    def setUp(self):
        self.app = shop(CATALOGUE)

    def test_any_case_finds_the_product(self):
        self.assertEqual(statuses(self.app, [(0, lambda: self.app.inventory.stock("SKU-007"))]), [8])
        self.assertEqual(statuses(self.app, [(0, lambda: self.app.web.product("Sku-4321"))]), [200])

    def test_a_lookup_in_a_large_catalogue_is_quick(self):
        finished = []

        async def look():
            await self.app.inventory.stock("sku-5999")
            finished.append(self.app.runtime.now)

        self.app.runtime.spawn(look())
        self.app.runtime.run(60_000)
        self.assertEqual(len(finished), 1)
        self.assertLessEqual(finished[0], 50)

    def test_a_lookup_reads_the_catalogue_through_its_index(self):
        # What SQLite ran and the work it did, seen from the check's side: no clock or plan label the app code
        # holds counts. The second lookup is measured, so an index the first one builds is allowed.
        with statements() as (ran, steps, own):
            app = shop(CATALOGUE)
            del ran[:]
            work = []

            async def measured():
                before = steps[0]
                stock = await app.inventory.stock("sku-4242")
                work.append(steps[0] - before)
                return stock

            self.assertEqual(statuses(app, [(0, lambda: app.inventory.stock("sku-5999")), (1_000, measured)]),
                             [50, 43])
            trusted = own()
        plans = [row[3] for sql in ran if not sql.startswith("EXPLAIN")
                 for row in trusted.execute("EXPLAIN QUERY PLAN " + sql)]
        # Any table name counts: an alias (`items AS i`) shows up as `SCAN i`.
        self.assertEqual({plan.split()[0] for plan in plans} & {"SEARCH", "SCAN"}, {"SEARCH"}, plans)
        # A lookup through the index takes tens of steps; walking the catalogue takes over 30,000.
        self.assertLessEqual(work[0], STEP_BUDGET, plans)

    def test_an_unknown_product_is_not_found(self):
        self.assertEqual(statuses(self.app, [(0, lambda: self.app.web.product("nope"))]), [404])
