import unittest
from unittest.mock import patch

import pandas as pd

from market_data import MarketDataError, fetch_history
from test_strategy import sample


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.data = sample(count=700)
        self.now = self.data.time.iloc[-1] + pd.Timedelta(minutes=15)
        self.cursors = []

    def page(self, url, params):
        limit = int(params["limit"])
        self.assertIn("/history-candles", url)
        self.assertLessEqual(limit, 200)
        cutoff = int(params["endTime"])
        self.cursors.append(cutoff)
        older = self.data[self.data.time.astype("int64") // 1_000_000 <= cutoff].tail(limit)
        return [[str(row.time.value // 1_000_000), str(row.open), str(row.high),
                 str(row.low), str(row.close), str(row.volume), "1000"]
                for row in older.itertuples()]

    def test_pagination_handles_inclusive_overlap(self):
        with patch("market_data.fetch_candles", return_value=self.data.tail(100)):
            with patch("market_data.request_data", side_effect=self.page):
                result = fetch_history("BTCUSDT", "15m", 500, self.now)
        self.assertEqual(len(result), 500)
        self.assertEqual(len(self.cursors), 3)
        self.assertTrue(all(a > b for a, b in zip(self.cursors, self.cursors[1:])))
        self.assertTrue(result.time.reset_index(drop=True).equals(self.data.time.tail(500).reset_index(drop=True)))

    def test_pagination_rejects_gap(self):
        data = self.data.tail(100)
        self.data = self.data.drop(index=599)
        with patch("market_data.fetch_candles", return_value=data):
            with patch("market_data.request_data", side_effect=self.page):
                with self.assertRaisesRegex(MarketDataError, "分页"):
                    fetch_history("BTCUSDT", "15m", 500, self.now)
