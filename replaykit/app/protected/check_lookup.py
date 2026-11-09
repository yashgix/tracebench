"""Protected checks (never shown to the investigator): product codes match in any case, quickly, in a big catalogue."""

import contextlib
import itertools
import re
import sqlite3
import unittest

from _shop import db_name, shop, statements, statuses
from shop.rpc import ServiceError
from shop.runtime import Future

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

    def test_a_lookup_shows_the_stock_in_the_database(self):
        # Trials one after another, one product each: reads after one or two overlapping reservations must match
        # the database, read on the check's own connection. The full sequence is in SPEC-4.
        name = db_name()
        app = shop(CATALOGUE, name)
        runtime, inventory, seen, allowed, products = app.runtime, app.inventory, {}, {}, {}

        async def ask(key, values, method, *args):
            allowed[key] = values
            try:
                seen[key] = await method(*args)
            except ServiceError as error:
                seen[key] = error.status

        def start(at, *step):
            done = Future()

            async def run():
                await ask(*step)
                done.resolve()

            runtime.spawn(run(), at=at)
            return done

        async def read_all(n, when, sku):
            for spell in SPELLINGS:
                await ask((n, when, spell.__name__), None, inventory.stock, spell(sku))

        async def trial(n, sku, stock, offset, first, during, second, at_once):
            gaps = (0,) if second is None else (0, second)
            left = {stock - 3 * k for k in range(1, len(gaps) + 1)}
            products[n] = sku, min(left)
            if first:
                await ask((n, "first"), {stock}, inventory.stock, first(sku))
            now = runtime.now
            reserving = [
                start(now + 4 + gap, (n, "reserve", i), left, inventory.reserve, sku, 3) for i, gap in enumerate(gaps)
            ]
            reading = start(now + offset, (n, "during"), {stock} | left, inventory.stock, during(sku))
            for done in reserving:
                await done
            if at_once:
                await read_all(n, "after", sku)
            await reading
            await runtime.sleep(60)
            await read_all(n, "later", sku)

        tries = itertools.product(range(13), (None, *SPELLINGS), SPELLINGS, (None, *range(9)), (True, False))
        plan = list(zip(((sku, stock) for sku, stock in CATALOGUE.items() if stock > 6), tries))

        async def trials():
            for n, ((sku, stock), args) in enumerate(plan):
                await trial(n, sku, stock, *args)
            return len(plan)

        # Simulated time costs nothing; an hour leaves room for lookups near the 50 ms limit.
        self.assertEqual(statuses(app, [(0, trials)], until=3_600_000), [3120])
        with contextlib.closing(sqlite3.connect(f"file:{name}?mode=memory&cache=shared", uri=True)) as own:
            held = dict(own.execute("SELECT sku, stock FROM items"))
        allowed |= {key: {held[products[key[0]][0]]} for key, values in allowed.items() if values is None}
        for n, (sku, left) in products.items():
            seen[n, "database"], allowed[n, "database"] = held[sku], {left}
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
        plans = [
            row[3]
            for sql in ran
            if not sql.startswith("EXPLAIN")
            for row in trusted.execute("EXPLAIN QUERY PLAN " + sql)
        ]
        # Any table name counts: an alias (`items AS i`) shows up as `SCAN i`.
        self.assertEqual({plan.split()[0] for plan in plans} & {"SEARCH", "SCAN"}, {"SEARCH"}, plans)
        # A lookup through the index takes tens of steps; walking the catalogue takes over 30,000.
        bounds = [0, *starts[1:], len(ran)]
        spent = [
            sum(n for sql, n in zip(ran[a:b], work[a:b]) if i or not INDEX.match(sql))
            for i, (a, b) in enumerate(zip(bounds, bounds[1:]))
        ]
        self.assertLessEqual(max(spent), STEP_BUDGET, (spent, plans))

    def test_an_unknown_product_is_not_found(self):
        self.assertEqual(statuses(self.app, [(0, lambda: self.app.web.product("nope"))]), [404])
