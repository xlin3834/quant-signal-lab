import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backtest import Costs, simulate
from bitget_structure import load_structure, normalize_symbol
from evidence import bitget_symbol_for, crosscheck, screenshot_evidence, social_evidence
from freshness import can_call_current, classify, status_sentence
from github_public import normalize_repo, score_repo
from hyperliquid_public import extract_address, parse_portfolio, parse_positions, position_changes, win_rate_from_fills
from mean_reversion import score_mean_reversion
from okx_public import normalize_rank, parse_data_ver, win_rate
from platform_status import MODULES, VENUES
from presets import DEFAULTS, from_session
from research_http import request_json
from research_store import add_article, add_snapshot, connect, is_favorite, load_presets, recent_snapshots, save_preset, set_favorite
from signals import Rules, ordered_weights, score_frames


def _response(payload, status=200):
    class Response:
        status_code = status
        text = json.dumps(payload)

        def json(self):
            return payload
    return Response()


class FreshnessTests(unittest.TestCase):
    def test_stale_snapshot_cannot_be_called_current(self):
        status, latency = classify(1_000, 1_500, 120_000, 60_000)
        self.assertEqual(status, "过期")
        self.assertEqual(latency, 500)
        self.assertFalse(can_call_current(status))
        self.assertIn("不能当作实时", status_sentence(status))

    def test_missing_exchange_time_is_not_live(self):
        status, _ = classify(None, 1_000, 2_000, 60_000)
        self.assertEqual(status, "无行情时间")
        self.assertIn("不能称为实时", status_sentence(status))


class EvidenceTests(unittest.TestCase):
    def test_screenshot_and_social_are_not_verified_positions(self):
        shot = screenshot_evidence()
        social = social_evidence()
        self.assertEqual(shot["grade"], "D")
        self.assertEqual(social["grade"], "C")
        self.assertIn("不能当作已验证持仓", shot["label"])
        self.assertIn("不能当作已验证持仓", social["label"])

    def test_price_crosscheck_does_not_upgrade_grade(self):
        result = crosscheck(101, 100)
        self.assertAlmostEqual(result["diff_pct"], 1)
        self.assertFalse(result["upgrades_grade"])
        self.assertIn("不能证明", result["note"])
        self.assertEqual(bitget_symbol_for("ETH-USDT-SWAP"), "ETHUSDT")
        self.assertIsNone(bitget_symbol_for("xyz:TSLA"))
        self.assertIsNone(bitget_symbol_for("@107"))


class PresetTests(unittest.TestCase):
    def test_balanced_preset_matches_existing_defaults(self):
        config = DEFAULTS["均衡"]
        self.assertEqual(config.style, "trend")
        self.assertEqual((config.threshold, config.strong, config.minimum_rr), (65, 80, 1.5))
        self.assertEqual(config.weights, (30, 20, 20, 15, 15))
        self.assertEqual((config.risk_pct, config.fee_pct, config.slip_pct), (0.5, 0.06, 0.03))
        self.assertEqual(DEFAULTS["均值回归"].style, "mean_reversion")
        self.assertFalse(any(item.errors() for item in DEFAULTS.values()))

    def test_saved_preset_does_not_replace_the_others(self):
        with TemporaryDirectory() as folder:
            conn = connect(Path(folder) / "research.sqlite3")
            changed = DEFAULTS["稳健"].from_dict({**DEFAULTS["稳健"].to_dict(), "threshold": 77})
            save_preset(conn, changed)
            loaded = load_presets(conn)
            self.assertEqual(loaded["稳健"].threshold, 77)
            self.assertEqual(loaded["均衡"].threshold, 65)
            self.assertEqual(loaded["稳健"].style, "trend")


class ScoreTests(unittest.TestCase):
    def _row(self):
        return pd.DataFrame([{
            "open": 100, "high": 101, "low": 99, "close": 100, "ema7": 100, "rsi": 50,
            "macd": 0, "macd_signal": 1, "rsi_5m": 40, "volume": 1, "vol_avg": 5, "atr": 10,
            "direction_4H": 1, "direction_1H": 1, "swing_low": 98, "swing_high": 102,
            "support": 97, "resistance": 103, "far_low": 96, "far_high": 104,
        }])

    def test_default_weights_stay_on_the_original_scale(self):
        scored = score_frames(self._row(), Rules())
        self.assertEqual(int(scored.score_long.iloc[0]), 30)
        self.assertEqual(int(scored.score_short.iloc[0]), 0)
        replaced = score_frames(self._row(), Rules(), weights=(50, 10, 10, 10, 20))
        self.assertEqual(int(replaced.score_long.iloc[0]), 50)
        with self.assertRaises(ValueError):
            ordered_weights((10, 10, 10, 10, 10))

    def test_mean_reversion_uses_only_closed_bar_and_next_open(self):
        frame = pd.DataFrame([
            {"time": "2026-01-01T00:00Z", "open": 91, "high": 92, "low": 89, "close": 90,
             "boll_low": 95, "boll_up": 110, "boll_mid": 100, "rsi": 20, "atr": 1},
            {"time": "2026-01-01T00:15Z", "open": 90.5, "high": 92, "low": 90, "close": 91,
             "boll_low": 80, "boll_up": 110, "boll_mid": 100, "rsi": 50, "atr": 1},
        ])
        rules = Rules(minimum_rr=1.2)
        costs = Costs()
        scored = score_mean_reversion(frame, rules, costs.fee, costs.slip, DEFAULTS["均值回归"])
        self.assertEqual(list(scored.side), [1, 0])
        _stats, curve, _trades = simulate(scored, costs, rules.minimum_rr)
        self.assertEqual(curve.units.iloc[0], 0)
        self.assertGreater(curve.units.iloc[1], 0)
        longer = pd.concat([frame, frame.iloc[[1]].assign(close=1)], ignore_index=True)
        again = score_mean_reversion(longer.iloc[:2], rules, costs.fee, costs.slip, DEFAULTS["均值回归"])
        self.assertEqual(list(again.side), [1, 0])


class HyperliquidTests(unittest.TestCase):
    def test_parser_keeps_public_grade_and_skips_flat(self):
        state = {"assetPositions": [
            {"position": {"coin": "ETH", "szi": "0", "entryPx": "1"}},
            {"position": {"coin": "BTC", "szi": "-0.2", "entryPx": "100", "positionValue": "20",
                          "unrealizedPnl": "-1", "leverage": {"value": 5}, "liquidationPx": "120"}},
        ]}
        rows = parse_positions(state, {"BTC": "101"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], "short")
        self.assertEqual(rows[0]["grade"], "B")
        self.assertEqual(rows[0]["mid"], 101)
        self.assertIsNone(rows[0]["open_time"])
        rate = win_rate_from_fills([{"closedPnl": "0"}, {"closedPnl": "2"}, {"closedPnl": "-1"}])
        self.assertEqual(rate["samples"], 2)
        self.assertEqual(rate["rate"], 0.5)
        self.assertIn("2000", rate["note"])

    def test_address_parser_and_position_diff(self):
        address = extract_address("see https://example.invalid/trader/0x" + "ab" * 20)
        self.assertEqual(address, "0x" + "ab" * 20)
        with self.assertRaises(Exception):
            extract_address("satoshi")
        with self.assertRaises(Exception):
            extract_address("0x" + "11" * 20 + " 0x" + "22" * 20)
        self.assertIn("不能判断", position_changes(None, [], 1)[0])
        alerts = position_changes(
            [{"symbol": "BTC", "side": "long", "unrealized_pnl": 1}],
            [{"symbol": "BTC", "side": "long", "unrealized_pnl": 80}],
            50,
        )
        self.assertTrue(any("未实现盈亏变化" in item for item in alerts))
        curve, drawdown = parse_portfolio([["day", {"accountValueHistory": [[1, "100"], [2, "80"], [3, "90"]]}]])
        self.assertEqual(len(curve), 3)
        self.assertAlmostEqual(drawdown["value"], -0.2)
        self.assertIn("不是交易所单独给出", drawdown["note"])


class OkxAndGithubTests(unittest.TestCase):
    def test_public_rank_has_no_drawdown_field(self):
        row = normalize_rank({"nickName": "n", "uniqueCode": "A" * 16, "winRatio": "0.5", "pnl": "1"})
        self.assertEqual(row["grade"], "B")
        self.assertIsNone(row["max_drawdown"])
        rate = win_rate([{"pnl": 3}, {"pnl": -1}])
        self.assertEqual(rate["rate"], 0.5)
        self.assertIn("不是账户全部成交", rate["note"])
        self.assertEqual(parse_data_ver("20231129213200"), int(datetime(2023, 11, 29, 21, 32, tzinfo=timezone.utc).timestamp() * 1000))
        self.assertIsNone(parse_data_ver("latest"))

    def test_license_score_does_not_execute_code(self):
        now = datetime(2026, 11, 25, tzinfo=timezone.utc)
        permissive = score_repo(normalize_repo({
            "full_name": "ccxt/ccxt", "html_url": "https://github.com/ccxt/ccxt", "description": "x",
            "license": {"spdx_id": "MIT"}, "pushed_at": "2026-11-20T00:00:00Z", "archived": False,
            "stargazers_count": 10, "language": "Python", "open_issues_count": 1,
        }), now)
        copyleft = score_repo({"license_spdx": "GPL-3.0", "pushed_at": "2026-11-20T00:00:00Z",
                                "archived": False, "stars": 10, "description": "x", "language": "Python"}, now)
        self.assertTrue(permissive["adopt"])
        self.assertFalse(copyleft["adopt"])
        self.assertIsNone(permissive["security_score"])
        self.assertIn("未克隆", permissive["security_note"])


class StoreAndHttpTests(unittest.TestCase):
    def test_database_search_favorite_and_retry(self):
        with TemporaryDirectory() as folder:
            conn = connect(Path(folder) / "research.sqlite3")
            with self.assertRaises(ValueError):
                add_article(conn, "t", "ftp://example.invalid", "", "")
            add_article(conn, "笔记", "https://example.invalid/a", "研究", "手工摘要")
            set_favorite(conn, "article", "article:1", "笔记", True)
            self.assertTrue(is_favorite(conn, "article", "article:1"))
            add_snapshot(conn, venue="hyperliquid", entity_key="hyperliquid:0x1", grade="B",
                         exchange_time_ms=10, received_time_ms=20, connection_status="正常", payload={"positions": []})
            self.assertEqual(recent_snapshots(conn, "hyperliquid:0x1")[0]["grade"], "B")
        calls = []

        def transport(method, url, **kwargs):
            calls.append(kwargs.get("headers"))
            if len(calls) < 3:
                return _response({"msg": "busy"}, 429)
            return _response({"ok": True})

        payload, status = request_json("GET", "https://example.invalid", transport=transport, sleeper=lambda _seconds: None)
        self.assertEqual(status, 200)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(len(calls), 3)

    def test_binance_451_is_region_blocked(self):
        from evidence import SourceError
        from platform_ui import _probe_binance

        def fail(*_args, **_kwargs):
            raise SourceError("HTTP 451 restricted", 451)

        with patch("platform_ui.request_json", fail):
            status, detail = _probe_binance()
        self.assertEqual(status, "区域不可用")
        self.assertIn("451", detail)

    def test_structure_parser_and_symbol_guard(self):
        def requester(url, params):
            if url.endswith("/ticker"):
                return {"lastPr": "1", "markPrice": "1.1", "indexPrice": "1.2", "fundingRate": "0.0001",
                        "holdingAmount": "9", "usdtVolume": "100", "baseVolume": "3", "ts": "1000"}
            if url.endswith("/history-fund-rate"):
                return [{"fundingRate": "0.0001", "fundingTime": "900"}]
            if url.endswith("/fills"):
                return [{"price": "1", "size": "2", "side": "buy", "tradeId": "1", "ts": "1000"}]
            if url.endswith("/merge-depth"):
                return {"bids": [["0.9", "1"]], "asks": [["1.1", "1"]], "ts": "1000"}
            raise AssertionError(url)

        payload = load_structure("btcusdt", requester)
        self.assertEqual(payload["ticker"]["open_interest"], 9)
        self.assertEqual(payload["book"]["bid"], 0.9)
        self.assertIn("不是交易员持仓", payload["market_note"])
        with self.assertRaises(ValueError):
            normalize_symbol("BTC-USDT")

    def test_session_round_trip(self):
        state = DEFAULTS["趋势跟踪"].session_values()
        restored = from_session("趋势跟踪", state)
        self.assertEqual(restored.weights, (40, 25, 15, 10, 10))
        self.assertEqual(restored.style, "trend")

    def test_status_matrix_names_blocked_work(self):
        states = {item["state"] for item in MODULES}
        self.assertTrue(states <= {"完成", "部分", "阻塞"})
        self.assertEqual(len(MODULES), 11)
        blocked = {item["id"] for item in MODULES if item["state"] == "阻塞"}
        self.assertIn("meme", blocked)
        self.assertIn("x_intel", blocked)
        self.assertIn("binance", {item["id"] for item in VENUES if item["state"] == "阻塞"})
        self.assertNotIn("完成", {item["state"] for item in VENUES if item["id"] in {"telegram", "x", "binance"}})


if __name__ == "__main__":
    unittest.main()
