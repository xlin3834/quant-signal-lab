"""Public Bitget USDT perpetual contract directory and snapshot rankings."""
import math
import re
import time

from market_data import MarketDataError, request_data

ROOT = "https://api.bitget.com/api/v2/mix/market"
PARAMS = {"productType": "USDT-FUTURES"}
MAX_QUOTE_AGE = 90_000
SYMBOL = re.compile(r"[A-Z0-9._-]{1,32}")
VIEWS = ("全部合约", "成交额排行", "涨幅榜", "跌幅榜", "波动率排行", "我的自选")


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def timestamp(value):
    try:
        stamp = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return stamp if 0 < stamp < 10**14 else None


def is_active(spec):
    return (spec.get("symbolType") == "perpetual" and spec.get("symbolStatus") == "normal"
            and spec.get("quoteCoin") == "USDT" and "USDT" in (spec.get("supportMarginCoins") or ()))


def load_directory():
    specs = request_data(f"{ROOT}/contracts", PARAMS)
    if not isinstance(specs, list) or not specs or not all(isinstance(row, dict) for row in specs):
        raise MarketDataError("Bitget 返回的合约列表格式异常")
    return {row["symbol"]: row for row in specs
            if SYMBOL.fullmatch(str(row.get("symbol", ""))) and is_active(row)}


def load_tickers():
    ticks = request_data(f"{ROOT}/tickers", PARAMS)
    if not isinstance(ticks, list) or not all(isinstance(row, dict) for row in ticks):
        raise MarketDataError("Bitget 返回的永续行情格式异常")
    return ticks, int(time.time() * 1000)


def build_rows(directory, tickers):
    lookup = {}
    for tick in tickers:
        symbol, stamp = tick.get("symbol"), timestamp(tick.get("ts"))
        if symbol in directory and stamp and stamp > lookup.get(symbol, (0, {}))[0]:
            lookup[symbol] = (stamp, tick)
    rows = []
    for symbol, spec in directory.items():
        stamp, tick = lookup.get(symbol, (None, {}))
        price, turnover, change, funding, high, low, bid, ask = (
            number(tick.get(field)) for field in (
                "lastPr", "usdtVolume", "change24h", "fundingRate", "high24h", "low24h", "bidPr", "askPr")
        )
        rows.append(dict(
            symbol=symbol, base=spec.get("baseCoin"), spec=spec, timestamp=stamp,
            price=price if price is not None and price > 0 else None,
            turnover=turnover if turnover is not None and turnover >= 0 else None,
            change=None if change is None else change * 100,
            funding=None if funding is None else funding * 100,
            range_pct=100 * (high / low - 1) if high is not None and low is not None and high >= low > 0 else None,
            spread_pct=200 * (ask - bid) / (ask + bid) if ask is not None and bid is not None and ask >= bid > 0 else None,
        ))
    return rows


def quote_state(row, now):
    if row["timestamp"] is None or row["price"] is None:
        return "缺失"
    age = now - row["timestamp"]
    if age < -2000:
        return "时钟异常"
    return "正常" if age <= MAX_QUOTE_AGE else "过期"


def visible_rows(rows, query, view, favorites, now, min_turnover=0, max_spread=0):
    query = query.strip().upper()
    result = []
    ranks = {"成交额排行": "turnover", "涨幅榜": "change", "跌幅榜": "change", "波动率排行": "range_pct"}
    ranking = ranks.get(view)
    for row in rows:
        if query not in row["symbol"] and query not in (row["base"] or ""):
            continue
        if view == "我的自选" and row["symbol"] not in favorites:
            continue
        state = quote_state(row, now)
        if ranking and (state != "正常" or row[ranking] is None):
            continue
        if min_turnover and (state != "正常" or row["turnover"] is None or row["turnover"] < min_turnover):
            continue
        if max_spread and (state != "正常" or row["spread_pct"] is None or row["spread_pct"] > max_spread):
            continue
        result.append({**row, "state": state})
    if ranking == "change":
        sign = 1 if view == "涨幅榜" else -1
        return sorted((row for row in result if sign * row["change"] > 0),
                      key=lambda row: (-sign * row["change"], row["symbol"]))
    if ranking:
        return sorted(result, key=lambda row: (-row[ranking], row["symbol"]))
    return sorted(result, key=lambda row: (
        row["state"] != "正常", -(row["turnover"] or 0), row["symbol"]))


def read_favorites(value):
    return tuple(dict.fromkeys(symbol for symbol in value.split(",") if SYMBOL.fullmatch(symbol)))[:50]
