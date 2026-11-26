"""Chronological simulated fills shared by the backtest and paper worker."""
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from signals import Rules, entry_terms, score_frames

TRADE_COLUMNS = ["time", "side", "entry", "exit", "units", "pnl", "reason"]


@dataclass(frozen=True)
class Costs:
    capital: float = 1000
    risk: float = .005
    fee: float = .0006
    slip: float = .0003


def new_position(plan, opening, equity, costs, minimum_rr):
    direction = plan["side"]
    entry, risk, reward = entry_terms(direction, opening, plan["stop"], plan["target1"],
                                      costs.fee, costs.slip)
    if not (direction * (entry - plan["stop"]) > 0 and risk > 0 and reward >= risk * minimum_rr):
        return None
    units = min(equity / (entry * (1 + costs.fee)), equity * costs.risk / risk)
    if not math.isfinite(units) or units <= 0:
        return None
    return {**plan, "entry": entry, "units": units, "partial": False, "realized": 0.0}


def process_bar(position, bar, costs):
    """Process stop first, including on the entry bar, then target legs."""
    direction = position["side"]
    adverse = bar.low if direction == 1 else bar.high
    favorable = bar.high if direction == 1 else bar.low
    stop = position["stop"]
    if direction * (adverse - stop) <= 0:
        raw = min(bar.open, stop) if direction == 1 else max(bar.open, stop)
        return [_close(position, raw, position["units"], bar.time, "止损", costs)], None

    fills = []
    if not position["partial"] and direction * (favorable - position["target1"]) >= 0:
        units = position["units"] / 2
        fills.append(_close(position, position["target1"], units, bar.time, "止盈一", costs))
        position = {**position, "partial": True, "units": units,
                    "realized": position["realized"] + fills[-1]["pnl"]}
    if position["partial"] and direction * (favorable - position["target2"]) >= 0:
        fills.append(_close(position, position["target2"], position["units"], bar.time, "止盈二", costs))
        position = None
    return fills, position


def _close(position, price, units, when, reason, costs):
    side, entry = position["side"], position["entry"]
    exit_price = price * (1 - side * costs.slip)
    pnl = units * (side * (exit_price - entry) - costs.fee * (entry + exit_price))
    return dict(time=when, side=side, entry=entry, exit=exit_price, units=units, pnl=pnl, reason=reason)


def mark(position, close, equity, costs):
    if position is None:
        return equity
    return equity + _close(position, close, position["units"], None, "", costs)["pnl"]


def simulate(scored, costs=Costs(), minimum_rr=1.5, detail=False):
    equity, position, pending = costs.capital, None, None
    curve, trades, outcomes, holds = [], [], [], []
    cancelled = 0
    official = int(((pd.to_numeric(scored.side, errors="coerce") == 1) | (pd.to_numeric(scored.side, errors="coerce") == -1)).sum()) if len(scored) else 0
    for bar in scored.itertuples(index=False):
        if position is None and pending is not None:
            opened = new_position(pending, bar.open, equity, costs, minimum_rr)
            if opened is None:
                cancelled += 1
            else:
                opened["entry_time"] = bar.time
            position = opened
        if position is not None:
            prior = position["realized"]
            fills, leftover = process_bar(position, bar, costs)
            net = sum(fill["pnl"] for fill in fills)
            equity += net
            for fill in fills:
                fill["entry_time"] = position["entry_time"]
            trades.extend(fills)
            position = leftover
            if position is None:
                outcomes.append(prior + net)
                holds.append(bar.time - fills[-1]["entry_time"] if fills else pd.Timedelta(0))
        curve.append({"time": bar.time, "equity": mark(position, bar.close, equity, costs),
                      "units": 0.0 if position is None else position["units"] * position["side"]})
        pending = None
        side = bar.side
        if side is None or not np.isfinite(side):
            side = 0
        else:
            side = int(side)
        if position is None and side:
            suffix = "long" if side == 1 else "short"
            pending = {"side": side, "stop": getattr(bar, f"stop_{suffix}"),
                       "target1": getattr(bar, f"target1_{suffix}"), "target2": getattr(bar, f"target2_{suffix}")}
    curve = pd.DataFrame(curve)
    if curve.empty:
        statistics = {"净收益 USDT": 0.0, "最大回撤 %": 0.0, "交易次数": 0, "胜率 %": None, "平均盈亏比": None}
        extra = {"官方信号数": official, "取消次数": cancelled, "期望收益 USDT": None,
                 "假信号比例 %": None, "平均持仓": None, "outcomes": outcomes}
        trades = pd.DataFrame(trades, columns=TRADE_COLUMNS + (["entry_time"] if detail else []))
        return (statistics, curve, trades, extra) if detail else (statistics, curve, pd.DataFrame(trades, columns=TRADE_COLUMNS))
    drawdown = curve.equity / curve.equity.cummax().clip(lower=costs.capital) - 1
    wins = [value for value in outcomes if value > 0]
    losses = [value for value in outcomes if value < 0]
    statistics = {"净收益 USDT": float(curve.equity.iloc[-1] - costs.capital),
                  "最大回撤 %": float(drawdown.min() * 100),
                  "交易次数": len(outcomes),
                  "胜率 %": 100 * len(wins) / len(outcomes) if outcomes else None,
                  "平均盈亏比": (sum(wins) / len(wins)) / (-sum(losses) / len(losses)) if wins and losses else None}
    if not detail:
        return statistics, curve, pd.DataFrame(trades, columns=TRADE_COLUMNS)
    attempted = cancelled + len(outcomes)
    false_ratio = 100 * (cancelled + len(losses)) / attempted if attempted else None
    avg_hold = (sum(holds, pd.Timedelta(0)) / len(holds)) if holds else None
    extra = {"官方信号数": official, "取消次数": cancelled,
             "期望收益 USDT": (sum(outcomes) / len(outcomes)) if outcomes else None,
             "假信号比例 %": false_ratio,
             "平均持仓": avg_hold, "outcomes": outcomes, "holds": holds}
    columns = TRADE_COLUMNS + ["entry_time"]
    table = pd.DataFrame(trades)
    if not table.empty:
        table = table.reindex(columns=[col for col in columns if col in table.columns])
    return statistics, curve, table, extra


def evaluate(base, costs, rules, optimize=False):
    split = int(len(base) * .7)
    training, test = base.iloc[:split], base.iloc[split:]
    candidates = (55, 60, 65, 70, 75, 80) if optimize else (rules.threshold,)
    runs = {}
    for threshold in candidates:
        selected = Rules(threshold, rules.strong, rules.minimum_rr)
        runs[threshold] = simulate(score_frames(training, selected, costs.fee, costs.slip), costs, rules.minimum_rr)
    threshold = max(candidates, key=lambda t: (runs[t][0]["净收益 USDT"], -abs(t - rules.threshold)))
    selected = Rules(threshold, rules.strong, rules.minimum_rr)
    return threshold, runs[threshold], simulate(score_frames(test, selected, costs.fee, costs.slip), costs, rules.minimum_rr)
