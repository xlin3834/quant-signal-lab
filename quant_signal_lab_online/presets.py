"""Named strategy presets and user-saved custom profiles.

Default values are initial test settings from the user's specification and screenshot.
They are not claimed to be optimal. Existing saved profiles are never overwritten on load.
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from indicators import Parameters
from signals import Rules

PRESET_PATH = Path(__file__).resolve().parent / "data" / "user_presets.json"
PRESET_NAMES = ("敏捷型", "均衡型", "稳健型", "震荡行情型", "自定义", "超短线", "激进", "平衡", "稳健初始", "保守")
RESEARCH_WEIGHTS = (20, 20, 15, 15, 10)
DEFAULT_NAME = "均衡型"


@dataclass(frozen=True)
class Preset:
    name: str
    ema: tuple = (9, 20, 50, 200)
    macd: tuple = (12, 26, 9)
    rsi: int = 14
    atr: int = 14
    boll: int = 20
    boll_mult: float = 2.0
    volume: int = 20
    rvol: float = 1.2
    early: int = 60
    threshold: int = 70
    strong: int = 80
    atr_stop: float = 1.5
    minimum_rr: float = 1.8
    structure: int = 20
    weights: tuple = (30, 20, 20, 15, 15)
    note: str = ""

    def parameters(self):
        return Parameters(self.ema, self.boll, self.rsi, self.macd, self.atr, self.volume, self.structure, self.boll_mult)

    def rules(self):
        return Rules(self.threshold, self.strong, self.minimum_rr, self.early, self.rvol, self.atr_stop, self.weights)


BUILTINS = {
    "敏捷型": Preset(
        "敏捷型", (7, 15, 30, 200), (6, 13, 5), 7, 10, 15, 2.0, 15, 1.1, 50, 60, 75, 1.2, 1.5,
        note="强调短周期动能与早期变化。允许盘中临时预警，正式候选默认收盘确认。",
    ),
    "均衡型": Preset(
        "均衡型", (9, 20, 50, 200), (12, 26, 9), 14, 14, 20, 2.0, 20, 1.2, 60, 70, 80, 1.5, 1.8,
        note="默认基准测试模式。兼顾趋势、动能、成交量、结构与触发条件。参数仅为初始测试值，须经历史数据验证。",
    ),
    "稳健型": Preset(
        "稳健型", (12, 26, 60, 200), (12, 30, 9), 21, 21, 25, 2.2, 30, 1.5, 70, 80, 90, 2.0, 2.0,
        note="强调收盘确认、趋势结构、量能与突破回踩，降低频繁交叉噪声。",
    ),
    "震荡行情型": Preset(
        "震荡行情型", (7, 20, 50, 200), (12, 26, 9), 14, 14, 20, 2.0, 20, 1.2, 55, 65, 80, 1.5, 1.5,
        note="增加带宽、ADX、EMA 斜率与均值回归判断。单边趋势形成后提示震荡策略可能失效，不得机械逆势出正式候选。",
    ),
    "超短线": Preset(
        "超短线", (9, 20, 50, 200), (12, 26, 9), 14, 14, 20, 2.0, 20, 1.2, 45, 55, 70, 1.5, 1.8,
        weights=RESEARCH_WEIGHTS,
        note="研究档位，正式候选 55。不替换敏捷型。五项权重用八因子里已接通的前五项 20/20/15/15/10；后三项未计算，不计入分数。不是已验证最优。",
    ),
    "激进": Preset(
        "激进", (9, 20, 50, 200), (12, 26, 9), 14, 14, 20, 2.0, 20, 1.2, 50, 60, 75, 1.5, 1.8,
        weights=RESEARCH_WEIGHTS,
        note="研究档位，正式候选 60。不替换已有预设。后三项八因子未计入分数。不是已验证最优。",
    ),
    "平衡": Preset(
        "平衡", (9, 20, 50, 200), (12, 26, 9), 14, 14, 20, 2.0, 20, 1.2, 55, 65, 80, 1.5, 1.8,
        weights=RESEARCH_WEIGHTS,
        note="研究档位，正式候选 65。不替换均衡型。不是已验证最优。",
    ),
    "稳健初始": Preset(
        "稳健初始", (9, 20, 50, 200), (12, 26, 9), 14, 14, 20, 2.0, 20, 1.2, 65, 75, 85, 1.5, 1.8,
        weights=RESEARCH_WEIGHTS,
        note="研究清单里的 75 分档。原「稳健型」仍是 80 分，没有被替换。不是已验证最优。",
    ),
    "保守": Preset(
        "保守", (9, 20, 50, 200), (12, 26, 9), 14, 14, 20, 2.0, 20, 1.2, 75, 85, 90, 1.5, 1.8,
        weights=RESEARCH_WEIGHTS,
        note="研究档位，正式候选 85。不替换稳健型或自定义。不是已验证最优。",
    ),
}


def builtin(name=DEFAULT_NAME):
    return BUILTINS[name]


def preset_table():
    keys = [
        ("节奏 EMA", lambda p: p.ema[0]),
        ("短线 EMA", lambda p: p.ema[1]),
        ("中线 EMA", lambda p: p.ema[2]),
        ("背景 EMA", lambda p: p.ema[3]),
        ("MACD 快线", lambda p: p.macd[0]),
        ("MACD 慢线", lambda p: p.macd[1]),
        ("MACD 信号线", lambda p: p.macd[2]),
        ("RSI 周期", lambda p: p.rsi),
        ("ATR 周期", lambda p: p.atr),
        ("BOLL 周期", lambda p: p.boll),
        ("BOLL 倍数", lambda p: p.boll_mult),
        ("成交量均值周期", lambda p: p.volume),
        ("交易确认 RVOL", lambda p: p.rvol),
        ("提前预警分数", lambda p: p.early),
        ("正式候选分数", lambda p: p.threshold),
        ("强候选分数", lambda p: p.strong),
        ("ATR 止损倍数", lambda p: p.atr_stop),
        ("最低净盈亏比", lambda p: p.minimum_rr),
    ]
    table = []
    for label, getter in keys:
        row = {"参数": label}
        for name in ("敏捷型", "均衡型", "稳健型", "震荡行情型", "超短线", "激进", "平衡", "稳健初始", "保守"):
            row[name] = getter(BUILTINS[name])
        table.append(row)
    return table


def compare_presets(current: Preset, incoming: Preset):
    rows = []
    for item in fields(Preset):
        if item.name in {"name", "note"}:
            continue
        old, new = getattr(current, item.name), getattr(incoming, item.name)
        if old != new:
            rows.append({"参数": item.name, "当前": old, "应用后": new})
    return rows


def _load(path=PRESET_PATH):
    if not path.is_file():
        return {"version": 1, "profiles": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("自定义配置文件格式无效")
    payload.setdefault("version", 1)
    payload.setdefault("profiles", {})
    return payload


def list_custom(path=PRESET_PATH):
    return dict(_load(path)["profiles"])


def _write(payload, path=PRESET_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_custom(name, preset: Preset, path=PRESET_PATH):
    if not name or name in BUILTINS:
        raise ValueError("自定义配置名称不能为空，也不能与内置预设重名")
    payload = _load(path)
    data = asdict(preset)
    data["name"] = name
    data["ema"] = list(data["ema"])
    data["macd"] = list(data["macd"])
    data["weights"] = list(data["weights"])
    payload["profiles"][name] = data
    _write(payload, path)
    return name


def rename_custom(old, new, path=PRESET_PATH):
    payload = _load(path)
    if old not in payload["profiles"]:
        raise KeyError(old)
    if new in payload["profiles"] or new in BUILTINS:
        raise ValueError("新名称已存在")
    payload["profiles"][new] = {**payload["profiles"].pop(old), "name": new}
    _write(payload, path)


def copy_custom(src, dest, path=PRESET_PATH):
    payload = _load(path)
    if src not in payload["profiles"]:
        raise KeyError(src)
    if dest in payload["profiles"] or dest in BUILTINS:
        raise ValueError("目标名称已存在")
    payload["profiles"][dest] = {**deepcopy(payload["profiles"][src]), "name": dest}
    _write(payload, path)


def delete_custom(name, path=PRESET_PATH):
    payload = _load(path)
    payload["profiles"].pop(name)
    _write(payload, path)


def export_custom(path=PRESET_PATH):
    return json.dumps(_load(path), ensure_ascii=False, indent=2)


def import_custom(text, path=PRESET_PATH, replace=False):
    incoming = json.loads(text)
    profiles = incoming.get("profiles", incoming)
    if not isinstance(profiles, dict):
        raise ValueError("导入内容缺少 profiles")
    payload = {"version": 1, "profiles": {}} if replace else _load(path)
    for name, raw in profiles.items():
        if name in BUILTINS:
            continue
        payload["profiles"][name] = raw
    _write(payload, path)
    return list(payload["profiles"])


def load_preset(name, path=PRESET_PATH):
    if name in BUILTINS:
        return BUILTINS[name]
    raw = list_custom(path).get(name)
    if raw is None:
        raise KeyError(name)
    raw = dict(raw)
    raw["ema"] = tuple(raw["ema"])
    raw["macd"] = tuple(raw["macd"])
    raw["weights"] = tuple(raw.get("weights", (30, 20, 20, 15, 15)))
    allowed = {item.name for item in fields(Preset)}
    return Preset(**{key: value for key, value in raw.items() if key in allowed})


def resolved_custom(path=PRESET_PATH, name="自定义"):
    """Use a saved custom profile when present; otherwise inherit 均衡型 defaults.

    Loading this helper never writes user_presets.json.
    """
    customs = list_custom(path)
    if name in customs:
        return load_preset(name, path), "saved"
    defaults = builtin(DEFAULT_NAME)
    return Preset(
        name, defaults.ema, defaults.macd, defaults.rsi, defaults.atr, defaults.boll,
        defaults.boll_mult, defaults.volume, defaults.rvol, defaults.early, defaults.threshold,
        defaults.strong, defaults.atr_stop, defaults.minimum_rr, defaults.structure, defaults.weights,
        note="无已保存自定义配置，沿用均衡型默认测试值，并非另一套已优化参数。",
    ), "default_balanced"
