"""Workbench honesty checks. Network is not required."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QSL_DISABLE_FEED", "1")

from fastapi.testclient import TestClient

from workbench.__main__ import bind_address
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


class BindTests(unittest.TestCase):
    def test_port_env_and_default_interface(self):
        self.assertEqual(bind_address(environ={}), ("0.0.0.0", 8787))
        self.assertEqual(bind_address(environ={"PORT": "10000"}), ("0.0.0.0", 10000))
        self.assertEqual(bind_address(port_arg=9000, environ={"PORT": "10000"}), ("0.0.0.0", 9000))
        with self.assertRaises(ValueError):
            bind_address(environ={"PORT": "0"})


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
        os.environ.pop("QSL_ADMIN_TOKEN", None)
        os.environ.pop("QSL_WRITE_LIMIT", None)
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
        self.assertEqual(list(by_id), ["BTC", "ETH", "SOL", "BNB", "NVDA", "XAU"])
        self.assertIsNone(by_id["NVDA"]["quote"])
        self.assertIsNone(by_id["XAU"]["quote"])
        self.assertIn("没有已授权", by_id["NVDA"]["reason"])
        self.assertIsNone(by_id["BTC"]["quote"]["price"])
        self.assertNotEqual(by_id["BTC"]["quote"]["freshness"], "实时")
        for key in ("transport_ms", "median_ms", "p95_ms", "samples"):
            self.assertIn(key, by_id["BTC"]["quote"])
        self.assertIsNone(by_id["NVDA"]["quote"])
        self.assertEqual(by_id["NVDA"]["source"], "未接通")
        self.assertIsNone(by_id["NVDA"]["updated_at"])
        self.assertEqual(by_id["NVDA"]["connection"], "未连接")
        self.assertIn("NVDAUSDT", by_id["NVDA"]["not_substitute"])
        self.assertEqual(by_id["XAU"]["freshness_label"], "无行情")
        self.assertIn("不能代替 XAU/USD", by_id["XAU"]["not_substitute"])
        settings = self.client.get("/api/settings").json()
        self.assertEqual(settings["scoring_weights"], [30, 20, 20, 15, 15])
        self.assertTrue(settings["weights_applied"])
        self.assertIn("会改变当前评分", settings["weights_note"])
        self.assertFalse(settings["gate_active"])
        self.assertIn("不再单独", settings["gate_note"])
        self.assertEqual(settings["chart_default"], ["ema10", "ema120"])
        self.assertTrue(any(item["name"] == "合约资金" and item["scored"] is False for item in settings["eight_factor_research"]))
        self.assertIn("不是已验收", settings["latency_note"])
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
        self.assertIn('id="latency"', blob)
        self.assertIn("median_ms", blob)
        self.assertIn("p95_ms", blob)
        self.assertIn("transport_ms", blob)
        self.assertIn("ema10: true", blob)
        self.assertIn("无法判断", blob)
        self.assertIn("做多", blob)


class ScoreRuleTests(unittest.TestCase):
    def frame(self, direction_4h=1, direction_1h=1):
        import pandas as pd
        from signals import Rules, score_frames

        row = {
            "ema7": 99, "rsi": 60, "macd": 1, "macd_signal": 0, "rsi_5m": 60,
            "vol_avg": 10, "atr": 1, "open": 99, "high": 101, "low": 98, "close": 100,
            "volume": 20, "direction_4H": direction_4h, "direction_1H": direction_1h,
            "swing_low": 95, "swing_high": 105, "support": 90, "resistance": 110,
            "far_low": 80, "far_high": 120,
        }
        return pd.DataFrame([row]), Rules(weights=(1, 1, 1, 1, 1)), score_frames

    def test_saved_weights_change_score(self):
        from signals import WEIGHTS

        frame, rules, score_frames = self.frame()
        self.assertEqual(tuple(WEIGHTS.values()), (30, 20, 20, 15, 15))
        self.assertNotEqual(rules.weights, tuple(WEIGHTS.values()))
        scored = score_frames(frame, rules)
        self.assertEqual(int(scored.score_long.iloc[0]), 5)

    def test_four_hour_disagreement_does_not_veto(self):
        from signals import Rules, score_frames

        frame, _rules, _score = self.frame(direction_4h=-1, direction_1h=1)
        scored = score_frames(frame, Rules())
        self.assertEqual(int(scored.score_long.iloc[0]), 70)
        self.assertTrue(bool(scored.valid_long.iloc[0]))
        self.assertEqual(int(scored.side.iloc[0]), 1)
        self.assertFalse(bool(scored.higher_tf_long.iloc[0]))


class DirectionAndMonitorTests(unittest.TestCase):
    def test_old_preset_names_stay_and_research_modes_are_added(self):
        from presets import BUILTINS, PRESET_NAMES

        for name in ("敏捷型", "均衡型", "稳健型", "震荡行情型", "自定义"):
            self.assertIn(name, PRESET_NAMES)
        self.assertEqual(BUILTINS["稳健型"].threshold, 80)
        self.assertEqual(BUILTINS["稳健初始"].threshold, 75)
        self.assertEqual(BUILTINS["保守"].threshold, 85)
        self.assertEqual(BUILTINS["超短线"].threshold, 55)
        self.assertEqual(BUILTINS["超短线"].weights, (20, 20, 15, 15, 10))

    def test_missing_scores_are_undecided_and_positions_are_parsed(self):
        from workbench.research import build_conclusion, parse_positions

        missing = build_conclusion(True, 0, None, None, "", "", "多", "空")
        self.assertEqual(missing["action"], "无法判断")
        decided = build_conclusion(True, 1, 70, 20, "动量", "", "空", "多", "", False)
        self.assertEqual(decided["action"], "做多")
        self.assertIn("4小时空", decided["higher_tf"])
        rows = parse_positions({
            "time": 1795700000000,
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "0.2", "entryPx": "100", "positionValue": "22",
                              "unrealizedPnl": "1.5", "leverage": {"value": 5}}},
                {"position": {"coin": "ETH", "szi": "-1", "entryPx": "10", "markPx": "9",
                              "unrealizedPnl": "-1", "leverage": {"value": 2}}},
                {"position": {"coin": "SOL", "szi": "0", "entryPx": "1"}},
            ],
        }, "0x1234567890abcdef1234567890abcdef12345678", "")
        self.assertEqual([row["side"] for row in rows], ["多", "空"])
        self.assertEqual(rows[0]["name"], "")
        self.assertTrue(rows[0]["label"].startswith("0x"))
        self.assertEqual(rows[1]["mark"], 9)
        self.assertNotIn("交易员", rows[0]["label"])

    def test_open_positions_keep_real_rows_and_skip_flat_accounts(self):
        from workbench.research import open_positions_from_states

        address = "0x" + "ab" * 20
        flat = "0x" + "cd" * 20
        shown, omitted, equities = open_positions_from_states([
            {"address": flat, "name": "", "payload": {"marginSummary": {"accountValue": "10"}, "assetPositions": [], "time": 1795700000000}},
            {"address": address, "name": "", "payload": {
                "time": 1795700000000,
                "marginSummary": {"accountValue": "2500"},
                "assetPositions": [{"position": {
                    "coin": "BTC", "szi": "1.5", "entryPx": "60000", "positionValue": "91500",
                    "unrealizedPnl": "1500", "leverage": {"value": 10},
                }}],
            }},
        ])
        self.assertEqual(equities[flat], "10")
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["coin"], "BTC")
        self.assertEqual(shown[0]["side"], "多")
        self.assertEqual(shown[0]["size"], 1.5)
        self.assertEqual(shown[0]["entry"], 60000)
        self.assertEqual(shown[0]["leverage"], 10)
        self.assertEqual(shown[0]["unrealized_pnl"], 1500)
        self.assertIsNotNone(shown[0]["mark"])
        self.assertEqual(omitted, [])

    def test_flat_wallet_route_returns_empty_positions(self):
        from unittest.mock import patch

        address = "0x" + "11" * 20
        payload = {"marginSummary": {"accountValue": "10"}, "assetPositions": [], "time": 1795700000000, "withdrawable": "10"}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        with patch("workbench.research.requests.post", return_value=Response()), \
             patch("workbench.research.perp_dex_names", return_value=[]):
            response = TestClient(create_app()).get("/api/monitor/wallet", params={"address": address})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["positions"], [])
        self.assertIn("没有持仓", body["flat_note"])
        self.assertEqual(body["account_value"], "10")

    def test_wallet_get_is_not_method_not_allowed(self):
        client = TestClient(create_app())
        response = client.get("/api/monitor/wallet", params={"address": "test"})
        self.assertNotEqual(response.status_code, 405)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["ok"], False)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["QSL_PRESET_PATH"] = str(root / "user_presets.json")
        os.environ["QSL_SETTINGS_PATH"] = str(root / "site_settings.json")
        os.environ["QSL_DISABLE_FEED"] = "1"
        os.environ.pop("QSL_ADMIN_TOKEN", None)
        os.environ.pop("QSL_WRITE_LIMIT", None)

    def tearDown(self):
        os.environ.pop("QSL_ADMIN_TOKEN", None)
        os.environ.pop("QSL_WRITE_LIMIT", None)
        self.tmp.cleanup()

    def current(self, client):
        return client.get("/api/settings").json()["current"]

    def test_remote_write_is_rejected_without_token(self):
        remote = TestClient(create_app(), client=("203.0.113.8", 4400))
        denied = remote.put("/api/settings", json=self.current(remote))
        self.assertEqual(denied.status_code, 403)
        self.assertIn("不能修改", denied.json()["error"])

    def test_loopback_can_write_until_token_is_configured(self):
        local = TestClient(create_app(), client=("127.0.0.1", 4401))
        saved = local.put("/api/settings", json=self.current(local))
        self.assertEqual(saved.status_code, 200)
        os.environ["QSL_ADMIN_TOKEN"] = "phase1-token"
        blocked = local.put("/api/settings", json=self.current(local))
        self.assertEqual(blocked.status_code, 403)
        allowed = local.put("/api/settings", json=self.current(local), headers={"X-Admin-Token": "phase1-token"})
        self.assertEqual(allowed.status_code, 200)

    def test_write_rate_limit(self):
        os.environ["QSL_WRITE_LIMIT"] = "2"
        client = TestClient(create_app(), client=("203.0.113.10", 4402))
        body = {
            "equity": 10000, "risk_pct": 1, "entry": 100, "stop": 95, "leverage": 1,
            "fee_pct": 0.05, "slip_pct": 0.03, "atr_mult": 1.5, "min_qty": 0.0001,
            "min_rr": 1.5, "mode": "固定止损价",
        }
        self.assertEqual(client.post("/api/risk", json=body).status_code, 200)
        self.assertEqual(client.post("/api/risk", json=body).status_code, 200)
        limited = client.post("/api/risk", json=body)
        self.assertEqual(limited.status_code, 429)


if __name__ == "__main__":
    unittest.main()
