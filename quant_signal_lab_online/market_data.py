"""Public Bitget spot candles for research and backtesting."""

import logging
import time

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


class MarketDataError(ValueError):
    """An exchange or market data failure that can be shown in the app."""


def fetch_candles(symbol, interval):
    granularity, _ = INTERVALS[interval]
    params = {"symbol": symbol, "granularity": granularity, "limit": "200"}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(URL, params=params, timeout=(3, 8))
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
                try:
                    return parse_candles(payload.get("data"))
                except MarketDataError:
                    LOGGER.exception("Invalid Bitget candles for %s %s", symbol, granularity)
                    raise
            retry = status in (408, 429) or 500 <= status < 600 or (
                status == 200 and code in ("40010", "429", "45001")
            )
            error = f"Bitget 接口异常（HTTP {status}"
            if code:
                error += f"，错误码 {code}"
            error += "）"
            if isinstance(payload, dict):
                LOGGER.info("Bitget error message: %r", str(payload.get("msg", ""))[:300])

        LOGGER.warning("%s %s attempt %d/%d: %s", symbol, granularity, attempt, MAX_ATTEMPTS, error)
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
