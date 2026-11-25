"""Bitget public perpetual structure: funding, open interest, trades, and top of book.

These are market records, not a trader's positions. Private copy-trading endpoints
are intentionally not called.
"""
import re

from market_data import request_data

ROOT = "https://api.bitget.com/api/v2/mix/market"
DOCS = "https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market"
SYMBOL = re.compile(r"[A-Z0-9]{2,24}")
COPY_REQUIREMENT = (
    "Bitget 跟单查询在本次探测中返回 Invalid ACCESS_KEY。"
    "需要所有者自己的只读密钥、口令和签名，且密钥不能写入仓库。本版本不发送签名请求。"
)


def normalize_symbol(symbol):
    text = (symbol or "").strip().upper()
    if not SYMBOL.fullmatch(text):
        raise ValueError("合约代码只允许大写字母和数字")
    return text


def last_price(symbol, requester=request_data):
    symbol = normalize_symbol(symbol)
    ticker = _one(requester(f"{ROOT}/ticker", {"symbol": symbol, "productType": "USDT-FUTURES"}))
    return _float(ticker.get("lastPr")), _int(ticker.get("ts"))


def load_structure(symbol, requester=request_data):
    symbol = normalize_symbol(symbol)
    params = {"symbol": symbol, "productType": "USDT-FUTURES"}
    ticker = _one(requester(f"{ROOT}/ticker", params))
    funding = requester(f"{ROOT}/history-fund-rate", {**params, "pageSize": "20"})
    trades = requester(f"{ROOT}/fills", {**params, "limit": "20"})
    depth = requester(f"{ROOT}/merge-depth", {**params, "precision": "scale0", "limit": "5"})
    if not isinstance(funding, list) or not isinstance(trades, list) or not isinstance(depth, dict):
        raise ValueError("Bitget 市场结构响应格式异常")
    return {
        "symbol": symbol,
        "docs": DOCS,
        "grade": None,
        "market_note": "Bitget 公开市场数据，不是交易员持仓。",
        "ticker": {
            "price": _float(ticker.get("lastPr")),
            "mark": _float(ticker.get("markPrice")),
            "index": _float(ticker.get("indexPrice")),
            "funding_rate": _float(ticker.get("fundingRate")),
            "open_interest": _float(ticker.get("holdingAmount")),
            "turnover": _float(ticker.get("usdtVolume")),
            "base_volume": _float(ticker.get("baseVolume")),
            "exchange_time_ms": _int(ticker.get("ts")),
        },
        "funding_history": [
            {"funding_rate": _float(row.get("fundingRate")), "funding_time_ms": _int(row.get("fundingTime"))}
            for row in funding if isinstance(row, dict)
        ],
        "trades": [
            {"price": _float(row.get("price")), "size": _float(row.get("size")), "side": row.get("side"),
             "trade_id": row.get("tradeId"), "exchange_time_ms": _int(row.get("ts"))}
            for row in trades if isinstance(row, dict)
        ],
        "book": {
            "exchange_time_ms": _int(depth.get("ts")),
            "bid": _level((depth.get("bids") or [None])[0]),
            "ask": _level((depth.get("asks") or [None])[0]),
            "note": "只保留合并深度的最优买卖档，不是完整订单流重建。",
        },
    }


def _one(payload):
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        raise ValueError("Bitget ticker 响应异常")
    return payload


def _level(value):
    if isinstance(value, (list, tuple)) and value:
        return _float(value[0])
    if isinstance(value, dict):
        return _float(value.get("price") or value.get("prc"))
    return None


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
