"""Independent macro-cycle and Fibonacci research calculations.

These parameters are managed separately from strategy presets and must not
replace the five collapsible strategy modules. Swing points are confirmed only
after a symmetric number of later closed bars, so completed values do not
repaint. Environment output may only suggest a preset, never auto-apply it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indicators import _ema
from market_data import MarketDataError, parse_candles, request_data
from swap_data import PRODUCT, ROOT

RETRACEMENTS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
EXTENSIONS = (1.272, 1.618, 2.0)


@dataclass(frozen=True)
class MacroParams:
    daily_fast: int = 10
    daily_slow: int = 30
    weekly_fast: int = 10
    weekly_slow: int = 30
    swing: int = 5


@dataclass(frozen=True)
class FibParams:
    swing: int = 5
    lookback: int = 180
    interval: str = "1D"
    retracements: tuple = RETRACEMENTS
    extensions: tuple = EXTENSIONS


def fetch_macro_candles(symbol, granularity, limit=200):
    if granularity not in {"1D", "1W", "1M"}:
        raise MarketDataError(f"不支持的宏观周期 {granularity}")
    return parse_candles(request_data(f"{ROOT}/candles", {
        "symbol": symbol, "productType": PRODUCT,
        "granularity": granularity, "limit": str(limit),
    }))


def closed_macro(data, granularity, now=None):
    if data is None or data.empty:
        raise MarketDataError("宏观周期 K 线为空")
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    last = data.time.iloc[-1]
    if last > now:
        raise MarketDataError("宏观周期时间戳晚于当前时间")
    forming = False
    if granularity == "1D":
        forming = last.floor("D") + pd.Timedelta(days=1) > now
    elif granularity == "1W":
        forming = last + pd.Timedelta(days=7) > now
    elif granularity == "1M":
        forming = (last.year, last.month) == (now.year, now.month)
    closed = data.iloc[:-1] if forming else data
    if closed.empty:
        raise MarketDataError("尚无已收盘的宏观周期 K 线")
    return closed.reset_index(drop=True)


def _resample(monthly, rule):
    frame = monthly.set_index("time").sort_index()
    out = frame.resample(rule, label="right", closed="right").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna(subset=["open", "close"])
    out = out.reset_index()
    return out


def quarterly_from_monthly(monthly, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    data = _resample(monthly, "QE")
    if data.empty:
        raise MarketDataError("季度 K 线不足")
    last = data.time.iloc[-1]
    if (last.year, (last.month - 1) // 3) == (now.year, (now.month - 1) // 3):
        data = data.iloc[:-1]
    if data.empty:
        raise MarketDataError("尚无已收盘的季度 K 线")
    return data.reset_index(drop=True)


def yearly_from_monthly(monthly, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    data = _resample(monthly, "YE")
    if data.empty:
        raise MarketDataError("年度 K 线不足")
    if data.time.iloc[-1].year == now.year:
        data = data.iloc[:-1]
    if data.empty:
        raise MarketDataError("尚无已收盘的年度 K 线")
    return data.reset_index(drop=True)


def confirmed_swings(data, left=5):
    """Fractal swings confirmed only after `left` later closed bars."""
    if len(data) < 2 * left + 1:
        return pd.DataFrame(columns=["time", "price", "kind", "index", "confirmed_time"])
    high, low, time = data.high.to_numpy(), data.low.to_numpy(), data.time
    rows = []
    last = len(data) - 1
    for i in range(left, last - left + 1):
        window_high = high[i - left:i + left + 1]
        window_low = low[i - left:i + left + 1]
        if high[i] == window_high.max() and np.argmax(window_high) == left:
            rows.append({"time": time.iloc[i], "price": float(high[i]), "kind": "high",
                         "index": i, "confirmed_time": time.iloc[i + left]})
        if low[i] == window_low.min() and np.argmin(window_low) == left:
            rows.append({"time": time.iloc[i], "price": float(low[i]), "kind": "low",
                         "index": i, "confirmed_time": time.iloc[i + left]})
    return pd.DataFrame(rows)


def _active_leg(swings):
    if swings is None or len(swings) < 2:
        return None
    ordered = swings.sort_values(["index", "kind"]).drop_duplicates(["index", "kind"])
    last = ordered.iloc[-1]
    prior = ordered[ordered["index"] < last["index"]]
    opposite = prior[prior.kind != last.kind]
    if opposite.empty:
        return None
    start = opposite.iloc[-1]
    return start, last


def fibonacci_grid(data, params=FibParams(), now=None):
    window = data.tail(params.lookback).reset_index(drop=True) if params.lookback else data
    swings = confirmed_swings(window, params.swing)
    leg = _active_leg(swings)
    if leg is None:
        return {"ready": False, "reason": "已确认摆动点不足，无法计算斐波那契", "swings": swings}
    start, end = leg
    low_to_high = end.kind == "high"
    origin, extreme = (float(start.price), float(end.price))
    span = extreme - origin
    if span == 0:
        return {"ready": False, "reason": "摆动区间为零", "swings": swings}
    retracements = {ratio: extreme - span * ratio for ratio in params.retracements}
    extensions = {ratio: origin + span * ratio for ratio in params.extensions}
    close = float(window.close.iloc[-1])
    all_levels = {**{f"回撤 {ratio:g}": price for ratio, price in retracements.items()},
                  **{f"延伸 {ratio:g}": price for ratio, price in extensions.items()}}
    nearest = min(all_levels, key=lambda name: abs(all_levels[name] - close))
    return {
        "ready": True,
        "interval": params.interval,
        "direction": "上升摆动" if low_to_high else "下降摆动",
        "start_time": start.time, "start_price": origin,
        "end_time": end.time, "end_price": extreme,
        "confirmed_time": end.confirmed_time,
        "close": close,
        "retracements": retracements,
        "extensions": extensions,
        "nearest": nearest,
        "nearest_price": all_levels[nearest],
        "swings": swings,
    }


def _trend_stage(data, fast, slow, label):
    if len(data) < max(fast, slow) * 2 + 3:
        return {"label": label, "stage": "数据不足", "reason": "已收盘 K 线不足以计算宏观均线"}
    close = data.close
    ema_fast, ema_slow = _ema(close, fast), _ema(close, slow)
    last_close, last_fast, last_slow = float(close.iloc[-1]), float(ema_fast.iloc[-1]), float(ema_slow.iloc[-1])
    if not np.isfinite([last_fast, last_slow]).all():
        return {"label": label, "stage": "数据不足", "reason": "宏观均线尚未完成预热"}
    slope = float(ema_fast.iloc[-1] - ema_fast.shift(3).iloc[-1])
    if last_fast > last_slow and last_close > last_slow and slope > 0:
        stage, reason = "上升阶段", f"收盘位于慢线之上，{label} 快慢均线向上"
    elif last_fast < last_slow and last_close < last_slow and slope < 0:
        stage, reason = "下降阶段", f"收盘位于慢线之下，{label} 快慢均线向下"
    else:
        stage, reason = "整理阶段", f"{label} 快慢均线或收盘位置尚未形成单向排列"
    return {
        "label": label, "stage": stage, "reason": reason,
        "close": last_close, "ema_fast": last_fast, "ema_slow": last_slow,
        "time": data.time.iloc[-1],
    }


def _cycle(data, label):
    if len(data) < 4:
        return {"label": label, "phase": "数据不足", "reason": "周期样本过短"}
    close = data.close
    last = float(close.iloc[-1])
    low_i, high_i = int(close.idxmin()), int(close.idxmax())
    low_t, high_t = data.time.loc[close.idxmin()], data.time.loc[close.idxmax()]
    cycle_low, cycle_high = float(close.min()), float(close.max())
    span = cycle_high - cycle_low
    position = 0.5 if span == 0 else (last - cycle_low) / span
    if high_t > low_t and position >= 0.7:
        phase = "周期偏高"
    elif high_t < low_t and position <= 0.3:
        phase = "周期偏低"
    elif 0.4 <= position <= 0.6:
        phase = "周期中位"
    else:
        phase = "周期回撤或修复中"
    return {
        "label": label, "phase": phase, "position": float(position),
        "low": cycle_low, "high": cycle_high, "low_time": low_t, "high_time": high_t,
        "close": last, "time": data.time.iloc[-1],
        "reason": f"样本内从 {cycle_low:g} 到 {cycle_high:g}，现价位于 {position:.0%}",
    }


def analyze_macro(frames, params=MacroParams()):
    """`frames` keys may include 1D, 1W, 1M, 1Q, 1Y of already closed bars."""
    report = {"auto_apply": False}
    if "1D" in frames:
        report["daily"] = _trend_stage(frames["1D"], params.daily_fast, params.daily_slow, "日线")
    if "1W" in frames:
        report["weekly"] = _trend_stage(frames["1W"], params.weekly_fast, params.weekly_slow, "周线")
    if "1M" in frames:
        report["monthly"] = _cycle(frames["1M"], "月线")
    if "1Q" in frames:
        report["quarterly"] = _cycle(frames["1Q"], "季度线")
    if "1Y" in frames:
        report["yearly"] = _cycle(frames["1Y"], "年度线")
    stages = [report[key]["stage"] for key in ("daily", "weekly") if key in report and "stage" in report[key]]
    if stages and all(stage == "上升阶段" for stage in stages):
        suggestion = "稳健型或均衡型"
        summary = "日线与周线均为上升阶段"
    elif stages and all(stage == "下降阶段" for stage in stages):
        suggestion = "稳健型或均衡型"
        summary = "日线与周线均为下降阶段"
    elif "weekly" in report and report["weekly"].get("stage") == "整理阶段":
        suggestion = "震荡行情型"
        summary = "周线处于整理阶段"
    else:
        suggestion = "均衡型（观望为主）"
        summary = "宏观周期方向不一致或样本不足"
    report["structure"] = {key: confirmed_swings(value, params.swing)
                           for key, value in frames.items() if key in {"1D", "1W"}}
    report["suggestion"] = suggestion
    report["summary"] = summary
    return report


def load_macro_frames(symbol, now=None, limit=200):
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    daily = closed_macro(fetch_macro_candles(symbol, "1D", limit), "1D", now)
    weekly = closed_macro(fetch_macro_candles(symbol, "1W", min(limit, 200)), "1W", now)
    monthly = closed_macro(fetch_macro_candles(symbol, "1M", min(limit, 200)), "1M", now)
    frames = {"1D": daily, "1W": weekly, "1M": monthly}
    try:
        frames["1Q"] = quarterly_from_monthly(monthly, now)
    except MarketDataError:
        pass
    try:
        frames["1Y"] = yearly_from_monthly(monthly, now)
    except MarketDataError:
        pass
    return frames
