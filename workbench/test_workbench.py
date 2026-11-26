"""Workbench honesty checks. Network is not required."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QSL_DISABLE_FEED", "1")

from fastapi.testclient import TestClient

from workbench.catalog import (
    badge_for,
    classify_contract,
    filter_catalog,
    gap_rows,
    overlay_live,
    summarize,
)
from workbench.research import parse_calendar, parse_leaderboard, reference_position
from workbench.server import create_app
from workbench.store import (
    apply_named_preset,
    list_custom,
    load_preset,
    load_settings,
    preset_file,
    save_named_custom,
    save_settings,
    sync_shortcut,
)


def sample_row(symbol, base, rwa, price=10, freshness="快照"):
    meta = classify_contract(base, "YES" if rwa else "NO")
    return {
        "symbol": symbol, "base": base, "price": price, "turnover": 1000, "change": 1,
        "freshness": freshness, "timestamp": 1, **meta,
    }


class CatalogTests(unittest.TestCase):
    def rows(self):
        return [
            sample_row("BTCUSDT", "BTC", False, 60000),
            sample_row("XAUUSDT", "XAU", True, 4000),
            sample_row("XAUTUSDT", "XAUT", True, 4001),
            sample_row("PAXGUSDT", "PAXG", True, 4002),
            sample_row("FOXAUSDT", "FOXA", True, 30),
            sample_row("NVDAUSDT", "NVDA", True, 180),
        ]

    def test_xau_search_does_not_match_foxa(self):
        found, note = filter_catalog(self.rows(), gap_rows(), "XAU")
        self.assertIsNone(note)
        symbols = [row["symbol"] for row in found]
        self.assertIn("XAUUSDT", symbols)
        self.assertIn("XAUTUSDT", symbols)
        self.assertIn("XAU/USD", symbols)
        self.assertNotIn("FOXAUSDT", symbols)
        self.assertNotIn("PAXGUSDT", symbols)
        spot = next(row for row in found if row["symbol"] == "XAU/USD")
        self.assertIsNone(spot["price"])
        self.assertEqual(spot["freshness"], "数据源未接通")
        self.assertIn("不是现货", next(row["product_label"] for row in found if row["symbol"] == "XAUUSDT"))

    def test_nvda_cash_and_perp_stay_distinct(self):
        found, _note = filter_catalog(self.rows(), gap_rows(), "NVDA")
        cash = next(row for row in found if row["symbol"] == "NVDA")
        perp = next(row for row in found if row["symbol"] == "NVDAUSDT")
        self.assertIsNone(cash["price"])
        self.assertEqual(cash["product_class"], "cash_equity")
        self.assertIn("不是正股", perp["product_label"])
        self.assertEqual(perp["product_class"], "tokenized_equity")

    def test_anomaly_count_matches_the_same_rows(self):
        rows = [
            {"freshness": "数据异常"},
            {"freshness": "数据源未接通"},
            {"freshness": "快照"},
            {"freshness": "过期"},
        ]
        summary = summarize(rows)
        self.assertEqual(summary["anomaly"], 1)
        self.assertEqual(summary["unconnected"], 1)
        self.assertEqual(summary["stale"], 1)
        self.assertEqual(sum(1 for row in rows if row["freshness"] == "数据异常"), summary["anomaly"])

    def test_live_price_replaces_snapshot_price(self):
        rows = [sample_row("BTCUSDT", "BTC", False, price=1)]
        snaps = {"BTCUSDT": {"price": 62000.5, "freshness": "实时", "origin": "WebSocket",
                             "official_candidates_ok": True, "age_ms": 320, "transport_ms": 310}}
        merged = overlay_live(rows, snaps)
        self.assertEqual(merged[0]["price"], 62000.5)
        self.assertEqual(merged[0]["freshness"], "实时")

    def test_rest_snapshot_is_not_called_live(self):
        badge = badge_for(
            {"price": 1, "freshness": "延迟", "origin": "REST 快照"},
            {"price": 1, "freshness": "延迟", "origin": "REST 快照"},
        )
        self.assertEqual(badge["level"], "delay")
        self.assertNotIn("实时有效", badge["text"])
        live = badge_for(
            {"price": 1, "freshness": "实时", "origin": "WebSocket"},
            {"price": 1, "freshness": "实时", "origin": "WebSocket"},
        )
        self.assertEqual(live["level"], "live")

    def test_other_exchange_is_not_faked(self):
        rows, note = filter_catalog(self.rows(), gap_rows(), "", "", "okx")
        self.assertEqual(rows, [])
        self.assertIn("未接通", note)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["QSL_PRESET_PATH"] = str(root / "user_presets.json")
        os.environ["QSL_SETTINGS_PATH"] = str(root / "site_settings.json")
        os.environ["QSL_DISABLE_FEED"] = "1"

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_does_not_create_or_overwrite_saved_profiles(self):
        apply_named_preset("敏捷型")
        self.assertFalse(preset_file().exists())
        current = load_settings()
        self.assertEqual(current["modules"]["intraday"]["ema"][0], 7)
        self.assertEqual(current["modules"]["ema_boll"]["leverage"], 1)
        self.assertEqual(current["modules"]["ema_boll"]["risk_pct"], 1)
        save_named_custom("我的配置")
        saved = load_preset("我的配置", preset_file())
        self.assertEqual(saved.ema[0], 7)
        before = preset_file().read_bytes()
        apply_named_preset("均衡型")
        self.assertEqual(preset_file().read_bytes(), before)
        self.assertEqual(load_settings()["modules"]["intraday"]["ema"], [9, 20, 50, 200])
        self.assertEqual(load_preset("我的配置", preset_file()).ema[0], 7)
        self.assertIn("我的配置", list_custom(preset_file()))

    def test_shortcut_sync_keeps_long_ma(self):
        current = load_settings()
        current["modules"]["shortcut"]["short"] = 8
        current["modules"]["shortcut"]["long"] = 21
        current["modules"]["shortcut"]["rsi"] = 10
        save_settings(current)
        synced = sync_shortcut(load_settings())
        self.assertEqual(synced["modules"]["intraday"]["ema"][0], 8)
        self.assertEqual(synced["modules"]["intraday"]["ema"][1], 20)
        self.assertEqual(synced["modules"]["intraday"]["rsi"], 10)
        self.assertEqual(synced["modules"]["shortcut"]["long"], 21)

    def test_empty_number_is_rejected(self):
        current = load_settings()
        current["modules"]["intraday"]["rsi"] = None
        with self.assertRaises(Exception):
            save_settings(current)
        self.assertIsNotNone(load_settings()["modules"]["intraday"]["rsi"])


class ResearchPureTests(unittest.TestCase):
    def test_risk_uses_budget_not_leverage_to_size(self):
        result = reference_position({
            "equity": 10000, "risk_pct": 1, "entry": 100, "stop": 95,
            "leverage": 5, "fee_pct": 0.05, "slip_pct": 0.03,
            "atr_mult": 1.5, "min_qty": 0.0001, "min_rr": 1.5, "mode": "固定止损价",
        })
        self.assertLess(result["qty"], 25)
        self.assertAlmostEqual(result["qty"] * result["risk_per_unit"], result["risk_budget"], delta=result["risk_per_unit"])
        self.assertIn("不含资金费", result["cost_note"])
        self.assertIn("不会下单", result["order_note"])

    def test_calendar_parser_keeps_source_fields(self):
        events = parse_calendar([
            {"title": "CPI", "country": "USD", "date": "2026-11-26T08:30:00-05:00", "impact": "High", "forecast": "0.2", "previous": "0.1"},
            {"title": "", "date": "2026-11-26T00:00:00Z"},
        ], now="2026-11-26T18:00:00Z")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "CPI")
        self.assertTrue(events[0]["past"])

    def test_leaderboard_parser_limits_rows(self):
        payload = {"leaderboardRows": [
            {"ethAddress": "0xabc", "accountValue": "2", "displayName": "a",
             "windowPerformances": [["day", {"pnl": "1", "roi": "0.1", "vlm": "3"}]]},
            {"ethAddress": "0xdef", "accountValue": "9", "displayName": None,
             "windowPerformances": [["day", {"pnl": "4", "roi": "0.2", "vlm": "8"}]]},
        ]}
        rows = parse_leaderboard(payload, sort="account", limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account_value"], 9)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["QSL_PRESET_PATH"] = str(root / "user_presets.json")
        os.environ["QSL_SETTINGS_PATH"] = str(root / "site_settings.json")
        os.environ["QSL_DISABLE_FEED"] = "1"
        self.client = TestClient(create_app())

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_and_core_assets_are_honest(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("市场工作台", page.text)
        self.assertNotIn("Edit site", page.text)
        self.assertNotIn("chatgpt", page.text.lower())
        core = self.client.get("/api/core").json()
        by_id = {item["id"]: item for item in core["assets"]}
        self.assertEqual(list(by_id), ["BTC", "ETH", "NVDA", "XAU"])
        self.assertIsNone(by_id["NVDA"]["quote"])
        self.assertIsNone(by_id["XAU"]["quote"])
        self.assertIn("没有已授权", by_id["NVDA"]["reason"])
        self.assertIsNone(by_id["BTC"]["quote"]["price"])
        self.assertNotEqual(by_id["BTC"]["quote"]["freshness"], "实时")
        health = self.client.get("/api/health").json()
        self.assertFalse(health["trading"])
        self.assertFalse(health["cloud_alerts"])

    def test_blocked_chart_has_no_candles(self):
        body = self.client.get("/api/chart", params={"symbol": "NVDA"}).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["bars"], [])
        self.assertTrue(body["blocked"])

    def test_static_copy_has_no_login_wall(self):
        root = Path(__file__).resolve().parent / "static"
        blob = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*") if path.suffix in {".html", ".js", ".css"})
        self.assertNotIn("Edit site", blob)
        self.assertNotIn("chatgpt", blob.lower())


if __name__ == "__main__":
    unittest.main()
