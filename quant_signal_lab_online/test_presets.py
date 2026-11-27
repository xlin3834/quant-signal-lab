import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from presets import (
    BUILTINS, compare_presets, copy_custom, delete_custom, import_custom, list_custom,
    load_preset, preset_table, rename_custom, save_custom,
)
from strategy_modules import (
    DynamicStop, EmaBoll, IntradayScore, Pullback, ShortcutMA, default_modules,
    dynamic_levels, ema_boll_plan, environment, pullback_plan, shocks, sync_shortcut,
)
from test_strategy import sample
from indicators import calculate


class PresetTests(unittest.TestCase):
    def test_screenshot_defaults(self):
        table = {row["参数"]: row for row in preset_table()}
        self.assertEqual(table["节奏 EMA"]["敏捷型"], 7)
        self.assertEqual(table["节奏 EMA"]["均衡型"], 9)
        self.assertEqual(table["节奏 EMA"]["稳健型"], 12)
        self.assertEqual(table["节奏 EMA"]["震荡行情型"], 7)
        self.assertEqual(table["短线 EMA"]["敏捷型"], 15)
        self.assertEqual(table["短线 EMA"]["均衡型"], 20)
        self.assertEqual(table["短线 EMA"]["稳健型"], 26)
        self.assertEqual(table["短线 EMA"]["震荡行情型"], 20)
        self.assertEqual(table["MACD 快线"]["敏捷型"], 6)
        self.assertEqual(table["正式候选分数"]["均衡型"], 70)
        self.assertEqual(table["提前预警分数"]["震荡行情型"], 55)
        self.assertEqual(table["ATR 止损倍数"]["稳健型"], 2.0)
        self.assertEqual(table["最低净盈亏比"]["均衡型"], 1.8)
        self.assertEqual(table["BOLL 倍数"]["稳健型"], 2.2)

    def test_custom_roundtrip_does_not_overwrite_missing_file_on_load(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "user_presets.json"
            self.assertEqual(list_custom(path), {})
            self.assertFalse(path.exists())
            preset = BUILTINS["均衡型"]
            save_custom("研究A", preset, path)
            rename_custom("研究A", "研究B", path)
            copy_custom("研究B", "研究C", path)
            self.assertEqual(set(list_custom(path)), {"研究B", "研究C"})
            delete_custom("研究C", path)
            loaded = load_preset("研究B", path)
            self.assertEqual(loaded.ema, (9, 20, 50, 200))
            imported = import_custom(json.dumps({"profiles": {"导入": loaded.__dict__}}), path)
            self.assertIn("导入", imported)

    def test_compare_and_sync_shortcut(self):
        rows = compare_presets(BUILTINS["均衡型"], BUILTINS["敏捷型"])
        names = {row["参数"] for row in rows}
        self.assertIn("ema", names)
        synced = sync_shortcut(ShortcutMA(7, 21, 14), BUILTINS["均衡型"].parameters())
        self.assertEqual(synced.ema[0], 7)
        self.assertEqual(synced.rsi, 14)


class ModuleEngineTests(unittest.TestCase):
    def test_modules_produce_numeric_plans(self):
        frames = {"15m": sample(count=600), "5m": sample("5min", 1800), "1H": sample("1h", 600), "4H": sample("4h", 600)}
        params = BUILTINS["均衡型"].parameters()
        data = calculate(frames["15m"], params)
        env = environment(data)
        self.assertIn(env["regime"], {"上涨趋势", "下跌趋势", "横盘震荡", "高波动行情", "低波动行情", "趋势转换阶段"})
        self.assertFalse(env["auto_apply"])
        flags = shocks({key: calculate(value, params) for key, value in frames.items()}, IntradayScore())
        self.assertIn("15m", flags)
        plan = ema_boll_plan(frames["15m"], EmaBoll(enabled=True, inherit=False), params)
        self.assertTrue(plan["ready"])
        self.assertIn("stop", plan)
        pb = pullback_plan(frames, Pullback(enabled=True), params)
        self.assertIn("entries", pb)
        last = data.dropna(subset=["boll_mid", "atr", "ema20"]).iloc[-1]
        levels = dynamic_levels(last, DynamicStop(scheme="布林动态"), 1, float(last.close),
                                float(last.close) * 0.99, float(last.close) * 1.01, float(last.close) * 1.02,
                                float(last.atr))
        self.assertEqual(levels["scheme"], "布林动态")
        self.assertLess(levels["stop"], last.close)

    def test_default_modules_exist(self):
        modules = default_modules()
        self.assertEqual(len(modules), 5)
        self.assertEqual(modules[0].short, 9)
        self.assertEqual(modules[0].long, 21)
        self.assertEqual(modules[1].threshold, 70)
        self.assertEqual(modules[2].ema, 10)
        self.assertEqual(modules[2].capital, 80)
        self.assertEqual(modules[3].rsi_long, 40)
        self.assertEqual(modules[4].minimum_rr, 1.5)
