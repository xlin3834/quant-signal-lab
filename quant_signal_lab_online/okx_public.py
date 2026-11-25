"""OKX public copy-trading reads. No private account or order endpoints."""
from evidence import SourceError
from research_http import request_json

BASE = "https://www.okx.com/api/v5/copytrading"
DOCS = "https://www.okx.com/docs-v5/en/"
DRAWDOWN_NOTE = "本次读到的公开榜字段里没有最大回撤。"
HISTORY_NOTE = "胜率只用本次返回的公开历史带单计算，官方说明该接口最多覆盖近 3 个月且单次有条数上限，不是账户全部成交。"


def fetch_ranks(page=1, limit=20, *, transport=None):
    payload = _get("/public-lead-traders", {"instType": "SWAP", "page": str(page), "limit": str(limit)}, transport)
    block = (payload.get("data") or [{}])[0]
    ranks = [normalize_rank(row) for row in block.get("ranks") or [] if isinstance(row, dict)]
    return {"data_ver": block.get("dataVer"), "total_page": block.get("totalPage"), "ranks": ranks, "docs": DOCS}


def fetch_positions(unique_code, *, transport=None):
    code = _code(unique_code)
    current = _get("/public-current-subpositions", {"instType": "SWAP", "uniqueCode": code}, transport)
    history = _get("/public-subpositions-history", {"instType": "SWAP", "uniqueCode": code, "limit": "100"}, transport)
    positions = [normalize_position(row) for row in current.get("data") or [] if isinstance(row, dict)]
    closed = [normalize_closed(row) for row in history.get("data") or [] if isinstance(row, dict)]
    return {"unique_code": code, "grade": "B", "docs": DOCS, "positions": positions,
            "closed": closed, "win_rate": win_rate(closed), "max_drawdown": None,
            "max_drawdown_note": DRAWDOWN_NOTE}


def normalize_rank(row):
    return {
        "nickname": row.get("nickName") or "",
        "unique_code": row.get("uniqueCode") or "",
        "win_ratio": _float(row.get("winRatio")),
        "pnl": _float(row.get("pnl")),
        "pnl_ratio": _float(row.get("pnlRatio")),
        "aum": _float(row.get("aum")),
        "lead_days": row.get("leadDays"),
        "portrait": row.get("portLink") or "",
        "grade": "B",
        "max_drawdown": None,
        "max_drawdown_note": DRAWDOWN_NOTE,
    }


def normalize_position(row):
    return {
        "symbol": row.get("instId") or "",
        "side": row.get("posSide") or "",
        "entry": _float(row.get("openAvgPx")),
        "mark": _float(row.get("markPx")),
        "leverage": _float(row.get("lever")),
        "size": _float(row.get("subPos")),
        "size_note": "subPos 按 OKX 返回原样保存。未把张数换算成名义额。",
        "unrealized_pnl": _float(row.get("upl")),
        "open_time_ms": _int(row.get("openTime")),
        "close_time_ms": None,
        "position_value": None,
        "grade": "B",
    }


def normalize_closed(row):
    return {
        "symbol": row.get("instId") or "",
        "side": row.get("posSide") or "",
        "entry": _float(row.get("openAvgPx")),
        "exit": _float(row.get("closeAvgPx")),
        "pnl": _float(row.get("pnl")),
        "open_time_ms": _int(row.get("openTime")),
        "close_time_ms": _int(row.get("closeTime")),
        "leverage": _float(row.get("lever")),
    }


def win_rate(closed):
    decided = [row for row in closed if row.get("pnl") not in (None, 0)]
    wins = sum(row["pnl"] > 0 for row in decided)
    return {"wins": wins, "losses": len(decided) - wins, "samples": len(decided),
            "rate": None if not decided else wins / len(decided), "note": HISTORY_NOTE}


def parse_data_ver(value):
    text = str(value or "")
    if len(text) != 14 or not text.isdigit():
        return None
    year, month, day = int(text[0:4]), int(text[4:6]), int(text[6:8])
    hour, minute, second = int(text[8:10]), int(text[10:12]), int(text[12:14])
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60):
        return None
    from datetime import datetime, timezone
    try:
        stamp = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(stamp.timestamp() * 1000)


def _get(path, params, transport):
    payload, status = request_json("GET", BASE + path, params=params, transport=transport)
    if not isinstance(payload, dict) or payload.get("code") != "0":
        code = payload.get("code") if isinstance(payload, dict) else "invalid"
        message = payload.get("msg") if isinstance(payload, dict) else "响应异常"
        raise SourceError(f"OKX {code} {message}".strip(), status)
    return payload


def _code(value):
    text = (value or "").strip()
    if not (len(text) in (16, 18) and text.isalnum()):
        raise SourceError("uniqueCode 需为 16 或 18 位字母数字。公开接口不能用钱包地址代替。")
    return text


def _float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
