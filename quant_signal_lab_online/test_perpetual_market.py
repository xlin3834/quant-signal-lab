import unittest
from unittest.mock import patch

from market_data import MarketDataError
from perpetual_market import build_rows, load_directory, load_tickers, read_favorites, visible_rows

NOW = 1_795_113_000_000


def contract(symbol, **changes):
    return {"symbol": symbol, "baseCoin": symbol.removesuffix("USDT"), "symbolType": "perpetual",
            "symbolStatus": "normal", "quoteCoin": "USDT", "supportMarginCoins": ["USDT"],
            "minTradeNum": ".01", **changes}


def snapshot(symbol, change=".03", turnover="100", **changes):
    return {"symbol": symbol, "ts": str(NOW - 300), "lastPr": "10", "usdtVolume": turnover,
            "change24h": change, "fundingRate": ".0002", "high24h": "12", "low24h": "8",
            "bidPr": "9.99", "askPr": "10.01", **changes}


class PerpetualMarketTests(unittest.TestCase):

    def test_contract_state_and_product_filters(self):
        raw = [contract("AAAUSDT"), contract("PAUSED", symbolStatus="maintain"),
               contract("DELIVERY", symbolType="delivery"), contract("OTHER", quoteCoin="USDC"),
               contract("MOCK", supportMarginCoins=["USDC"])]
        with patch("perpetual_market.request_data", return_value=raw) as request:
            self.assertEqual(list(load_directory()), ["AAAUSDT"])
            self.assertEqual(request.call_args.args[1], {"productType": "USDT-FUTURES"})

    def test_bad_payload_is_not_an_empty_market(self):
        with patch("perpetual_market.request_data", return_value={"bad": 1}):
            with self.assertRaises(MarketDataError):
                load_directory()
            with self.assertRaises(MarketDataError):
                load_tickers()

    def test_join_and_units_use_latest_ticker_and_official_usdt_turnover(self):
        rows = build_rows({"AAAUSDT": contract("AAAUSDT"), "BBBUSDT": contract("BBBUSDT")},
                          [snapshot("AAAUSDT", ts=str(NOW - 1000), lastPr="5"),
                           snapshot("AAAUSDT", quoteVolume="900"),
                           snapshot("UNLISTED")])
        self.assertEqual(len(rows), 2)
        first, missing = rows
        self.assertEqual((first["price"], first["turnover"], first["change"], first["funding"]),
                         (10., 100., 3., .02))
        self.assertAlmostEqual(first["spread_pct"], .2)
        self.assertAlmostEqual(first["range_pct"], 50)
        self.assertIsNone(missing["price"])
        self.assertIsNone(missing["timestamp"])

    def test_rankings_sort_and_exclude_stale_or_missing_data(self):
        data = [snapshot("AAAUSDT", ".10", "100"), snapshot("BBBUSDT", ".02", "200"),
                snapshot("CCCUSDT", "-.07", "400"), snapshot("DDDUSDT", "-.04", "300"),
                snapshot("EEEUSDT", ".99", "900", ts=str(NOW - 120_000))]
        rows = build_rows({t["symbol"]: contract(t["symbol"]) for t in data}, data)
        expected = {"涨幅榜": ["AAAUSDT", "BBBUSDT"], "跌幅榜": ["CCCUSDT", "DDDUSDT"],
                    "成交额排行": ["CCCUSDT", "DDDUSDT", "BBBUSDT", "AAAUSDT"]}
        for view, symbols in expected.items():
            self.assertEqual([r["symbol"] for r in visible_rows(rows, "", view, (), NOW)], symbols)
        self.assertEqual(visible_rows(rows, "EEE", "全部合约", (), NOW)[0]["state"], "过期")

    def test_search_favorites_and_liquidity(self):
        data = [snapshot("AAAUSDT", turnover="200"), snapshot("AAXUSDT", turnover="400", bidPr="9.0")]
        rows = build_rows({t["symbol"]: contract(t["symbol"]) for t in data}, data)
        favorites = read_favorites("AAAUSDT,AAXUSDT,AAAUSDT,<bad>")
        self.assertEqual(favorites, ("AAAUSDT", "AAXUSDT"))
        matching = visible_rows(rows, " aa ", "我的自选", favorites, NOW, 150, .25)
        self.assertEqual([r["symbol"] for r in matching], ["AAAUSDT"])

    def test_nonfinite_future_and_crossed_quotes(self):
        data = [snapshot("AAAUSDT", ts=str(NOW + 3000), lastPr="NaN", usdtVolume="inf",
                         bidPr="12", askPr="9")]
        row = build_rows({"AAAUSDT": contract("AAAUSDT")}, data)[0]
        self.assertIsNone(row["price"])
        self.assertIsNone(row["turnover"])
        self.assertIsNone(row["spread_pct"])
        future = {**row, "price": 10}
        self.assertEqual(visible_rows([future], "", "全部合约", (), NOW)[0]["state"], "时钟异常")
