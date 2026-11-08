"""Protected checks (never shown to the investigator): product codes match in any case, quickly, in a big catalogue."""

import itertools
import re
import unittest

from _shop import shop, statements, statuses
from shop.rpc import ServiceError

CATALOGUE = {f"sku-{i:03d}": i % 50 + 1 for i in range(6000)}
STEP_BUDGET = 200
SPELLINGS = (str.lower, str.upper, str.title)  # sku-007, SKU-007, Sku-007
INDEX = re.compile(r"(?:\s|/\*.*?\*/|--[^\n]*\n)*CREATE\s+(?:UNIQUE\s+)?INDEX\b", re.I | re.S)


class LookupChecks(unittest.TestCase):
    def setUp(self):
        self.app = shop(CATALOGUE)

    def test_any_case_finds_the_product(self):
        self.assertEqual(statuses(self.app, [(0, lambda: self.app.inventory.stock("SKU-007"))]), [8])
        self.assertEqual(statuses(self.app, [(0, lambda: self.app.web.product("Sku-4321"))]), [200])

    def test_a_lookup_shows_a_reservation(self):
        # Read in one spelling or not at all, reserve, read in another at every offset into the reservation (that
        # read may show either stock), then read in all three spellings: each must show the reservation.
        inventory, requests, allowed = self.app.inventory, [], {}

        def call(key, values, at, method, *args):
            async def run():
                try:
                    return key, await method(*args)
                except ServiceError as error:
                    return key, error.status
            requests.append((at, run))
            allowed[key] = values

        tries = itertools.product(range(-4, 12), (None, *SPELLINGS), SPELLINGS)
        skus = (sku for sku, stock in CATALOGUE.items() if stock > 3)
        for n, ((offset, first, during), sku) in enumerate(zip(tries, skus)):
            start, stock, left = n * 100, CATALOGUE[sku], CATALOGUE[sku] - 3
            if first:
                call((n, "first"), {stock}, start, inventory.stock, first(sku))
            call((n, "reserve"), {left}, start + 20, inventory.reserve, sku, 3)
            call((n, "during"), {stock, left}, start + 20 + offset, inventory.stock, during(sku))
            for spell in SPELLINGS:
                call((n, spell.__name__), {left}, start + 80, inventory.stock, spell(sku))
        seen = dict(statuses(self.app, requests))
        self.assertEqual(seen.keys(), allowed.keys())
        self.assertEqual({key: value for key, value in seen.items() if value not in allowed[key]}, {})

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
        # holds counts. Building an index is left out of the first lookup's work, never of the later ones'.
        with statements() as (ran, work, own):
            app = shop(CATALOGUE)
            del ran[:], work[:]
            starts = []

            def lookup(sku):
                async def run():
                    starts.append(len(ran))
                    try:
                        return await app.inventory.stock(sku)
                    except ServiceError as error:  # what it answers is for the other checks
                        return error.status
                return run

            skus = ("sku-5999", "sku-4242", "Sku-4321", "sku-9999")  # the last one is not in the catalogue
            self.assertEqual(len(statuses(app, [(i * 10_000, lookup(sku)) for i, sku in enumerate(skus)])), 4)
            trusted = own()
        plans = [row[3] for sql in ran if not sql.startswith("EXPLAIN")
                 for row in trusted.execute("EXPLAIN QUERY PLAN " + sql)]
        # Any table name counts: an alias (`items AS i`) shows up as `SCAN i`.
        self.assertEqual({plan.split()[0] for plan in plans} & {"SEARCH", "SCAN"}, {"SEARCH"}, plans)
        # A lookup through the index takes tens of steps; walking the catalogue takes over 30,000.
        bounds = [0, *starts[1:], len(ran)]
        spent = [sum(n for sql, n in zip(ran[a:b], work[a:b]) if i or not INDEX.match(sql))
                 for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]
        self.assertLessEqual(max(spent), STEP_BUDGET, (spent, plans))

    def test_an_unknown_product_is_not_found(self):
        self.assertEqual(statuses(self.app, [(0, lambda: self.app.web.product("nope"))]), [404])
