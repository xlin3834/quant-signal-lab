"""Five original parameter modules connected to the calculation engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd

from indicators import Parameters, _ema, calculate
from signals import entry_terms


@dataclass(frozen=True)
class ShortcutMA:
    short: int = 9
    long: int = 21
    rsi: int = 14


@dataclass(frozen=True)
class IntradayScore:
    ema: tuple = (9, 20, 50, 200)
    macd: tuple = (12, 26, 9)
    rsi: int = 14
    atr: int = 14
    boll: int = 20
    boll_mult: float = 2.0
    volume: int = 20
    threshold: int = 70
    rvol: float = 1.2
    extreme_atr: float = 0.04
    shock_5m: float = 0.008
    shock_15m: float = 0.015
    shock_1h: float = 0.025
    volume_shock: float = 1.8
    volume_high: float = 2.5
    weights: tuple = (30, 20, 20, 15, 15)


@dataclass(frozen=True)
class EmaBoll:
    enabled: bool = False
    only_two_lines: bool = True
    show_levels: bool = True
    ema: int = 10
    interval: str = "15m"
    inherit: bool = True
    capital: float = 80.0
    risk: float = 0.01
    leverage: float = 1.0
    atr_stop: float = 1.5
    fee: float = 0.0005
    slip: float = 0.0003


@dataclass(frozen=True)
class Pullback:
    enabled: bool = False
    both_sides: bool = True
    show_levels: bool = True
    inherit: bool = True
    capital: float = 80.0
    risk: float = 0.01
    leverage: float = 1.0
    rsi_long: float = 40.0
    rsi_short: float = 60.0
    fee: float = 0.0005
    slip: float = 0.0003


@dataclass(frozen=True)
class DynamicStop:
    scheme: str = "ATR/结构"
    boll: int = 20
    boll_mult: float = 2.0
    atr: int = 14
    minimum_rr: float = 1.5
    structure_buffer: float = 0.3
    atr_guard: float = 1.5
    second_atr: float = 3.0
    max_stop_pct: float = 0.06
    trail_start_r: float = 1.0
    trail_method: str = "布林中轨"
    trail_atr: float = 2.0
    breakeven: bool = True
    trailing: bool = True


def default_modules():
    return ShortcutMA(), IntradayScore(), EmaBoll(), Pullback(), DynamicStop()


def sync_shortcut(shortcut: ShortcutMA, params: Parameters):
    """Keep the MA/RSI shortcut and unified indicator settings aligned."""
    ema = (shortcut.short, params.ema[1], params.ema[2], params.ema[3])
    return Parameters(ema, params.boll, shortcut.rsi, params.macd, params.atr, params.volume, params.structure, params.boll_mult)


def shortcut_from_params(params: Parameters, long_ma=21):
    return ShortcutMA(params.ema[0], long_ma, params.rsi)


def shocks(frames, settings: IntradayScore):
    result = {}
    mapping = {"5m": settings.shock_5m, "15m": settings.shock_15m, "1H": settings.shock_1h}
    for interval, threshold in mapping.items():
        if interval not in frames:
            continue
        data = frames[interval]
        move = (data.close - data.open).abs() / data.open.replace(0, np.nan)
        rvol = data.volume / data.get("vol_avg", data.volume.rolling(settings.volume).mean())
        last = data.iloc[-1]
        result[interval] = {
            "move": float(move.iloc[-1]),
            "threshold": threshold,
            "shocked": bool(move.iloc[-1] >= threshold),
            "rvol": None if pd.isna(rvol.iloc[-1]) else float(rvol.iloc[-1]),
            "volume_flag": (None if pd.isna(rvol.iloc[-1]) else
                            "高等级量能" if rvol.iloc[-1] >= settings.volume_high
                            else "量能异动" if rvol.iloc[-1] >= settings.volume_shock else "正常"),
            "close": float(last.close),
        }
    return result


def environment(frame):
    """Suggest a research regime. Never auto-overrides the user's preset."""
    data = calculate(frame) if "ema20" not in frame else frame
    last = data.dropna(subset=["ema20", "ema50", "atr"]).iloc[-1]
    width = (last.boll_up - last.boll_low) / last.close
    mid_width = ((data.boll_up - data.boll_low) / data.close).median()
    slope = last.ema20 - data.ema20.shift(5).loc[last.name]
    if last.atr / last.close >= 0.03:
        regime, reason = "高波动行情", f"ATR 占收盘价 {last.atr / last.close:.2%}"
    elif last.atr / last.close <= 0.008:
        regime, reason = "低波动行情", f"ATR 占收盘价 {last.atr / last.close:.2%}"
    elif abs(slope) / last.close < 0.002 and width <= mid_width * 1.2:
        regime, reason = "横盘震荡", "EMA 斜率平坦且布林带宽未扩张"
    elif (last.ema20 - last.ema50) * slope < 0:
        regime, reason = "趋势转换阶段", "短线 EMA 斜率与快慢均线方向不一致"
    elif last.ema20 > last.ema50 and last.close > last.ema50:
        regime, reason = "上涨趋势", "收盘与 EMA 排列向上"
    elif last.ema20 < last.ema50 and last.close < last.ema50:
        regime, reason = "下跌趋势", "收盘与 EMA 排列向下"
    else:
        regime, reason = "横盘震荡", "暂无明确单边排列"
    suggestion = {
        "上涨趋势": "稳健型或均衡型",
        "下跌趋势": "稳健型或均衡型",
        "横盘震荡": "震荡行情型",
        "高波动行情": "稳健型",
        "低波动行情": "均衡型或敏捷型",
        "趋势转换阶段": "均衡型（观望为主）",
    }[regime]
    return {"regime": regime, "reason": reason, "suggestion": suggestion, "auto_apply": False}


def ema_boll_plan(frame, module: EmaBoll, params: Parameters):
    if not module.enabled:
        return None
    data = calculate(frame, params).copy()
    ema = _ema(data.close, module.ema)
    data["strategy_ema"] = ema
    mid = data.boll_mid
    long_x = (ema > mid) & (ema.shift() <= mid.shift())
    short_x = (ema < mid) & (ema.shift() >= mid.shift())
    last = data.iloc[-1]
    if pd.isna(last.atr) or pd.isna(last.strategy_ema) or pd.isna(last.boll_mid):
        return {"ready": False, "reason": "指标尚未完成预热"}
    side = 1 if bool(long_x.iloc[-1]) else -1 if bool(short_x.iloc[-1]) else 0
    stop = float(last.close - module.atr_stop * last.atr) if side == 1 else float(last.close + module.atr_stop * last.atr)
    entry, risk, reward = entry_terms(side or 1, last.close, stop, last.boll_up if side >= 0 else last.boll_low,
                                      module.fee, module.slip)
    qty = (module.capital * module.risk) / risk if side and risk > 0 else 0.0
    return {
        "ready": True, "side": side, "ema": float(last.strategy_ema), "mid": float(last.boll_mid),
        "entry": float(last.close), "stop": stop, "qty": float(qty * module.leverage) if side else 0.0,
        "interval": module.interval, "inherit": module.inherit,
    }


def pullback_plan(frames, module: Pullback, params: Parameters):
    if not module.enabled:
        return None
    base = calculate(frames["15m"], params)
    higher = calculate(frames.get("1H", frames["15m"]), params)
    last = base.iloc[-1]
    trend = 1 if higher.ema20.iloc[-1] > higher.ema50.iloc[-1] else -1 if higher.ema20.iloc[-1] < higher.ema50.iloc[-1] else 0
    entries = []
    if module.both_sides or trend == 1:
        if last.rsi <= module.rsi_long and last.low <= last.ema20 <= last.high:
            entries.append({"side": 1, "entry": float(last.ema20), "rsi": float(last.rsi), "label": "回踩短线 EMA 做多"})
    if module.both_sides or trend == -1:
        if last.rsi >= module.rsi_short and last.low <= last.ema20 <= last.high:
            entries.append({"side": -1, "entry": float(last.ema20), "rsi": float(last.rsi), "label": "回抽短线 EMA 做空"})
    return {"trend": trend, "entries": entries, "close": float(last.close), "inherit": module.inherit}


def dynamic_levels(row, module: DynamicStop, side, entry, stop, target, target2, atr):
    """Map ATR/structure or Bollinger trailing plans. No live orders are placed."""
    if module.scheme == "ATR/结构":
        buffer = module.structure_buffer * atr
        guard = entry - side * module.atr_guard * atr
        planned_stop = stop if side * (entry - stop) > 0 else guard
        if abs(entry - planned_stop) / entry > module.max_stop_pct:
            planned_stop = entry - side * module.max_stop_pct * entry
        second = entry + side * module.second_atr * atr
        return {"scheme": module.scheme, "stop": float(planned_stop), "target1": float(target),
                "target2": float(target2 if side * (target2 - entry) >= side * (second - entry) else second),
                "buffer": float(buffer), "breakeven": module.breakeven, "trailing": False}
    mid = float(row["boll_mid"])
    trail = mid if module.trail_method == "布林中轨" else entry - side * module.trail_atr * atr
    planned_stop = float(row["boll_low"] if side == 1 else row["boll_up"])
    if abs(entry - planned_stop) / entry > module.max_stop_pct:
        planned_stop = entry - side * module.max_stop_pct * entry
    return {"scheme": module.scheme, "stop": float(planned_stop), "target1": mid,
            "target2": float(entry + side * module.second_atr * atr),
            "trail": float(trail), "start_r": module.trail_start_r,
            "breakeven": module.breakeven, "trailing": module.trailing}
