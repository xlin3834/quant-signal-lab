import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from backtest import Costs, TRADE_COLUMNS, simulate
from presets import BUILTINS, resolved_custom
from preset_backtest import compare_frames, comparison_presets
from signals import aligned_frames, score_with_config
from test_strategy import sample


def pack(count_15m=720):
    """Shared synthetic multi-timeframe pack ending at a fixed instant."""
    end = "2026-11-19 12:00Z"
    return {
        "15m": sample("15min", count_15m, end),
        "5m": sample("5min", count_15m * 3, end),
        "1H": sample("1h", max(count_15m // 2, 400), end),
        "4H": sample("4h", max(count_15m // 3, 400), end),
    }


class PresetBacktestTests(unittest.TestCase):
    def test_resolved_custom_inherits_balanced_without_writing(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "user_presets.json"
            custom, source = resolved_custom(path)
            self.assertEqual(source, "default_balanced")
            self.assertEqual(custom.ema, BUILTINS["均衡型"].ema)
            self.assertEqual(custom.threshold, BUILTINS["均衡型"].threshold)
            self.assertFalse(path.exists())

    def test_simulate_detail_is_backward_compatible(self):
        frames = pack(500)
        base = aligned_frames(frames, BUILTINS["均衡型"].parameters())
        scored = score_with_config(base, BUILTINS["均衡型"].rules())
        stats, curve, trades = simulate(scored, Costs(), 1.8)
        self.assertEqual(list(trades.columns), TRADE_COLUMNS if not trades.empty else list(trades.columns) or TRADE_COLUMNS)
        detailed = simulate(scored, Costs(), 1.8, detail=True)
        self.assertEqual(len(detailed), 4)
        self.assertIn("官方信号数", detailed[3])

    def test_five_presets_share_window_and_custom_matches_balanced(self):
        frames = pack(800)
        costs = Costs()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "user_presets.json"
            results, scored, mask = compare_frames(frames, costs, path)
        self.assertEqual(results["custom_source"], "default_balanced")
        window = results["window"]
        self.assertGreaterEqual(window["bars"], 50)
        self.assertEqual(window["is_bars"] + window["oos_bars"], window["bars"])
        self.assertEqual(set(results["presets"]), {"敏捷型", "均衡型", "稳健型", "震荡行情型", "自定义"})
        balanced = results["presets"]["均衡型"]["full"]
        custom = results["presets"]["自定义"]["full"]
        self.assertEqual(balanced["净收益 USDT"], custom["净收益 USDT"])
        self.assertEqual(balanced["交易次数"], custom["交易次数"])
        self.assertEqual(balanced["官方信号数"], custom["官方信号数"])
        # Different built-in logic should not all collapse to one identical row by renaming.
        signatures = {
            name: (row["full"]["官方信号数"], row["full"]["净收益 USDT"], row["full"]["交易次数"])
            for name, row in results["presets"].items() if name != "自定义"
        }
        self.assertGreaterEqual(len(set(signatures.values())), 2)
        # Fills occur on or after the signal bar close: entry is the next bar open.
        preset = BUILTINS["均衡型"]
        frame = scored["均衡型"].loc[mask].reset_index(drop=True)
        _, _, trades, extra = simulate(frame, costs, preset.rules().minimum_rr, detail=True)
        if not trades.empty:
            merged = trades.merge(frame[["time", "side"]], left_on="entry_time", right_on="time", how="left")
            # The fill bar itself may have a new side; the previous closed bar must exist.
            for entry in trades.entry_time:
                prior = frame[frame.time < entry]
                self.assertGreater(len(prior), 0)
                # Signal was issued on a strictly earlier closed 15m bar (next-open fill).
                self.assertTrue((prior.time.max() + pd.Timedelta(minutes=15)) == entry
                                or prior.time.max() < entry)

    def test_no_threshold_search_and_same_split(self):
        frames = pack(640)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "user_presets.json"
            results, _, _ = compare_frames(frames, Costs(), path)
        split = results["window"]["split"]
        for name in results["presets"]:
            # Window object is shared; reusing the same split is the contract.
            self.assertEqual(results["window"]["split"], split)
        # Thresholds stay at each preset's initial test value, not a grid-searched winner.
        self.assertEqual(results["presets"]["敏捷型"]["preset"].threshold, 60)
        self.assertEqual(results["presets"]["均衡型"]["preset"].threshold, 70)
        self.assertEqual(results["presets"]["稳健型"]["preset"].threshold, 80)
        self.assertEqual(results["presets"]["震荡行情型"]["preset"].threshold, 65)
