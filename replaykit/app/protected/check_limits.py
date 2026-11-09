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

    def test_the_limit_holds_for_one_second_then_starts_again(self):
        # From a burst at 0, a fixed or a rolling window passes only if it is within 50 ms of one second.
        times = [0] * 6 + [950] + [1_050] * 6
        self.assertEqual(self.at(times), [200] * 5 + [503, 503] + [200] * 5 + [503])

    def at(self, times):
        """The status of one busy user's checkout started at each of *times* (ms), in that order."""
        done = {}

        async def buy(n):
            done[n] = await self.buy("bot")()

        for n, at in enumerate(times):
            self.app.runtime.spawn(buy(n), at=at)
        self.app.runtime.run(10_000)
        return [done.get(n) for n in range(len(times))]
