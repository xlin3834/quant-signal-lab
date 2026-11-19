"""Explainable rule scores and structure-based simulation plans."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from indicators import Parameters, calculate
from market_data import INTERVALS

WEIGHTS = {"趋势结构": 30, "动量指标": 20, "成交量确认": 20, "市场形态": 15, "波动率与风险": 15}


@dataclass(frozen=True)
class Rules:
    threshold: int = 65
    strong: int = 80
    minimum_rr: float = 1.5


def aligned_frames(frames, params=Parameters()):
    base = calculate(frames["15m"], params)
    base["decision_time"] = base.time + INTERVALS["15m"][1]
    base["swing_low"] = base.low.shift().rolling(8).min()
    base["swing_high"] = base.high.shift().rolling(8).max()
    base["far_low"] = base.low.shift().rolling(params.structure * 4).min()
    base["far_high"] = base.high.shift().rolling(params.structure * 4).max()
    for interval in ("4H", "1H", "5m"):
        duration = INTERVALS[interval][1]
        higher = calculate(frames[interval], params)
        key = f"closed_{interval}"
        higher[key] = higher.time + duration
        higher[f"direction_{interval}"] = np.select(
            [(higher.ema20 > higher.ema50) & (higher.close > higher.ema50),
             (higher.ema20 < higher.ema50) & (higher.close < higher.ema50)], [1.0, -1.0], default=0.0,
        )
        higher[f"rsi_{interval}"] = higher.rsi
        base = pd.merge_asof(
            base, higher[[key, f"direction_{interval}", f"rsi_{interval}"]],
            left_on="decision_time", right_on=key, direction="backward",
            tolerance=duration - pd.Timedelta(milliseconds=1),
        )
    return base


def entry_terms(side, opening, stop, target, fee, slip):
    entry = opening * (1 + slip * side)
    stop_fill = stop * (1 - slip * side)
    target_fill = target * (1 - slip * side)
    risk = side * (entry - stop_fill) + fee * (entry + stop_fill)
    reward = side * (target_fill - entry) - fee * (entry + target_fill)
    return entry, risk, reward


def score_frames(base, rules=Rules(), fee=.0006, slip=.0003):
    result = base.copy()
    complete = base[["ema7", "rsi", "macd_signal", "rsi_5m", "vol_avg", "atr"]].notna().all(axis=1)
    spread = (base.high - base.low).where(base.high > base.low)
    candle_location = (base.close - base.low) / spread
    for side, suffix in ((1, "long"), (-1, "short")):
        structure = (base.direction_4H == side) & (base.direction_1H == side)
        momentum = ((base.rsi - 50) * side > 0) & ((base.macd - base.macd_signal) * side > 0)
        volume = (base.volume >= base.vol_avg) & ((base.close - base.open) * side > 0)
        pattern = (((candle_location - .5) * side >= .15)
                   & ((base.close - base.ema7) * side > 0) & ((base.rsi_5m - 50) * side > 0))
        volatility = (base.atr / base.close).between(.001, .03)
        checks = (structure, momentum, volume, pattern, volatility)
        points = sum(check.astype(int) * weight for check, weight in zip(checks, WEIGHTS.values()))
        result[f"score_{suffix}"] = points
        result[f"reasons_{suffix}"] = [", ".join(label for label, passed in zip(WEIGHTS, flags) if passed)
                                      for flags in zip(*checks)]
        stop = base.swing_low if side == 1 else base.swing_high
        target = base.resistance if side == 1 else base.support
        target2 = base.far_high if side == 1 else base.far_low
        entry, risk, reward = entry_terms(side, base.close, stop, target, fee, slip)
        rr = reward / risk.where(risk > 0)
        valid = (complete & structure & (points >= rules.threshold) & (rr >= rules.minimum_rr)
                 & ((entry - stop) * side > 0) & ((target2 - target) * side >= 0))
        for key, value in {"stop": stop, "target1": target, "target2": target2,
                           "rr": rr, "valid": valid}.items():
            result[f"{key}_{suffix}"] = value
    result["side"] = np.select([result.valid_long, result.valid_short], [1, -1], default=0)
    result["grade"] = np.select(
        [result.valid_long & (result.score_long >= rules.strong),
         result.valid_short & (result.score_short >= rules.strong), result.side != 0],
        ["强候选", "强候选", "普通候选"], default="观望",
    )
    return result


def make_plan(row):
    side = int(row.side)
    if side == 0:
        return None
    suffix = "long" if side == 1 else "short"
    return {"side": side, "stop": float(row[f"stop_{suffix}"]),
            "target1": float(row[f"target1_{suffix}"]), "target2": float(row[f"target2_{suffix}"])}
