import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from market_data import MarketDataError, closed_candles, fetch_candles, parse_candles


class MarketDataTests(unittest.TestCase):
    def test_parse_seven_and_eight_fields_in_time_order(self):
        data = parse_candles([
            ["1795107600000", "2", "3", "1", "2", "4", "8", "9"],
            ["1795104000000", "1", "2", "1", "2", "3", "6"],
        ])
        self.assertTrue(data.time.is_monotonic_increasing)
        self.assertEqual(data.quote_volume.tolist(), [6, 9])

    def test_reject_empty_and_malformed_rows(self):
        for rows in ([], [["bad"]], [["bad", "1", "2", "1", "2", "3", "6"]]):
            with self.subTest(rows=rows), self.assertRaises(MarketDataError):
                parse_candles(rows)

    def test_checks_closed_and_fresh_candles_on_each_use(self):
        start = pd.Timestamp("2026-11-19T12:00:00Z")
        times = pd.date_range(end=start, periods=51, freq="5min")
        candles = pd.DataFrame({"time": times})
        closed = closed_candles(candles, "5m", start + pd.Timedelta(minutes=2))
        self.assertEqual(len(closed), 50)
        with self.assertRaisesRegex(MarketDataError, "过期"):
            closed_candles(candles, "5m", start + pd.Timedelta(minutes=11))
        with self.assertRaisesRegex(MarketDataError, "晚于"):
            closed_candles(candles, "5m", start - pd.Timedelta(seconds=1))
        with self.assertRaisesRegex(MarketDataError, "不连续"):
            closed_candles(candles.drop(index=2), "5m", start + pd.Timedelta(minutes=5))

    @patch("market_data.time.sleep")
    @patch("market_data.requests.get")
    def test_retry_timeout_and_rate_limit_then_parse(self, get, sleep):
        get.side_effect = [
            requests.Timeout(),
            Mock(status_code=429, **{"json.return_value": {"code": "429"}}),
            Mock(status_code=200, **{"json.return_value": {
                "code": "00000", "data": [["1795104000000", "1", "2", "1", "2", "3", "6"]],
            }}),
        ]
        self.assertEqual(len(fetch_candles("SOLUSDT", "4H")), 1)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args.kwargs["params"], {
            "symbol": "SOLUSDT", "granularity": "4h", "limit": "200",
        })
        self.assertEqual(get.call_args.kwargs["timeout"], (3, 8))
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    @patch("market_data.requests.get")
    def test_http_400_reports_exchange_code_without_retry(self, get):
        get.return_value = Mock(status_code=400, **{"json.return_value": {"code": "400171"}})
        with self.assertRaisesRegex(MarketDataError, r"HTTP 400.*400171"):
            fetch_candles("BTCUSDT", "15m")
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
