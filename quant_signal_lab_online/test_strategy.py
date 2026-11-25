import unittest

import numpy as np
import pandas as pd

from charts import market_figure
from indicators import Parameters, calculate
from signals import aligned_frames, score_frames


def sample(freq="15min", count=360, end="2026-11-19 12:00Z"):
    time = pd.date_range(end=end, periods=count, freq=freq)
    close = 100 + np.arange(count) * .03 + np.sin(np.arange(count) / 6)
    return pd.DataFrame({"time": time, "open": close - .1, "close": close,
                         "high": close + .2, "low": close - .3, "volume": 100.})


class StrategyTests(unittest.TestCase):
    def test_indicator_prefix_and_chart(self):
        d = sample(count=600)
        full = calculate(d)
        pd.testing.assert_frame_equal(full.iloc[:260], calculate(d.iloc[:260]))
        self.assertEqual(len(market_figure(full, Parameters()).data), 16)
        self.assertTrue(full.ema200.notna().iloc[-1])
        pd.testing.assert_frame_equal(full.iloc[-50:], calculate(d.iloc[100:]).iloc[-50:],
                                      check_exact=False, atol=1e-12)

    def test_signal_and_high_timeframe_prefix(self):
        frames = {"15m": sample(), "5m": sample("5min", 1200),
                  "1H": sample("1h"), "4H": sample("4h")}
        full = score_frames(aligned_frames(frames))
        cutoff = full.decision_time.iloc[-5]
        clipped = {i: d[d.time + pd.Timedelta({"15m": "15min", "5m": "5min",
                                               "1H": "1h", "4H": "4h"}[i]) <= cutoff]
                   for i, d in frames.items()}
        pd.testing.assert_frame_equal(full[full.decision_time <= cutoff],
                                      score_frames(aligned_frames(clipped)))
        self.assertTrue((full.closed_4H <= full.decision_time).all())
        self.assertTrue(full.score_long.between(0, 100).all())

    def test_forming_bar_does_not_change_closed_signal(self):
        from market_data import active_forming, capture_forming, closed_candles

        frames = {"15m": sample(), "5m": sample("5min", 1200),
                  "1H": sample("1h"), "4H": sample("4h")}
        last = frames["15m"].time.iloc[-1]
        now = last + pd.Timedelta(minutes=16)
        extra = frames["15m"].iloc[[-1]].copy()
        extra.loc[:, "time"] = last + pd.Timedelta(minutes=15)
        extra.loc[:, ["open", "high", "low", "close"]] = [140., 150., 130., 145.]
        raw = pd.concat([frames["15m"], extra], ignore_index=True)
        closed = closed_candles(raw, "15m", now, min_bars=50)
        forming = capture_forming(raw, "15m", now, closed)
        self.assertEqual(forming["close"], 145.)
        self.assertIsNotNone(active_forming(forming, "15m", now, closed.time.iloc[-1]))
        pd.testing.assert_frame_equal(closed.reset_index(drop=True), frames["15m"].reset_index(drop=True))
        pd.testing.assert_frame_equal(score_frames(aligned_frames(frames)),
                                      score_frames(aligned_frames({**frames, "15m": closed})))

    def test_chart_keeps_closed_candles_until_a_forming_bar_is_supplied(self):
        from charts import market_figure

        full = calculate(sample(count=600))
        plain = market_figure(full, Parameters())
        self.assertEqual(len(plain.data), 16)
        self.assertNotIn("未收盘", [trace.name for trace in plain.data])
        forming = {"time": full.time.iloc[-1] + pd.Timedelta(minutes=15),
                   "open": 101., "high": 102., "low": 100., "close": 101.5, "volume": 4.}
        shown = market_figure(full, Parameters(), forming=forming)
        self.assertEqual([trace.name for trace in shown.data].count("未收盘"), 1)
        hidden = market_figure(full, Parameters(), show=frozenset({"ema", "volume", "macd", "rsi"}))
        quiet = [trace for trace in hidden.data if str(trace.name).startswith("BOLL") or trace.name in ("压力", "支撑")]
        self.assertTrue(quiet)
        self.assertTrue(all(trace.visible == "legendonly" for trace in quiet))
        averages = [trace for trace in hidden.data if str(trace.name).startswith("EMA")]
        self.assertTrue(all(trace.visible is True for trace in averages))

    def test_missing_market_fields_stay_readable(self):
        from ui import direction_text, optional_number

        self.assertEqual(optional_number(float("nan"), ".1f"), "缺失")
        self.assertEqual(optional_number(None, ".1f"), "缺失")
        self.assertEqual(optional_number(12.34, ".1f"), "12.3")
        self.assertEqual(direction_text(float("nan")), "缺失")
        self.assertEqual(direction_text(1), "多")


class FillAndWorkerTests(unittest.TestCase):
    def test_fill_stop_first_with_gap_and_costs(self):
        from types import SimpleNamespace
        from backtest import Costs, new_position, process_bar

        costs = Costs()
        plan = {"side": 1, "stop": 98, "target1": 104, "target2": 108}
        position = new_position(plan, 100, costs.capital, costs, 1.5)
        bar = SimpleNamespace(open=97, low=96, high=110, time="test")
        fills, leftover = process_bar(position, bar, costs)
        self.assertIsNone(leftover)
        self.assertEqual([fill["reason"] for fill in fills], ["止损"])
        self.assertLess(fills[0]["exit"], 97)
        self.assertLess(fills[0]["pnl"], 0)

    def test_out_of_sample_cannot_select_threshold(self):
        from backtest import Costs, evaluate
        from signals import Rules

        frames = {"15m": sample(), "5m": sample("5min", 1200),
                  "1H": sample("1h"), "4H": sample("4h")}
        data = aligned_frames(frames)
        alternate = data.copy()
        alternate.loc[alternate.index[int(len(data) * .7):], "close"] *= 2
        first = evaluate(data, Costs(), Rules(), True)
        second = evaluate(alternate, Costs(), Rules(), True)
        self.assertEqual(first[0], second[0])
        pd.testing.assert_frame_equal(first[1][1], second[1][1])
        self.assertEqual(first[2][1].time.iloc[0], data.time.iloc[int(len(data) * .7)])

    def test_worker_restart_dedup_and_controls(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from paper import advance, control, snapshot

        data = sample(count=4)
        data[["open", "close", "high", "low"]] = [100., 100., 101., 99.]
        data["side"], data["stop_long"], data["target1_long"], data["target2_long"] = 1, 98., 104., 108.
        with TemporaryDirectory() as folder:
            db = Path(folder) / "paper.db"
            initial = advance(db, "BTCUSDT", data.iloc[:1], data.time.iloc[1])
            self.assertIsNotNone(initial["pending"])
            self.assertIsNone(initial["position"])
            advance(db, "BTCUSDT", data.iloc[:2], data.time.iloc[2])
            opened = advance(db, "BTCUSDT", data.iloc[:3], data.time.iloc[3])
            self.assertIsNotNone(opened["position"])
            control(db, "stop")
            data.loc[3, ["low", "high"]] = [96., 110.]
            now = data.time.iloc[3] + pd.Timedelta(minutes=15)
            stopped = advance(db, "BTCUSDT", data, now)
            advance(db, "BTCUSDT", data, now)
            account, fills, equity = snapshot(db)
            self.assertEqual(stopped["mode"], "stop")
            self.assertEqual(account["losses"], 1)
            self.assertIsNone(account["pending"])
            self.assertIsNone(account["position"])
            self.assertEqual(len(fills), 1)
            self.assertEqual(len(equity), 4)

    def test_worker_fuse_persists_without_opening(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from paper import advance, control, read_account, save_account, transaction

        data = sample(count=4)
        data["side"], data["stop_long"], data["target1_long"], data["target2_long"] = 1, 98., 104., 108.
        with TemporaryDirectory() as folder:
            db = Path(folder) / "paper.db"
            with transaction(db) as conn:
                state = read_account(conn)
                save_account(conn, {**state, "losses": 3})
            state = advance(db, "BTCUSDT", data.iloc[:1], data.time.iloc[1])
            self.assertIsNone(state["pending"])
            with self.assertRaises(ValueError):
                control(db, "reset-streak")
            control(db, "pause")
            control(db, "reset-streak")
            control(db, "resume")
            state = advance(db, "BTCUSDT", data.iloc[:2], data.time.iloc[2])
            self.assertIsNotNone(state["pending"])

    def test_two_targets_close_short_in_halves(self):
        from types import SimpleNamespace
        from backtest import Costs, new_position, process_bar

        costs = Costs()
        plan = {"side": -1, "stop": 102, "target1": 95, "target2": 91}
        position = new_position(plan, 100, costs.capital, costs, 1.5)
        first = SimpleNamespace(open=99, low=93, high=101, time="first")
        fills, remainder = process_bar(position, first, costs)
        self.assertEqual(fills[0]["units"], position["units"] / 2)
        self.assertEqual(remainder["units"], fills[0]["units"])
        second = SimpleNamespace(open=94, low=90, high=96, time="second")
        fills, remainder = process_bar(remainder, second, costs)
        self.assertEqual(fills[0]["reason"], "止盈二")
        self.assertGreater(fills[0]["pnl"], 0)
        self.assertIsNone(remainder)

    def test_worker_daily_fuse_resets_on_new_utc_day(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from paper import advance, read_account, save_account, transaction

        data = sample(count=2, end="2026-11-20 00:00Z")
        data["side"], data["stop_long"], data["target1_long"], data["target2_long"] = 1, 98., 104., 108.
        with TemporaryDirectory() as folder:
            db = Path(folder) / "paper.db"
            with transaction(db) as conn:
                state = read_account(conn)
                save_account(conn, {**state, "day": "2026-11-19", "cash": 980., "equity": 980.})
            state = advance(db, "BTCUSDT", data.iloc[:1], data.time.iloc[1])
            self.assertTrue(state["day_halt"])
            self.assertIsNone(state["pending"])
            state = advance(db, "BTCUSDT", data, data.time.iloc[1] + pd.Timedelta(minutes=15))
            self.assertFalse(state["day_halt"])
            self.assertIsNotNone(state["pending"])

    def test_failed_poll_keeps_pending_plan_and_uses_wal(self):
        from contextlib import closing
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from market_data import MarketDataError
        from paper import advance, connect, record_failure

        data = sample(count=4)
        data["side"], data["stop_long"], data["target1_long"], data["target2_long"] = 1, 98., 104., 108.
        with TemporaryDirectory() as folder:
            db = Path(folder) / "paper.db"
            state = advance(db, "BTCUSDT", data.iloc[:1], data.time.iloc[1])
            self.assertIsNotNone(state["pending"])
            broken = data.drop(index=data.index[1])
            with self.assertRaisesRegex(MarketDataError, "缺失"):
                advance(db, "BTCUSDT", broken, broken.time.iloc[-1] + pd.Timedelta(minutes=15))
            failed = record_failure(db, MarketDataError("模拟交易历史缺失，保留仓位并等待补齐"))
            self.assertEqual(failed["pending"], state["pending"])
            self.assertEqual(failed["last_bar"], state["last_bar"])
            self.assertIsNone(failed["position"])
            self.assertIn("缺失", failed["error"])
            with closing(connect(db)) as conn:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_stop_without_a_gap_uses_the_risk_budget(self):
        from types import SimpleNamespace
        from backtest import Costs, new_position, process_bar

        for costs in (Costs(fee=0, slip=0), Costs()):
            plan = {"side": 1, "stop": 98, "target1": 104, "target2": 108}
            position = new_position(plan, 100, costs.capital, costs, 1.5)
            bar = SimpleNamespace(open=100, low=98, high=100.5, time="stop")
            fills, leftover = process_bar(position, bar, costs)
            self.assertIsNone(leftover)
            self.assertAlmostEqual(fills[0]["pnl"], -costs.capital * costs.risk, places=6)

    def test_research_stats_follow_closed_positions(self):
        from backtest import Costs, simulate

        start = pd.Timestamp("2026-11-20T00:00:00Z")

        def scored(rows):
            records = []
            for index, changes in enumerate(rows):
                row = {"time": start + pd.Timedelta(minutes=15 * index), "open": 100., "high": 101.,
                       "low": 99., "close": 100., "side": 0, "stop_long": 98., "target1_long": 104.,
                       "target2_long": 120., "stop_short": 102., "target1_short": 96., "target2_short": 94.}
                row.update(changes)
                records.append(row)
            return pd.DataFrame(records)

        costs = Costs(fee=0, slip=0)
        stopped, _curve, _trades = simulate(scored([
            {"side": 1},
            {"low": 97},
        ]), costs, 1.5)
        self.assertEqual(stopped["交易次数"], 1)
        self.assertEqual(stopped["盈利因子"], 0)
        self.assertAlmostEqual(stopped["净收益 USDT"], -5)
        self.assertAlmostEqual(stopped["平均每笔盈亏 USDT"], -5)
        self.assertEqual(stopped["最大连续亏损次数"], 1)
        self.assertEqual(stopped["收盘持仓占比 %"], 0)

        mixed, _curve, trades = simulate(scored([
            {"side": 1},
            {"high": 105},
            {"low": 97},
        ]), costs, 1.5)
        self.assertEqual(len(trades), 2)
        self.assertEqual(mixed["交易次数"], 1)
        self.assertIsNone(mixed["盈利因子"])
        self.assertAlmostEqual(mixed["净收益 USDT"], 2.5)
        self.assertEqual(mixed["最大连续亏损次数"], 0)
        self.assertAlmostEqual(mixed["收盘持仓占比 %"], 100 / 3)
