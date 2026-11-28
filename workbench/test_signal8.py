"""Signal Lab 8.0 crosses, watchlist, and alert delivery."""
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from workbench.crosses import (
    ACTIVE_ALERTS,
    ARCHIVED_ALERTS,
    confirmed_crosses,
    rank_by_turnover,
)
from workbench.monitor_worker import evaluate_symbol
from workbench.notify import deliver, format_message, set_channel
from workbench.server import create_app
from workbench.watchlist import mutate


def _bars(closes):
    return [{"t": index * 900_000, "c": value, "h": value, "l": value} for index, value in enumerate(closes)]


class CrossTests(unittest.TestCase):
    def test_only_six_active_alerts(self):
        self.assertEqual(len(ACTIVE_ALERTS), 6)
        self.assertNotIn("EMA9×EMA20金叉死叉", ACTIVE_ALERTS)
        self.assertIn("80分候选", ARCHIVED_ALERTS)

    def test_confirmed_cross_does_not_repaint(self):
        closes = [100.0] * 40 + [130.0]
        early = confirmed_crosses(_bars(closes))
        later = confirmed_crosses(_bars(closes + [100.0]))
        self.assertTrue(early)
        self.assertEqual(
            [(item["index"], item["label"]) for item in early],
            [(item["index"], item["label"]) for item in later if item["index"] < len(closes)],
        )

    def test_ema120_is_not_an_order(self):
        closes = [100.0] * 140 + [180.0]
        events = [item for item in confirmed_crosses(_bars(closes)) if item["ema"] == 120]
        self.assertTrue(events)
        self.assertIn("不是开仓指令", events[-1]["note"])

    def test_rank_refuses_mixed_units_and_missing_turnover(self):
        ranked, blocked = rank_by_turnover([
            {"symbol": "A", "turnover": 2, "turnover_unit": "USDT"},
            {"symbol": "B", "turnover": 9, "turnover_unit": "USD"},
        ])
        self.assertEqual(ranked, [])
        self.assertIn("不能混排", blocked[0]["rank_reason"])
        ranked, blocked = rank_by_turnover([
            {"symbol": "A", "turnover": 5, "turnover_unit": "USDT"},
            {"symbol": "B", "turnover": None, "turnover_unit": "USDT"},
        ])
        self.assertEqual([row["symbol"] for row in ranked], ["A"])
        self.assertIsNone(blocked[0]["rank"])
        self.assertIn("无可靠成交额", blocked[0]["rank_reason"])


class WatchlistTests(unittest.TestCase):
    def test_add_mute_and_no_six_cap(self):
        path = Path(tempfile.mkdtemp()) / "watchlist.json"
        for index in range(8):
            mutate("add", f"ABC{index}USDT", path=path)
        payload = mutate("mute", "ABC0USDT", path=path)
        self.assertEqual(len(payload["symbols"]), 8)
        self.assertTrue(payload["symbols"][0]["muted"])
        self.assertEqual(payload["symbols"][1]["timeframes"], ["5m", "15m"])


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["QSL_ALERT_PREFS"] = str(Path(self.tmp.name) / "channels.json")
        os.environ["QSL_WATCHLIST_PATH"] = str(Path(self.tmp.name) / "watch.json")
        os.environ["QSL_DISABLE_FEED"] = "1"
        os.environ.pop("QSL_SMTP_HOST", None)
        os.environ.pop("QSL_ALERT_EMAIL_TO", None)
        os.environ.pop("QSL_TELEGRAM_BOT_TOKEN", None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_email_is_not_sent_without_secrets(self):
        set_channel("email", True)
        result = deliver("email", format_message({"symbol": "BTCUSDT", "interval": "15m", "label": "EMA7上穿布林中轨", "ema": 7}))
        self.assertFalse(result["sent"])
        self.assertIn("未配置", result["reason"])

    def test_worker_baselines_history(self):
        closes = [100.0] * 40 + [130.0]
        bars = _bars(closes)
        state = {"initialized": {}, "seen": []}
        first = evaluate_symbol("BTCUSDT", "15m", bars, state)
        second = evaluate_symbol("BTCUSDT", "15m", bars, state)
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        third = evaluate_symbol("BTCUSDT", "15m", _bars(closes + [70.0]), state)
        self.assertTrue(third)
        again = evaluate_symbol("BTCUSDT", "15m", _bars(closes + [70.0]), state)
        self.assertEqual(again, [])

    def test_public_status_does_not_claim_always_on(self):
        client = TestClient(create_app())
        body = client.get("/api/alerts/status").json()
        self.assertFalse(body["runtime"]["web_process_is_24h"])
        self.assertTrue(body["runtime"]["render_free_sleeps"])
        self.assertIn("monitor_worker", body["runtime"]["always_on_command"])
        self.assertIn("EMA7上穿布林中轨", body["channels"]["active_alerts"])
        self.assertIn("趋势切换", body["channels"]["archived_alerts"])
        health = client.get("/api/health").json()
        self.assertFalse(health["cloud_alerts"])
        rows = client.get("/api/monitor", params={"sort": "account", "limit": 1}).json()
        self.assertIn("account_value_note", rows)


class LeaderboardNameTests(unittest.TestCase):
    def test_missing_display_name_is_not_invented(self):
        from workbench.research import parse_leaderboard, trader_label
        address = "0x" + "ab" * 20
        rows = parse_leaderboard({"leaderboardRows": [
            {"ethAddress": address, "accountValue": "9", "displayName": None, "windowPerformances": []},
        ]})
        self.assertIsNone(rows[0]["display_name"])
        self.assertEqual(rows[0]["name_source"], "address")
        self.assertEqual(rows[0]["name"], "0xabab…abab")
        self.assertNotIn("交易员", rows[0]["name"])
        self.assertIsNone(rows[0]["win_rate"])
        self.assertEqual(rows[0]["stats_note"], "暂无数据")
        named = parse_leaderboard({"leaderboardRows": [
            {"ethAddress": address, "accountValue": "9", "displayName": "Desk", "windowPerformances": []},
        ]})
        self.assertEqual(named[0]["name"], "Desk")
        self.assertEqual(named[0]["name_source"], "displayName")
        self.assertEqual(trader_label("", "备注甲", address), ("备注甲", "note"))
        self.assertEqual(trader_label("Desk", "备注甲", address)[0], "Desk")


class ScannerAndMonitorTests(unittest.TestCase):
    def test_cross_time_is_shanghai_and_unscored_rows_have_no_lean(self):
        from unittest.mock import patch
        from workbench.research import beijing, unscored_stance
        self.assertEqual(beijing(1714521600000), "2024-05-01 08:00:00")
        with patch("workbench.research.radar") as radar:
            stance = unscored_stance("DOGEUSDT")
            core = unscored_stance("BTCUSDT")
            radar.assert_not_called()
        self.assertFalse(stance["scored"])
        self.assertIsNone(stance["lean"])
        self.assertIn("不编造", stance["reason"])
        self.assertIsNone(core["lean"])
        self.assertTrue(core["pending"])

    def test_monitor_upstream_failure_stays_ok(self):
        import requests
        from threading import Thread
        from unittest.mock import patch
        from workbench import research

        research._BOARD.clear()
        research._BOARD.update(
            at=0.0, rows=None, error=None, positions=[], omitted=[], equities={},
            refreshing=False, event=None, fetched_at=None, positions_checked=False,
            position_error=None,
        )

        class Immediate(Thread):
            def start(self):
                self.run()

        with patch("workbench.research.Thread", Immediate), \
             patch("workbench.research._load_leaderboard", side_effect=requests.ConnectionError("down")):
            body = research.monitor(wait_seconds=0)
        self.assertTrue(body["ok"])
        self.assertIn("暂时取不到", body["error"] or "")
        self.assertEqual(body["rows"], [])
        self.assertEqual(body["positions"], [])
        self.assertFalse(body["positions_pending"])
        self.assertIn("不是你的 Bitget 账户", body["notice"])


if __name__ == "__main__":
    unittest.main()
