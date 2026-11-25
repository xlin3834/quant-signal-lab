"""Hyperliquid public info reads. No exchange-action or order signing."""
import re

from evidence import SourceError
from research_http import request_json

INFO_URL = "https://api.hyperliquid.xyz/info"
DOCS = "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
FILL_LIMIT_NOTE = "胜率只统计本次返回的 fills 里 closedPnl 非零的条数，官方单次最多 2000 条，不是完整历史。"


def extract_address(text):
    found = list(dict.fromkeys(ADDRESS.findall(text or "")))
    if not found:
        raise SourceError("没有找到 42 位十六进制地址。官方 info 不能按昵称查询。")
    if len(found) > 1:
        raise SourceError("文本里有多个地址，请只保留一个。")
    return found[0]


def fetch_account(address, *, transport=None, received_ms):
    address = extract_address(address)
    state, _ = _info({"type": "clearinghouseState", "user": address}, transport)
    mids, _ = _info({"type": "allMids"}, transport)
    fills, _ = _info({"type": "userFills", "user": address}, transport)
    portfolio, _ = _info({"type": "portfolio", "user": address}, transport)
    if not isinstance(state, dict) or "assetPositions" not in state:
        message = state.get("error") if isinstance(state, dict) else "clearinghouseState 响应异常"
        raise SourceError(str(message or "clearinghouseState 响应异常"))
    positions = parse_positions(state, mids if isinstance(mids, dict) else {})
    curve, drawdown = parse_portfolio(portfolio)
    return {
        "address": address,
        "grade": "B",
        "docs": DOCS,
        "exchange_time_ms": _int(state.get("time")),
        "received_time_ms": received_ms,
        "account_value": _float((state.get("marginSummary") or {}).get("accountValue")),
        "positions": positions,
        "equity_curve": curve,
        "max_drawdown": drawdown,
        "win_rate": win_rate_from_fills(fills if isinstance(fills, list) else []),
        "fills_returned": len(fills) if isinstance(fills, list) else 0,
    }


def parse_positions(state, mids):
    rows = []
    for item in state.get("assetPositions") or []:
        position = (item or {}).get("position") or {}
        size = _float(position.get("szi"))
        if size is None or size == 0:
            continue
        coin = str(position.get("coin") or "")
        leverage = position.get("leverage") or {}
        rows.append({
            "symbol": coin,
            "side": "long" if size > 0 else "short",
            "size": abs(size),
            "entry": _float(position.get("entryPx")),
            "mid": _float(mids.get(coin)) if isinstance(mids, dict) else None,
            "leverage": leverage.get("value") if isinstance(leverage, dict) else None,
            "position_value": _float(position.get("positionValue")),
            "unrealized_pnl": _float(position.get("unrealizedPnl")),
            "liquidation": _float(position.get("liquidationPx")),
            "open_time": None,
            "open_time_note": "clearinghouseState 不提供开仓时间。",
            "grade": "B",
        })
    return rows


def win_rate_from_fills(fills):
    decided = []
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        pnl = _float(fill.get("closedPnl"))
        if pnl is None or pnl == 0:
            continue
        decided.append(pnl)
    wins = sum(value > 0 for value in decided)
    return {
        "wins": wins,
        "losses": len(decided) - wins,
        "samples": len(decided),
        "rate": None if not decided else wins / len(decided),
        "note": FILL_LIMIT_NOTE,
    }


def parse_portfolio(payload):
    windows = {}
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict):
                windows[str(item[0])] = item[1]
    chosen = next((name for name in ("allTime", "month", "week", "day") if name in windows), None)
    if chosen is None:
        return [], {"window": None, "value": None, "points": 0,
                    "note": "portfolio 响应里没有可用的账户价值序列。"}
    history = windows[chosen].get("accountValueHistory") or []
    curve = []
    values = []
    for point in history:
        if isinstance(point, list) and len(point) >= 2:
            stamp, value = _int(point[0]), _float(point[1])
            if stamp is not None and value is not None:
                curve.append({"time_ms": stamp, "value": value})
                values.append(value)
    return curve, {
        "window": chosen,
        "value": _max_drawdown(values),
        "points": len(values),
        "note": "由 Hyperliquid portfolio 的 accountValueHistory 计算，不是交易所单独给出的回撤字段。",
    }


def position_changes(previous, current, pnl_abs):
    if previous is None:
        return ["没有更早的快照，不能判断持仓变化。"]
    old = {_key(row): row for row in previous}
    new = {_key(row): row for row in current}
    alerts = []
    for key, row in new.items():
        if key not in old:
            alerts.append(f"公开持仓出现 {row['symbol']} {row['side']}")
            continue
        before = old[key].get("unrealized_pnl")
        after = row.get("unrealized_pnl")
        if pnl_abs and before is not None and after is not None and abs(after - before) >= pnl_abs:
            alerts.append(f"未实现盈亏变化 {row['symbol']} {row['side']} {before:g} -> {after:g}")
    for key, row in old.items():
        if key not in new:
            alerts.append(f"公开持仓消失 {row['symbol']} {row['side']}")
    return alerts


def _info(body, transport):
    payload, status = request_json("POST", INFO_URL, body=body, transport=transport)
    if isinstance(payload, dict) and payload.get("error") and "assetPositions" not in payload:
        raise SourceError(str(payload.get("error")), status)
    return payload, status


def _key(row):
    return f"{row.get('symbol')}:{row.get('side')}"


def _max_drawdown(values):
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def _float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
