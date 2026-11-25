"""Causal 15-minute mean-reversion research rule.

Signals use only the closed bar's Bollinger bands, RSI, and ATR. The shared
fill engine still enters on the next bar's open. This does not replace the
default trend score, and the paper worker does not call it.
"""
import numpy as np

from signals import entry_terms


def score_mean_reversion(base, rules, fee, slip, strategy):
    result = base.copy()
    atr = base.atr
    low_band = base.close < base.boll_low
    high_band = base.close > base.boll_up
    ready = base[["close", "boll_low", "boll_up", "boll_mid", "rsi", "atr"]].notna().all(axis=1)
    plans = {
        "long": (
            ready & low_band & (base.rsi < strategy.mr_rsi_low),
            base.close - strategy.mr_atr_stop * atr,
            base.boll_mid,
            base.boll_up,
            "收盘低于布林下轨, RSI 低于阈值",
        ),
        "short": (
            ready & high_band & (base.rsi > strategy.mr_rsi_high),
            base.close + strategy.mr_atr_stop * atr,
            base.boll_mid,
            base.boll_low,
            "收盘高于布林上轨, RSI 高于阈值",
        ),
    }
    for side, suffix in ((1, "long"), (-1, "short")):
        trigger, stop, target, target2, reason = plans[suffix]
        entry, risk, reward = entry_terms(side, base.close, stop, target, fee, slip)
        rr = reward / risk.where(risk > 0)
        valid = trigger & (rr >= rules.minimum_rr) & ((entry - stop) * side > 0) & ((target2 - target) * side >= 0)
        result[f"stop_{suffix}"] = stop
        result[f"target1_{suffix}"] = target
        result[f"target2_{suffix}"] = target2
        result[f"rr_{suffix}"] = rr
        result[f"valid_{suffix}"] = valid
        result[f"score_{suffix}"] = np.where(valid, 100, 0)
        result[f"reasons_{suffix}"] = np.where(valid, reason, "")
    both = result.valid_long & result.valid_short
    result["side"] = np.select(
        [result.valid_long & ~both, result.valid_short & ~both], [1, -1], default=0,
    )
    result["grade"] = np.where(result.side != 0, "回归候选", "观望")
    return result
