"""Public Bitget USDT-margined perpetual candles. Never mixed with spot data."""
from market_data import INTERVALS, MarketDataError, closed_candles, parse_candles, request_data

ROOT = "https://api.bitget.com/api/v2/mix/market"
PRODUCT = "USDT-FUTURES"
PRODUCT_LABEL = "Bitget USDT 本位永续"
CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
MIX_GRANULARITY = {"5m": "5m", "15m": "15m", "1H": "1H", "4H": "4H", "1D": "1D"}


def fetch_swap_candles(symbol, interval, limit=200):
    if interval not in MIX_GRANULARITY:
        raise MarketDataError(f"不支持的永续周期 {interval}")
    return parse_candles(request_data(f"{ROOT}/candles", {
        "symbol": symbol, "productType": PRODUCT,
        "granularity": MIX_GRANULARITY[interval], "limit": str(limit),
    }))


def fetch_swap_ticker(symbol):
    rows = request_data(f"{ROOT}/ticker", {"symbol": symbol, "productType": PRODUCT})
    row = rows[0] if isinstance(rows, list) and rows else rows
    if not isinstance(row, dict) or (row.get("symbol") or row.get("instId")) != symbol:
        raise MarketDataError("Bitget 未返回该 USDT 永续合约")
    return row


def fetch_swap_history(symbol, interval, count, now=None):
    import pandas as pd

    granularity, duration = MIX_GRANULARITY[interval], INTERVALS[interval][1]
    raw = fetch_swap_candles(symbol, interval, 400)
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    latest = closed_candles(raw, interval, now)
    pages = [latest]
    remaining = count - len(latest)
    oldest = latest.time.iloc[0]
    while remaining > 0:
        limit = min(remaining + 1, 200)
        rows = request_data(f"{ROOT}/history-candles", {
            "symbol": symbol, "productType": PRODUCT, "granularity": granularity,
            "limit": str(limit), "endTime": str(oldest.value // 1_000_000),
        })
        page = parse_candles(rows)
        page = page[page.time < oldest]
        if page.empty or page.time.iloc[-1] != oldest - duration:
            raise MarketDataError(f"{interval} 永续历史 K 线缺失或分页未前进")
        pages.append(page)
        oldest = page.time.iloc[0]
        remaining -= len(page)
    data = pd.concat(pages[::-1], ignore_index=True).tail(count).reset_index(drop=True)
    return closed_candles(data, interval, now)


def load_swap_frames(symbol, counts=None):
    import pandas as pd

    intervals = ("15m", "4H", "1H", "5m")
    if counts is not None:
        return {interval: fetch_swap_history(symbol, interval, counts[interval]) for interval in intervals}
    raw = {interval: fetch_swap_candles(symbol, interval, 1000 if interval == "5m" else 950)
           for interval in intervals}
    now = pd.Timestamp.now(tz="UTC")
    return {interval: closed_candles(data, interval, now) for interval, data in raw.items()}
