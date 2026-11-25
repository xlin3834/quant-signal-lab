"""Optional live checks. They never print trader names, addresses, or profit figures."""
import os
import unittest

from bitget_structure import load_structure
from freshness import now_ms
from hyperliquid_public import fetch_account
from okx_public import fetch_ranks
from platform_ui import _probe_binance


@unittest.skipUnless(os.environ.get("QSL_LIVE") == "1", "set QSL_LIVE=1 to call public endpoints")
class LivePublicSourceTests(unittest.TestCase):
    def test_public_sources_that_this_environment_can_reach(self):
        structure = load_structure("BTCUSDT")
        self.assertIsNotNone(structure["ticker"]["open_interest"])
        self.assertIsNotNone(structure["ticker"]["funding_rate"])
        self.assertTrue(structure["trades"])
        account = fetch_account("0x" + "0" * 40, received_ms=now_ms())
        self.assertEqual(account["grade"], "B")
        self.assertEqual(account["positions"], [])
        self.assertGreater(account["exchange_time_ms"], 0)
        ranks = fetch_ranks(page=1, limit=1)
        self.assertEqual(len(ranks["ranks"]), 1)
        self.assertIsNone(ranks["ranks"][0]["max_drawdown"])
        self.assertIn(len(ranks["ranks"][0]["unique_code"]), (16, 18))
        status, detail = _probe_binance()
        if "451" in detail:
            self.assertEqual(status, "区域不可用")
        else:
            self.assertIn(status, {"已连接", "连接失败", "区域不可用"})


if __name__ == "__main__":
    unittest.main()
