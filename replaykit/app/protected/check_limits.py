"""Protected checks (never shown to the investigator): the checkout limit slows one busy user, not a product's buyers."""

import unittest

from _shop import shop, statuses


class LimitChecks(unittest.TestCase):
    def setUp(self):
        self.app = shop({"sku-1": 1000})

    def buy(self, user):
        return lambda: self.app.web.checkout(user, "sku-1", 1)

    def test_many_customers_can_buy_one_product(self):
        done = statuses(self.app, [(100 + n * 50, self.buy(f"user-{n}")) for n in range(12)])
        self.assertEqual(done, [200] * 12)

    def test_one_busy_user_is_slowed_down(self):
        done = statuses(self.app, [(100, self.buy("bot"))] * 8, until=1_000)
        self.assertIn(503, done)
        self.assertGreaterEqual(done.count(200), 5)

    def test_the_limit_ends_with_its_window(self):
        done = statuses(self.app, [(100, self.buy("bot"))] * 8 + [(5_100, self.buy("bot"))], until=10_000)
        self.assertEqual(done[-1], 200)
