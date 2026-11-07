"""Protected checks (never shown to the investigator): an order over the cap is the customer's error, not a failure."""

import unittest

from _shop import shop, statuses


class OrderChecks(unittest.TestCase):
    def setUp(self):
        self.app = shop({"sku-1": 10})

    def checkout(self, quantity):
        (status,) = statuses(self.app, [(0, lambda: self.app.web.checkout("u1", "sku-1", quantity))])
        return status

    def stock(self):
        return self.app.keeper.execute("SELECT stock FROM items WHERE sku = 'sku-1'").fetchone()[0]

    def test_an_oversized_order_is_refused_as_a_client_error(self):
        self.assertTrue(400 <= self.checkout(3) < 500)
        self.assertEqual(self.stock(), 10)
        self.assertFalse(any(s["status"] == "ERROR" for s in self.app.telemetry.spans))

    def test_an_allowed_order_goes_through(self):
        self.assertEqual(self.checkout(2), 200)
        self.assertEqual(self.stock(), 8)
