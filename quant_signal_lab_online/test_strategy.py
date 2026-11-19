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
