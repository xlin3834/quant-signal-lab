import unittest

import numpy as np
import pandas as pd

from macro_fib import (
    EXTENSIONS, FibParams, MacroParams, analyze_macro, closed_macro, confirmed_swings,
    fibonacci_grid, quarterly_from_monthly, yearly_from_monthly,
)


def candles(freq, count, end="2026-10-01 00:00Z", low=None, high=None):
    time = pd.date_range(end=end, periods=count, freq=freq, tz="UTC")
    index = np.arange(count)
    close = 100 + np.sin(index / 8) * 8 + index * 0.02
    if high is not None:
        close[high] = close.max() + 20
    if low is not None:
        close[low] = close.min() - 20
    return pd.DataFrame({
        "time": time, "open": close - 0.2, "close": close,
        "high": close + 0.5, "low": close - 0.5, "volume": 10.0,
    })


class MacroFibTests(unittest.TestCase):
    def test_closed_macro_drops_forming_bars(self):
        daily = candles("1D", 40, end="2026-11-26 00:00Z")
        now = pd.Timestamp("2026-11-26 08:00Z")
        closed = closed_macro(daily, "1D", now)
        self.assertEqual(closed.time.iloc[-1], pd.Timestamp("2026-11-25 00:00Z"))
        weekly = candles("7D", 20, end="2026-11-23 00:00Z")
        closed_w = closed_macro(weekly, "1W", pd.Timestamp("2026-11-26 00:00Z"))
        # 2026-11-23 开盘的周线在 11-26 仍未收盘，应被剔除。
        self.assertEqual(closed_w.time.iloc[-1], pd.Timestamp("2026-11-16 00:00Z"))
        weekly2 = candles("7D", 8, end="2026-11-24 00:00Z")
        closed_w2 = closed_macro(weekly2, "1W", pd.Timestamp("2026-11-26 00:00Z"))
        self.assertLessEqual(closed_w2.time.iloc[-1], pd.Timestamp("2026-11-17 00:00Z"))
        monthly = candles("MS", 18, end="2026-11-01 00:00Z")
        closed_m = closed_macro(monthly, "1M", pd.Timestamp("2026-11-26 00:00Z"))
        self.assertEqual((closed_m.time.iloc[-1].year, closed_m.time.iloc[-1].month), (2026, 10))

    def test_quarterly_and_yearly_drop_current_period(self):
        monthly = candles("MS", 36, end="2026-11-01 00:00Z")
        closed = closed_macro(monthly, "1M", pd.Timestamp("2026-11-26 00:00Z"))
        quarterly = quarterly_from_monthly(closed, pd.Timestamp("2026-11-26 00:00Z"))
        self.assertLessEqual(quarterly.time.iloc[-1], pd.Timestamp("2026-09-30", tz="UTC") + pd.Timedelta(days=1))
        yearly = yearly_from_monthly(closed, pd.Timestamp("2026-11-26 00:00Z"))
        self.assertEqual(yearly.time.iloc[-1].year, 2025)

    def test_swings_are_confirmed_and_do_not_repaint(self):
        data = candles("1D", 80, low=20, high=50)
        swings = confirmed_swings(data, left=5)
        last = len(data) - 1
        self.assertTrue((swings["index"] + 5 <= last).all())
        prefix = data.iloc[:60].reset_index(drop=True)
        early = confirmed_swings(prefix, left=5)
        later = confirmed_swings(data, left=5)
        later = later[later.confirmed_time <= prefix.time.iloc[-1]]
        merged = early.merge(later, on=["index", "kind", "price"])
        self.assertEqual(len(merged), len(early))

    def test_fibonacci_prefix_is_stable_after_confirmation(self):
        data = candles("1D", 120, low=30, high=80)
        params = FibParams(swing=5, lookback=120)
        at_100 = fibonacci_grid(data.iloc[:100].reset_index(drop=True), params)
        self.assertTrue(at_100["ready"])
        later = fibonacci_grid(data, params)
        # The earlier confirmed leg levels must still appear if that same end swing remains the active leg,
        # or at least the historically confirmed retracements stay identical when recomputed on the prefix.
        again = fibonacci_grid(data.iloc[:100].reset_index(drop=True), params)
        self.assertEqual(at_100["end_time"], again["end_time"])
        self.assertEqual(at_100["start_price"], again["start_price"])
        self.assertEqual(at_100["end_price"], again["end_price"])
        for ratio in at_100["retracements"]:
            self.assertAlmostEqual(at_100["retracements"][ratio], again["retracements"][ratio])
        self.assertTrue(set(EXTENSIONS).issubset(at_100["extensions"]))
        # Future bars cannot rewrite an already confirmed historical grid on the same cutoff.
        cutoff = at_100["confirmed_time"]
        after = later["swings"]
        historical = after[after.confirmed_time <= cutoff]
        earlier = at_100["swings"]
        self.assertEqual(len(historical.merge(earlier, on=["index", "kind", "price"])), len(earlier))

    def test_analyze_macro_suggests_but_never_auto_applies(self):
        frames = {
            "1D": candles("1D", 80),
            "1W": candles("7D", 80),
            "1M": closed_macro(candles("MS", 24, end="2026-10-01 00:00Z"), "1M",
                               pd.Timestamp("2026-11-26 00:00Z")),
        }
        frames["1Q"] = quarterly_from_monthly(frames["1M"], pd.Timestamp("2026-11-26 00:00Z"))
        frames["1Y"] = yearly_from_monthly(frames["1M"], pd.Timestamp("2026-11-26 00:00Z"))
        report = analyze_macro(frames, MacroParams())
        self.assertFalse(report["auto_apply"])
        self.assertIn("suggestion", report)
        self.assertIn(report["daily"]["stage"], {"上升阶段", "下降阶段", "整理阶段", "数据不足"})
        self.assertIn("phase", report["monthly"])
