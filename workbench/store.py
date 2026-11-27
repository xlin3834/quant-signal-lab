"""Site settings and named presets.

Applying a preset updates the shared working copy used by charts, scans,
scores, backtests and in-page alerts. It does not write user_presets.json.
Saved custom profiles are only touched by an explicit save, rename, copy,
delete or import.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from workbench.engine_path import PACKAGE  # noqa: F401
from presets import (
    BUILTINS,
    DEFAULT_NAME,
    PRESET_NAMES,
    PRESET_PATH,
    Preset,
    list_custom,
    load_preset,
    preset_table,
    resolved_custom,
    save_custom,
    delete_custom,
    rename_custom,
    copy_custom,
    export_custom,
    import_custom,
)

SETTINGS_PATH = PACKAGE.parent / "workbench" / "data" / "site_settings.json"
SECRET_MARKERS = ("api_key", "apisecret", "api_secret", "secret_key", "passphrase", "private_key", "tradeseekey")


class WorkbenchError(ValueError):
    """A message that can be shown to the visitor."""


def preset_file():
    override = os.environ.get("QSL_PRESET_PATH")
    return Path(override) if override else PRESET_PATH


def settings_file():
    override = os.environ.get("QSL_SETTINGS_PATH")
    return Path(override) if override else SETTINGS_PATH


def default_settings():
    return {
        "applied_preset": DEFAULT_NAME,
        "custom_note": "",
        "extra": {
            "early": 60,
            "strong": 80,
            "atr_stop": 1.5,
            "minimum_rr": 1.8,
            "structure": 20,
        },
        "modules": {
            "shortcut": {"short": 9, "long": 21, "rsi": 14},
            "intraday": {
                "ema": [9, 20, 50, 200],
                "macd": [12, 26, 9],
                "rsi": 14,
                "atr": 14,
                "boll": 20,
                "boll_mult": 2.0,
                "volume": 20,
                "threshold": 70,
                "rvol": 1.2,
                "extreme_atr_pct": 4.0,
                "shock_5m_pct": 0.8,
                "shock_15m_pct": 1.5,
                "shock_1h_pct": 2.5,
                "volume_shock": 1.8,
                "volume_high": 2.5,
                "weights": [30, 20, 20, 15, 15],
            },
            "ema_boll": {
                "enabled": False,
                "only_two_lines": True,
                "show_levels": True,
                "ema": 10,
                "interval": "15m",
                "inherit": True,
                "capital": 80.0,
                "risk_pct": 1.0,
                "leverage": 1.0,
                "atr_stop": 1.5,
                "fee_pct": 0.05,
                "slip_pct": 0.03,
            },
            "pullback": {
                "enabled": False,
                "both_sides": True,
                "show_levels": True,
                "inherit": True,
                "capital": 80.0,
                "risk_pct": 1.0,
                "leverage": 1.0,
                "rsi_long": 40.0,
                "rsi_short": 60.0,
                "fee_pct": 0.05,
                "slip_pct": 0.03,
            },
            "dynamic_stop": {
                "scheme": "ATR/结构",
                "boll": 20,
                "boll_mult": 2.0,
                "atr": 14,
                "minimum_rr": 1.5,
                "structure_buffer": 0.3,
                "atr_guard": 1.5,
                "second_atr": 3.0,
                "max_stop_pct": 6.0,
                "trail_start_r": 1.0,
                "trail_method": "布林中轨",
                "trail_atr": 2.0,
                "breakeven": True,
                "trailing": True,
            },
            "macro": {
                "daily_fast": 10,
                "daily_slow": 30,
                "weekly_fast": 10,
                "weekly_slow": 30,
                "swing": 5,
            },
            "fib": {"swing": 5, "lookback": 180, "interval": "1D"},
            "cross": {"inherit": True},
            "ui": {
                "up": "#2fdb93",
                "down": "#ff5c7a",
                "label_size": 10,
                "atr_adaptive": True,
            },
        },
    }


def _merge(base, incoming):
    if isinstance(base, dict) and isinstance(incoming, dict):
        merged = dict(base)
        for key, value in incoming.items():
            merged[key] = _merge(base[key], value) if key in base else value
        return merged
    return incoming


def _num(obj, key, lo, hi, integer=False):
    if not isinstance(obj, dict) or key not in obj or obj[key] is None or obj[key] == "":
        raise WorkbenchError(f"{key} 不能为空")
    try:
        value = float(obj[key])
    except (TypeError, ValueError):
        raise WorkbenchError(f"{key} 不是数字") from None
    if value != value or value in (float("inf"), float("-inf")) or value < lo or value > hi:
        raise WorkbenchError(f"{key} 超出允许范围")
    return int(round(value)) if integer else value


def _seq(obj, key, size, lo, hi, integer=False):
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise WorkbenchError(f"{key} 需要 {size} 个数字")
    return [_num({key: item}, key, lo, hi, integer) for item in value]


def _reject_secrets(body):
    try:
        blob = json.dumps(body, ensure_ascii=False).lower()
    except TypeError as exc:
        raise WorkbenchError("配置里有无法保存的内容") from exc
    if any(marker in blob for marker in SECRET_MARKERS):
        raise WorkbenchError("这里不接收交易密钥")


def validate_settings(body, previous=None):
    if not isinstance(body, dict):
        raise WorkbenchError("配置格式无效")
    _reject_secrets(body)
    settings = _merge(default_settings(), body)
    modules = settings["modules"]
    shortcut = modules["shortcut"]
    shortcut["short"] = _num(shortcut, "short", 2, 350, True)
    shortcut["long"] = _num(shortcut, "long", 2, 350, True)
    shortcut["rsi"] = _num(shortcut, "rsi", 2, 150, True)
    intra = modules["intraday"]
    intra["ema"] = _seq(intra, "ema", 4, 2, 350, True)
    intra["macd"] = _seq(intra, "macd", 3, 2, 150, True)
    if not intra["macd"][0] < intra["macd"][1]:
        raise WorkbenchError("MACD 快线周期需要小于慢线")
    for key, lo, hi, integer in (
        ("rsi", 2, 150, True), ("atr", 2, 150, True), ("boll", 2, 150, True),
        ("boll_mult", 0.5, 5, False), ("volume", 2, 150, True), ("threshold", 1, 100, True),
        ("rvol", 0.5, 5, False), ("extreme_atr_pct", 0.5, 20, False),
        ("shock_5m_pct", 0.1, 10, False), ("shock_15m_pct", 0.1, 20, False),
        ("shock_1h_pct", 0.1, 30, False), ("volume_shock", 0.5, 10, False),
        ("volume_high", 0.5, 10, False),
    ):
        intra[key] = _num(intra, key, lo, hi, integer)
    intra["weights"] = _seq(intra, "weights", 5, 0, 100, True)
    extra = settings["extra"]
    extra["early"] = _num(extra, "early", 1, 100, True)
    extra["strong"] = _num(extra, "strong", 1, 100, True)
    extra["atr_stop"] = _num(extra, "atr_stop", 0.5, 8, False)
    extra["minimum_rr"] = _num(extra, "minimum_rr", 0.5, 10, False)
    extra["structure"] = _num(extra, "structure", 2, 200, True)
    ema = modules["ema_boll"]
    ema["enabled"] = bool(ema.get("enabled"))
    ema["only_two_lines"] = bool(ema.get("only_two_lines"))
    ema["show_levels"] = bool(ema.get("show_levels"))
    ema["inherit"] = bool(ema.get("inherit"))
    ema["ema"] = _num(ema, "ema", 2, 350, True)
    if ema.get("interval") not in {"5m", "15m", "1H", "4H"}:
        raise WorkbenchError("EMA×布林周期无效")
    ema["capital"] = _num(ema, "capital", 10, 100000, False)
    ema["risk_pct"] = _num(ema, "risk_pct", 0.1, 5, False)
    ema["leverage"] = _num(ema, "leverage", 1, 20, False)
    ema["atr_stop"] = _num(ema, "atr_stop", 0.5, 8, False)
    ema["fee_pct"] = _num(ema, "fee_pct", 0, 1, False)
    ema["slip_pct"] = _num(ema, "slip_pct", 0, 1, False)
    pull = modules["pullback"]
    pull["enabled"] = bool(pull.get("enabled"))
    pull["both_sides"] = bool(pull.get("both_sides"))
    pull["show_levels"] = bool(pull.get("show_levels"))
    pull["inherit"] = bool(pull.get("inherit"))
    pull["capital"] = _num(pull, "capital", 10, 100000, False)
    pull["risk_pct"] = _num(pull, "risk_pct", 0.1, 5, False)
    pull["leverage"] = _num(pull, "leverage", 1, 20, False)
    pull["rsi_long"] = _num(pull, "rsi_long", 1, 50, False)
    pull["rsi_short"] = _num(pull, "rsi_short", 50, 99, False)
    pull["fee_pct"] = _num(pull, "fee_pct", 0, 1, False)
    pull["slip_pct"] = _num(pull, "slip_pct", 0, 1, False)
    stop = modules["dynamic_stop"]
    if stop.get("scheme") not in {"ATR/结构", "布林动态"}:
        raise WorkbenchError("止盈止损方案无效")
    if stop.get("trail_method") not in {"布林中轨", "ATR 跟踪"}:
        raise WorkbenchError("追踪方法无效")
    for key, lo, hi, integer in (
        ("boll", 2, 150, True), ("boll_mult", 0.5, 5, False), ("atr", 2, 150, True),
        ("minimum_rr", 0.5, 10, False), ("structure_buffer", 0, 5, False),
        ("atr_guard", 0.5, 8, False), ("second_atr", 0.5, 10, False),
        ("max_stop_pct", 0.5, 20, False), ("trail_start_r", 0.1, 5, False),
        ("trail_atr", 0.5, 8, False),
    ):
        stop[key] = _num(stop, key, lo, hi, integer)
    stop["breakeven"] = bool(stop.get("breakeven"))
    stop["trailing"] = bool(stop.get("trailing"))
    macro = modules["macro"]
    for key, lo, hi in (("daily_fast", 2, 200), ("daily_slow", 3, 400), ("weekly_fast", 2, 200), ("weekly_slow", 3, 400), ("swing", 2, 30)):
        macro[key] = _num(macro, key, lo, hi, True)
    fib = modules["fib"]
    fib["swing"] = _num(fib, "swing", 2, 30, True)
    fib["lookback"] = _num(fib, "lookback", 20, 500, True)
    if fib.get("interval") not in {"1D", "1W"}:
        raise WorkbenchError("斐波那契周期无效")
    modules["cross"]["inherit"] = bool(modules["cross"].get("inherit", True))
    ui = modules["ui"]
    ui["atr_adaptive"] = bool(ui.get("atr_adaptive", True))
    ui["label_size"] = _num(ui, "label_size", 8, 24, True)
    for color_key in ("up", "down"):
        color = str(ui.get(color_key) or "")
        if len(color) != 7 or not color.startswith("#"):
            raise WorkbenchError("颜色需要是 #RRGGBB")
        ui[color_key] = color.lower()
    name = str(settings.get("applied_preset") or DEFAULT_NAME)
    known = set(PRESET_NAMES) | set(list_custom(preset_file()))
    if name not in known and name != "自定义":
        if previous and previous.get("applied_preset") in known:
            name = previous["applied_preset"]
        else:
            name = DEFAULT_NAME
    settings["applied_preset"] = name
    settings["custom_note"] = str(settings.get("custom_note") or "")[:200]
    return settings


def load_settings():
    path = settings_file()
    if not path.is_file():
        return default_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_settings()
    try:
        return validate_settings(payload)
    except WorkbenchError:
        return default_settings()


def save_settings(settings):
    clean = validate_settings(settings, previous=load_settings())
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def preset_from_settings(name, settings):
    intra = settings["modules"]["intraday"]
    extra = settings["extra"]
    return Preset(
        name=name,
        ema=tuple(intra["ema"]),
        macd=tuple(intra["macd"]),
        rsi=int(intra["rsi"]),
        atr=int(intra["atr"]),
        boll=int(intra["boll"]),
        boll_mult=float(intra["boll_mult"]),
        volume=int(intra["volume"]),
        rvol=float(intra["rvol"]),
        early=int(extra["early"]),
        threshold=int(intra["threshold"]),
        strong=int(extra["strong"]),
        atr_stop=float(extra["atr_stop"]),
        minimum_rr=float(extra["minimum_rr"]),
        structure=int(extra["structure"]),
        weights=tuple(int(item) for item in intra["weights"]),
        note=settings.get("custom_note") or "由网页工作副本保存。这是测试配置，不是最优参数。",
    )


def _copy_preset_into(settings, preset):
    """Copy indicator numbers. Leave leverage and per-trade risk untouched."""
    settings = deepcopy(settings)
    settings["applied_preset"] = preset.name if preset.name in BUILTINS or preset.name in list_custom(preset_file()) else settings["applied_preset"]
    if preset.name not in BUILTINS and preset.name not in list_custom(preset_file()):
        settings["applied_preset"] = "自定义"
    intra = settings["modules"]["intraday"]
    intra["ema"] = list(preset.ema)
    intra["macd"] = list(preset.macd)
    intra["rsi"] = preset.rsi
    intra["atr"] = preset.atr
    intra["boll"] = preset.boll
    intra["boll_mult"] = preset.boll_mult
    intra["volume"] = preset.volume
    intra["rvol"] = preset.rvol
    intra["threshold"] = preset.threshold
    intra["weights"] = list(preset.weights)
    settings["modules"]["shortcut"]["short"] = preset.ema[0]
    settings["modules"]["shortcut"]["rsi"] = preset.rsi
    settings["extra"].update({
        "early": preset.early,
        "strong": preset.strong,
        "atr_stop": preset.atr_stop,
        "minimum_rr": preset.minimum_rr,
        "structure": preset.structure,
    })
    return settings


def apply_named_preset(name):
    """Switch the working copy. Never creates or rewrites a saved profile."""
    path = preset_file()
    if name in BUILTINS:
        preset = BUILTINS[name]
    elif name == "自定义":
        preset, _source = resolved_custom(path)
    else:
        preset = load_preset(name, path)
    settings = _copy_preset_into(load_settings(), preset)
    settings["applied_preset"] = name if name != "自定义" else preset.name
    if name == "自定义" and preset.name == "自定义":
        settings["applied_preset"] = "自定义"
    return save_settings(settings)


def save_named_custom(name, settings=None):
    settings = settings or load_settings()
    if not name or name in BUILTINS:
        raise WorkbenchError("自定义名称不能为空，也不能与内置预设重名")
    preset = preset_from_settings(name, settings)
    save_custom(name, preset, preset_file())
    return name


def working_signature(settings):
    intra = settings["modules"]["intraday"]
    extra = settings["extra"]
    return {
        "ema": list(intra["ema"]),
        "macd": list(intra["macd"]),
        "rsi": intra["rsi"],
        "atr": intra["atr"],
        "boll": intra["boll"],
        "boll_mult": intra["boll_mult"],
        "volume": intra["volume"],
        "rvol": intra["rvol"],
        "threshold": intra["threshold"],
        "weights": list(intra["weights"]),
        "early": extra["early"],
        "strong": extra["strong"],
        "atr_stop": extra["atr_stop"],
        "minimum_rr": extra["minimum_rr"],
    }


def preset_signature(preset):
    return {
        "ema": list(preset.ema),
        "macd": list(preset.macd),
        "rsi": preset.rsi,
        "atr": preset.atr,
        "boll": preset.boll,
        "boll_mult": preset.boll_mult,
        "volume": preset.volume,
        "rvol": preset.rvol,
        "threshold": preset.threshold,
        "weights": list(preset.weights),
        "early": preset.early,
        "strong": preset.strong,
        "atr_stop": preset.atr_stop,
        "minimum_rr": preset.minimum_rr,
    }


def drift_note(settings):
    name = settings.get("applied_preset")
    try:
        if name == "自定义":
            preset, source = resolved_custom(preset_file())
            if source != "saved":
                return False, "自定义还没有单独保存的配置，当前沿用均衡型默认测试值。"
        elif name in BUILTINS or name in list_custom(preset_file()):
            preset = load_preset(name, preset_file())
        else:
            return False, ""
    except KeyError:
        return False, ""
    drifted = working_signature(settings) != preset_signature(preset)
    if not drifted:
        return False, ""
    return True, "工作副本已偏离所应用预设。图表、评分、回测和页内提醒使用工作副本，不会改写已保存配置。"


def presets_public():
    path = preset_file()
    custom, source = resolved_custom(path)
    return {
        "names": list(PRESET_NAMES),
        "default": DEFAULT_NAME,
        "table": preset_table(),
        "notes": {name: item.note for name, item in BUILTINS.items()},
        "custom_names": list(list_custom(path)),
        "custom_preview": preset_signature(custom),
        "custom_source": source,
        "custom_note": custom.note,
    }


def sync_shortcut(settings):
    settings = deepcopy(settings)
    shortcut = settings["modules"]["shortcut"]
    settings["modules"]["intraday"]["ema"][0] = shortcut["short"]
    settings["modules"]["intraday"]["rsi"] = shortcut["rsi"]
    return save_settings(settings)


def delete_named(name):
    delete_custom(name, preset_file())


def rename_named(old, new):
    if new in BUILTINS:
        raise WorkbenchError("不能把自定义配置改成内置预设的名字")
    rename_custom(old, new, preset_file())


def copy_named(src, dest):
    copy_custom(src, dest, preset_file())


def export_named():
    return export_custom(preset_file())


def import_named(text, replace=False):
    _reject_secrets(json.loads(text))
    return import_custom(text, preset_file(), replace=replace)
