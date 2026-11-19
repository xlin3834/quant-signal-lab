import json
import time
import unittest
from threading import Event
from unittest.mock import Mock, patch

import pandas as pd

from comparison import compare
from live_market import Feed, normalize_quote
from market_data import MarketDataError
from quote_ui import fresh


def ticker(stamp, price="123.5"):
    return {"symbol": "BTCUSDT", "lastPr": price, "ts": str(stamp)}


def quotes():
    times = pd.date_range("2026-11-20T01:00:00Z", periods=4, freq="15min")
    return pd.DataFrame({"time": times, "close_time": times + pd.Timedelta(minutes=15),
                         "open": 100., "high": 102., "low": 99., "close": 101., "volume": 15.})


class MarketP0Tests(unittest.TestCase):
    def test_out_of_order_and_duplicate_tickers(self):
        feed = Feed()
        feed.request("BTCUSDT")
        now = int(time.time() * 1000)
        for stamp in (now - 20, now - 20, now - 40, now - 10):
            feed.ingest(json.dumps({"data": [ticker(stamp)]}))
        state = feed.details("BTCUSDT")
        self.assertEqual(state["samples"], 2)
        self.assertEqual(state["tick"]["timestamp"], now - 10)
        self.assertGreaterEqual(state["max_ms"], state["median_ms"])

    def test_reject_stale_and_unsubscribed_tickers(self):
        feed = Feed()
        now = int(time.time() * 1000)
        feed.ingest(json.dumps({"data": [ticker(now)]}))
        self.assertIsNone(feed.details("BTCUSDT")["tick"])
        feed.ingest(json.dumps({"data": [ticker(now - 20_000)]}))
        self.assertIsNone(feed.details("BTCUSDT")["tick"])
        with patch("live_market.time.monotonic", return_value=float("inf")):
            self.assertEqual(feed.subscriptions(), set())

    def test_reconnects_and_resubscribes(self):
        feed, stop = Feed(), Event()
        feed.request("BTCUSDT")
        failed, recovered = Mock(), Mock()
        failed.recv.side_effect = ConnectionError("network lost")

        def finish():
            stop.set()
            return json.dumps({"data": [ticker(int(time.time() * 1000))]})

        recovered.recv.side_effect = finish
        with patch("live_market.websocket.create_connection", side_effect=(failed, recovered)) as connect:
            with patch.object(stop, "wait", return_value=False):
                feed.run(stop)
        self.assertEqual(connect.call_count, 2)
        self.assertIn("BTCUSDT", recovered.send.call_args.args[0])
        self.assertEqual(feed.details("BTCUSDT")["samples"], 1)
        self.assertFalse(feed.connected)

    def test_quote_age_does_not_confuse_receive_delay(self):
        self.assertTrue(fresh(normalize_quote(ticker(1000), received=1100), 2000, 1500))
        self.assertFalse(fresh(normalize_quote(ticker(1000), received=1100), 3000, 1500))
        self.assertFalse(fresh(normalize_quote(ticker(1000), received=990), 1100, 1500))

    def test_comparison_lists_both_gap_and_changed_fields(self):
        actual = quotes()
        reference = actual.drop(index=0).copy()
        reference.loc[1, "close"] = 101.5
        reference.loc[2, "volume"] *= 2
        reference.loc[3, "close_time"] += pd.Timedelta(seconds=1)
        reference["time"] = reference.time.dt.tz_convert("Asia/Shanghai")
        matched, report = compare(actual, reference, compare_indicators=False)
        self.assertEqual(matched, 3)
        self.assertEqual(set(report["字段"]), {"开盘时间", "close", "volume", "close_time"})

    def test_reference_rejects_naive_time_and_duplicate(self):
        data = quotes()
        reference = data.copy()
        reference["time"] = reference.time.dt.tz_localize(None)
        with self.assertRaisesRegex(ValueError, "时区"):
            compare(data, reference)
        with self.assertRaisesRegex(ValueError, "重复"):
            compare(data, pd.concat([data, data.iloc[:1]]))

    def test_official_comparison_does_not_fill_missing_bars(self):
        from verify_market import compare_official

        data = quotes()
        with patch("verify_market.fetch_candles", return_value=data):
            with patch("verify_market.closed_candles", return_value=data):
                with patch("verify_market.request_data", return_value=[]):
                    with self.assertRaises(MarketDataError):
                        compare_official("BTCUSDT", "15m", 4)
