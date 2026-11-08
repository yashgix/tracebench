"""Protected checks (never shown to the investigator): product codes match in any case, quickly, in a big catalogue."""

import unittest

from _shop import shop, statuses


class LookupChecks(unittest.TestCase):
    def setUp(self):
        self.app = shop({f"sku-{i:03d}": i % 50 + 1 for i in range(6000)})

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

    def test_an_unknown_product_is_not_found(self):
        self.assertEqual(statuses(self.app, [(0, lambda: self.app.web.product("nope"))]), [404])
