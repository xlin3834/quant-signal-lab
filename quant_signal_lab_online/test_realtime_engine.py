import json
import time
import unittest
from threading import Event
from unittest.mock import Mock, patch

import pandas as pd

from realtime_engine import SwapFeed, classify, lag_ok, normalize_swap_quote
from swap_data import CORE_SYMBOLS, PRODUCT, PRODUCT_LABEL


def ticker(stamp, price="123.5", bid="123.4", ask="123.6", symbol="BTCUSDT"):
    return {"symbol": symbol, "instId": symbol, "lastPr": price, "bidPr": bid, "askPr": ask, "ts": str(stamp)}


class RealtimeEngineTests(unittest.TestCase):
    def setUp(self):
        self.history = patch("realtime_engine.fetch_swap_candles", return_value=pd.DataFrame())
        self.history.start()
        self.addCleanup(self.history.stop)

    def test_core_symbols_only(self):
        feed = SwapFeed()
        with self.assertRaisesRegex(Exception, "BTCUSDT"):
            feed.request("SOLUSDT")
        feed.request("ETHUSDT")
        self.assertIn("ETHUSDT", feed.subscriptions())

    def test_rejects_spot_product_frames(self):
        feed = SwapFeed()
        feed.request("BTCUSDT")
        payload = {"arg": {"instType": "SPOT", "channel": "ticker", "instId": "BTCUSDT"},
                   "data": [ticker(int(time.time() * 1000))]}
        self.assertEqual(feed.ingest(json.dumps(payload)), "ignored")
        self.assertIsNone(feed.ticks.get("BTCUSDT"))

    def test_dedup_and_order(self):
        feed = SwapFeed()
        feed.request("BTCUSDT")
        now = int(time.time() * 1000)
        for stamp, price in ((now - 20, "100"), (now - 20, "100"), (now - 40, "99"), (now - 5, "101")):
            feed.ingest(json.dumps({"arg": {"instType": PRODUCT, "channel": "ticker", "instId": "BTCUSDT"},
                                    "action": "update", "data": [ticker(stamp, price)]}))
        tick = feed.ticks["BTCUSDT"]
        self.assertEqual(tick["timestamp"], now - 5)
        self.assertEqual(tick["price"], 101.0)
        self.assertEqual(tick["product_label"], PRODUCT_LABEL)
        self.assertGreaterEqual(len(feed.latencies["BTCUSDT"]), 2)

    def test_classify_does_not_label_rest_as_live(self):
        self.assertEqual(classify(True, None, "WebSocket", 400), "实时")
        self.assertEqual(classify(True, None, "WebSocket", 8_000), "延迟")
        self.assertEqual(classify(False, "TimeoutError", "REST 快照", 3_000), "延迟")
        self.assertEqual(classify(False, "TimeoutError", "REST 快照", 40_000), "过期")
        self.assertEqual(classify(False, "TimeoutError", None, None), "不可用")
        self.assertNotEqual(classify(False, None, "REST 快照", 1_000), "实时")

    def test_quote_has_bid_ask_and_clock_skew(self):
        quote = normalize_swap_quote(ticker(1000), received=1100)
        self.assertEqual(quote["bid"], 123.4)
        self.assertEqual(quote["ask"], 123.6)
        self.assertTrue(lag_ok(1000, 1100, 1500, now=2000))
        self.assertTrue(lag_ok(1100, 1000, 1500, now=2000))  # small clock skew
        self.assertFalse(lag_ok(1000, 1100, 50, now=2000))

    def test_candle_upsert_and_gap_count(self):
        feed = SwapFeed()
        feed.request("BTCUSDT")
        start = pd.Timestamp("2026-11-20T00:00:00Z")
        rows = []
        for i in (0, 1, 3):
            ts = int((start + pd.Timedelta(minutes=15 * i)).value // 1_000_000)
            rows.append([str(ts), "100", "101", "99", "100.5", "10", "1000"])
        feed.ingest(json.dumps({
            "arg": {"instType": PRODUCT, "channel": "candle15m", "instId": "BTCUSDT"},
            "action": "snapshot", "data": rows,
        }))
        key = ("BTCUSDT", "15m")
        self.assertEqual(len(feed.candles[key]), 3)
        self.assertEqual(feed.missing[key], 1)
        ts = int((start + pd.Timedelta(minutes=30)).value // 1_000_000)
        feed.ingest(json.dumps({
            "arg": {"instType": PRODUCT, "channel": "candle15m", "instId": "BTCUSDT"},
            "action": "update", "data": [[str(ts), "100", "102", "99", "101", "11", "1100"]],
        }))
        self.assertEqual(len(feed.candles[key]), 4)
        self.assertEqual(feed.missing[key], 0)

    def test_reconnects_and_resubscribes_perpetual_channels(self):
        feed, stop = SwapFeed(), Event()
        feed.clients["BTCUSDT"] = {"until": time.monotonic() + 120, "intervals": ("15m",)}
        failed, recovered = Mock(), Mock()
        failed.recv.side_effect = ConnectionError("network lost")

        def finish():
            stop.set()
            return json.dumps({"arg": {"instType": PRODUCT, "channel": "ticker", "instId": "BTCUSDT"},
                               "data": [ticker(int(time.time() * 1000))]})

        recovered.recv.side_effect = finish
        with patch("realtime_engine.websocket.create_connection", side_effect=(failed, recovered)) as connect:
            with patch.object(stop, "wait", return_value=False):
                feed.run(stop)
        self.assertEqual(connect.call_count, 2)
        sent = recovered.send.call_args.args[0]
        self.assertIn("USDT-FUTURES", sent)
        self.assertIn("BTCUSDT", sent)
        self.assertNotIn("SPOT", sent)
        self.assertGreaterEqual(feed.disconnects, 1)

    def test_snapshot_pauses_official_candidates_when_stale(self):
        feed = SwapFeed()
        feed.request("BTCUSDT")
        now = int(time.time() * 1000)
        feed.connected = False
        feed.rest_ticks["BTCUSDT"] = normalize_swap_quote(ticker(now - 40_000), received=now - 39_000, origin="REST 快照")
        snap = feed.snapshot("BTCUSDT", front_ms=now)
        self.assertEqual(snap["status"], "过期")
        self.assertFalse(snap["official_candidates_ok"])
        self.assertEqual(snap["product"], PRODUCT)
        self.assertIn("USDT", snap["source"])
