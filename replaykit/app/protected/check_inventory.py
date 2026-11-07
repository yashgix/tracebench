"""Protected checks (never shown to the investigator): inventory gives every connection back and still behaves."""

import itertools
import random
import unittest

from shop import db
from shop.inventory import Inventory
from shop.rpc import ServiceError
from shop.runtime import Runtime
from shop.telemetry import Telemetry

_names = itertools.count()


class InventoryChecks(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        connect = db.connector(f"protected-{next(_names)}")
        self.keeper = db.create(connect, {"sku-1": 3, "sku-2": 0})
        self.inventory = Inventory(self.runtime, Telemetry(self.runtime, random.Random(1)), connect)

    def call(self, coro):
        box = {}

        async def wrapper():
            try:
                box["value"] = await coro
            except ServiceError as exc:
                box["status"] = exc.status

        self.runtime.spawn(wrapper())
        self.runtime.run(self.runtime.now + 60_000)
        return box

    def stock(self, sku):
        return self.keeper.execute("SELECT stock FROM items WHERE sku = ?", (sku,)).fetchone()[0]

    def test_reserve_takes_stock_and_returns_the_connection(self):
        self.assertEqual(self.call(self.inventory.reserve("sku-1", 2)), {"value": 1})
        self.assertEqual((self.stock("sku-1"), self.inventory.pool.in_use), (1, 0))

    def test_out_of_stock_is_a_conflict_and_returns_the_connection(self):
        self.assertEqual(self.call(self.inventory.reserve("sku-1", 5)), {"status": 409})
        self.assertEqual((self.stock("sku-1"), self.inventory.pool.in_use), (3, 0))

    def test_unknown_product_returns_the_connection(self):
        self.assertEqual(self.call(self.inventory.reserve("nope", 1)), {"status": 404})
        self.assertEqual(self.inventory.pool.in_use, 0)

    def test_stock_lookup_returns_the_connection(self):
        self.assertEqual(self.call(self.inventory.stock("nope")), {"status": 404})
        self.assertEqual(self.call(self.inventory.stock("sku-1")), {"value": 3})
        self.assertEqual(self.inventory.pool.in_use, 0)

    def test_many_conflicts_leave_the_pool_usable(self):
        for _ in range(30):
            self.assertEqual(self.call(self.inventory.reserve("sku-2", 1)), {"status": 409})
        self.assertEqual(self.call(self.inventory.reserve("sku-1", 1)), {"value": 2})
        self.assertEqual(self.inventory.pool.in_use, 0)
