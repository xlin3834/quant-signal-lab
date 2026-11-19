"""Public Bitget spot candles for research and backtesting."""

import logging
import time
from threading import Lock
from time import sleep as _rate_sleep

import numpy as np
import pandas as pd
import requests

URL = "https://api.bitget.com/api/v2/spot/market/candles"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
INTERVALS = {
    "5m": ("5min", pd.Timedelta(minutes=5)),
    "15m": ("15min", pd.Timedelta(minutes=15)),
    "1H": ("1h", pd.Timedelta(hours=1)),
    "4H": ("4h", pd.Timedelta(hours=4)),
    "1D": ("1day", pd.Timedelta(days=1)),
}
COLUMNS = ["ts", "open", "high", "low", "close", "volume", "quote_volume"]
MAX_ATTEMPTS = 3
LOGGER = logging.getLogger(__name__)
_RATE_LOCK = Lock()
_next_request = 0.0


class MarketDataError(ValueError):
    """An exchange or market data failure that can be shown in the app."""


def fetch_candles(symbol, interval, limit=200):
    granularity, _ = INTERVALS[interval]
    return parse_candles(request_data(URL, {
        "symbol": symbol, "granularity": granularity, "limit": str(limit),
    }))


def request_data(url, params):
    global _next_request
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with _RATE_LOCK:
                now = time.monotonic()
                slot = max(now, _next_request)
                _next_request = slot + 0.15
            if slot > now:
                _rate_sleep(slot - now)
            response = requests.get(url, params=params, timeout=(3, 8))
        except requests.RequestException as exc:
            retry = isinstance(exc, (requests.Timeout, requests.ConnectionError))
            error = f"连接 Bitget 失败（{type(exc).__name__}）"
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            status = response.status_code
            code = payload.get("code") if isinstance(payload, dict) else None
            if status == 200 and code == "00000":
                return payload.get("data")
            retry = status in (408, 429) or 500 <= status < 600 or (
                status == 200 and code in ("40010", "429", "45001")
            )
            error = f"Bitget 接口异常（HTTP {status}"
            if code:
                error += f"，错误码 {code}"
            error += "）"
            if isinstance(payload, dict):
                LOGGER.info("Bitget error message: %r", str(payload.get("msg", ""))[:300])

        LOGGER.warning("%s %s attempt %d/%d: %s", params.get("symbol", "all"), params.get("granularity", "ticker"), attempt, MAX_ATTEMPTS, error)
        if not retry or attempt == MAX_ATTEMPTS:
            raise MarketDataError(error)
        time.sleep(2 ** (attempt - 1))


def parse_candles(rows):
    if not isinstance(rows, list) or not rows:
        raise MarketDataError("Bitget 未返回 K 线数据")
    if any(not isinstance(row, list) or len(row) not in (7, 8) for row in rows):
        raise MarketDataError("Bitget K 线格式异常")

    # The optional eighth field is quote-currency volume. Older rows only have seven fields.
    df = pd.DataFrame([row[:6] + [row[-1]] for row in rows], columns=COLUMNS)
    try:
        df["time"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        for column in COLUMNS[1:]:
            df[column] = pd.to_numeric(df[column])
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError("Bitget K 线时间戳或数值无效") from exc
    if df["time"].isna().any() or not np.isfinite(df[COLUMNS[1:]].to_numpy()).all():
        raise MarketDataError("Bitget K 线包含缺失时间或无效数值")
    bounds = df[["open", "close"]]
    if ((df.low <= 0) | (df.low > bounds.min(axis=1)) | (df.high < bounds.max(axis=1))
            | (df.volume < 0) | (df.quote_volume < 0)).any():
        raise MarketDataError("Bitget K 线价格或成交量超出合理范围")
    return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def closed_candles(data, interval, now=None):
    """Check freshness on every use, including Streamlit cache hits."""
    _, duration = INTERVALS[interval]
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    if data["time"].iloc[-1] > now:
        raise MarketDataError("Bitget K 线时间戳晚于当前时间")

    closed = data.loc[data["time"] + duration <= now]
    if closed.empty or now - (closed["time"].iloc[-1] + duration) >= duration + pd.Timedelta(seconds=60):
        raise MarketDataError("Bitget 行情已过期，已暂停计算指标和模拟信号")
    if len(closed) < 50:
        raise MarketDataError("已收盘 K 线不足 50 根，无法计算所需指标")
    if not closed["time"].diff().iloc[1:].eq(duration).all():
        raise MarketDataError("Bitget K 线时间戳不连续，已暂停生成模拟信号")
    return closed


def fetch_tickers():
    rows = request_data(URL.replace("/candles", "/tickers"), {})
    if not isinstance(rows, list):
        raise MarketDataError("Bitget 行情列表格式异常")
    try:
        table = pd.DataFrame([
            {"交易对": row["symbol"], "最新价": float(row["lastPr"]),
             "24h 涨跌 %": float(row["change24h"]) * 100,
             "24h 成交额 USDT": float(row["usdtVolume"]),
             "时间 UTC": pd.to_datetime(int(row["ts"]), unit="ms", utc=True)}
            for row in rows if row["symbol"] in SYMBOLS
        ])
    except (TypeError, KeyError, ValueError, OverflowError) as exc:
        raise MarketDataError("Bitget 行情列表字段异常") from exc
    if len(table) != len(SYMBOLS):
        raise MarketDataError("Bitget 行情列表缺少交易对")
    return table


def fetch_history(symbol, interval, count, now=None):
    """Download an exact, contiguous window using the spot history endpoint."""
    granularity, duration = INTERVALS[interval]
    raw = fetch_candles(symbol, interval, 400)
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    latest = closed_candles(raw, interval, now)
    pages = [latest]
    remaining = count - len(latest)
    oldest = latest.time.iloc[0]
    while remaining > 0:
        limit = min(remaining + 1, 200)
        rows = request_data(URL.replace("/candles", "/history-candles"), {
            "symbol": symbol, "granularity": granularity, "limit": str(limit),
            "endTime": str(oldest.value // 1_000_000),
        })
        page = parse_candles(rows)
        page = page[page.time < oldest]
        if page.empty or page.time.iloc[-1] != oldest - duration:
            raise MarketDataError(f"{interval} 历史 K 线缺失或分页未前进")
        pages.append(page)
        oldest = page.time.iloc[0]
        remaining -= len(page)
    data = pd.concat(pages[::-1], ignore_index=True).tail(count).reset_index(drop=True)
    return closed_candles(data, interval, now)


def load_frames(symbol, counts=None):
    intervals = ("15m", "4H", "1H", "5m")
    # Use one observation time, but validate after the requests so boundary rollovers
    # do not make an exchange candle appear to open in the future.
    if counts is not None:
        return {interval: fetch_history(symbol, interval, counts[interval]) for interval in intervals}
    raw = {interval: fetch_candles(symbol, interval, 1000 if interval == "5m" else 950)
           for interval in intervals}
    now = pd.Timestamp.now(tz="UTC")
    return {interval: closed_candles(data, interval, now) for interval, data in raw.items()}
