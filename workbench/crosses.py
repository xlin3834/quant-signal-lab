"""Closed-candle EMA × Bollinger-mid crosses.

Bollinger mid is the 18-bar simple moving average of close. Bands are mid ± 2
population standard deviations. A cross is confirmed only on a closed bar and
uses that bar and the previous closed bar, so it does not repaint.
EMA120 crosses are directional reports, not instructions to open a position.
"""
from __future__ import annotations

import math

PERIOD = 18
MULT = 2.0
EMAS = (7, 10, 120)
ACTIVE_ALERTS = (
    "EMA7上穿布林中轨",
    "EMA7下穿布林中轨",
    "EMA10上穿布林中轨",
    "EMA10下穿布林中轨",
    "EMA120上穿布林中轨",
    "EMA120下穿布林中轨",
)
ARCHIVED_ALERTS = (
    "EMA9×EMA20金叉死叉",
    "80分候选",
    "趋势切换",
    "RSI背驰",
    "支撑压力触达",
)


def _ema(values, length):
    if length <= 0:
        raise ValueError("EMA 长度无效")
    alpha = 2 / (length + 1)
    out = [None] * len(values)
    if len(values) < length:
        return out
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    prev = seed
    for index in range(length, len(values)):
        prev = alpha * values[index] + (1 - alpha) * prev
        out[index] = prev
    return out


def _sma_std(values, period):
    mid = [None] * len(values)
    upper = [None] * len(values)
    lower = [None] * len(values)
    if period <= 1:
        raise ValueError("布林周期无效")
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        average = sum(window) / period
        var = sum((item - average) ** 2 for item in window) / period
        spread = math.sqrt(var) * MULT
        mid[index] = average
        upper[index] = average + spread
        lower[index] = average - spread
    return mid, upper, lower


def attach_indicators(closes):
    """Return parallel series. None means not warmed up."""
    closes = [float(item) for item in closes]
    mid, upper, lower = _sma_std(closes, PERIOD)
    return {
        "ema7": _ema(closes, 7),
        "ema10": _ema(closes, 10),
        "ema120": _ema(closes, 120),
        "boll_mid": mid,
        "boll_up": upper,
        "boll_low": lower,
    }


def _relation(ema, mid):
    if ema is None or mid is None:
        return "数据不足"
    if ema > mid:
        return "之上"
    if ema < mid:
        return "之下"
    return "持平"


def relation_snapshot(closes):
    series = attach_indicators(closes)
    if not closes:
        return {"ready": False, "reason": "没有收盘价"}
    last = len(closes) - 1
    ready = series["boll_mid"][last] is not None and series["ema120"][last] is not None
    return {
        "ready": ready,
        "reason": None if ready else "已收盘 K 线不足以完成 EMA120 与 BOLL18",
        "ema7": series["ema7"][last],
        "ema10": series["ema10"][last],
        "ema120": series["ema120"][last],
        "boll_mid": series["boll_mid"][last],
        "boll_up": series["boll_up"][last],
        "boll_low": series["boll_low"][last],
        "ema7_vs_mid": _relation(series["ema7"][last], series["boll_mid"][last]),
        "ema10_vs_mid": _relation(series["ema10"][last], series["boll_mid"][last]),
        "ema120_vs_mid": _relation(series["ema120"][last], series["boll_mid"][last]),
    }


def confirmed_crosses(bars):
    """bars are closed candles in time order: dicts with t and c.

    The forming candle must not be included. Confirmed events only reference
    indexes that stay the same if later bars are appended.
    """
    closes = [float(bar["c"]) for bar in bars]
    times = [bar.get("t") for bar in bars]
    series = attach_indicators(closes)
    events = []
    for index in range(1, len(closes)):
        mid_prev, mid_cur = series["boll_mid"][index - 1], series["boll_mid"][index]
        if mid_prev is None or mid_cur is None:
            continue
        for length, key in ((7, "ema7"), (10, "ema10"), (120, "ema120")):
            prev, cur = series[key][index - 1], series[key][index]
            if prev is None or cur is None:
                continue
            direction = None
            if prev <= mid_prev and cur > mid_cur:
                direction = "上穿"
            elif prev >= mid_prev and cur < mid_cur:
                direction = "下穿"
            if direction is None:
                continue
            label = f"EMA{length}{direction}布林中轨"
            instructional = length != 120
            events.append({
                "label": label,
                "ema": length,
                "direction": direction,
                "time": times[index],
                "index": index,
                "price": closes[index],
                "ema_value": cur,
                "boll_mid": mid_cur,
                "confirmed": True,
                "instruction": None if not instructional else None,
                "note": "只报告穿越方向，不是开仓指令。" if length == 120 else "收盘确认的指标事件，不是开仓指令。",
            })
    return events


def forward_path(bars, index, horizon):
    """Price path after a confirmed cross. This is not a win rate."""
    if index is None or index + horizon >= len(bars):
        return {"available": False, "horizon_bars": horizon, "reason": "后续已收盘K线不足", "label": "价格变化，不是胜率"}
    entry = float(bars[index]["c"])
    if entry == 0:
        return {"available": False, "horizon_bars": horizon, "reason": "收盘价为 0", "label": "价格变化，不是胜率"}
    segment = bars[index + 1:index + horizon + 1]
    closes = [float(item["c"]) for item in segment]
    highs = [float(item.get("h", item["c"])) for item in segment]
    lows = [float(item.get("l", item["c"])) for item in segment]
    return {
        "available": True,
        "horizon_bars": horizon,
        "close_change_pct": (closes[-1] - entry) / entry * 100,
        "mfe_pct": (max(highs) - entry) / entry * 100,
        "mae_pct": (min(lows) - entry) / entry * 100,
        "label": "价格变化，不是胜率",
    }


def latest_cross(events):
    if not events:
        return {"type": "暂无交叉", "time": None, "ema": None, "direction": None}
    last = events[-1]
    return {"type": last["label"], "time": last["time"], "ema": last["ema"], "direction": last["direction"]}


def event_key(event, exchange="Bitget", symbol="", interval=""):
    return "|".join([
        exchange, symbol, interval, str(event.get("time")), f"EMA{event.get('ema')}", event.get("direction") or "",
    ])


def new_events(events, seen):
    """One confirmed event per exchange/symbol/timeframe/candle/EMA/direction."""
    fresh = []
    seen = set(seen or [])
    for event in events:
        key = event.get("key") or event_key(event)
        if key in seen:
            continue
        seen.add(key)
        fresh.append({**event, "key": key})
    return fresh, seen


def baseline_seen(events):
    """Record history without sending it."""
    return {event_key(event) if "key" not in event else event["key"] for event in events}


def rank_by_turnover(rows, limit=20):
    """Rank one quote-currency book. Refuse mixed turnover units."""
    usable = []
    blocked = []
    units = set()
    for row in rows:
        unit = row.get("turnover_unit") or row.get("quote")
        if row.get("turnover") is None or not unit:
            blocked.append({**row, "rank": None, "rank_reason": "无可靠成交额，不参与排名"})
            continue
        units.add(unit)
        usable.append(row)
    if len(units) > 1:
        return [], [{"rank": None, "rank_reason": "成交额单位不同，不能混排", "units": sorted(units)}] + blocked
    usable.sort(key=lambda row: (-float(row["turnover"]), row.get("symbol") or ""))
    ranked = []
    for index, row in enumerate(usable[:limit], start=1):
        ranked.append({**row, "rank": index, "rank_reason": None})
    return ranked, blocked
