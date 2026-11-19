"""Streamlit transport diagnostics without an in-page streaming worker."""
import logging
import os

import pandas as pd
import requests
import streamlit as st

from live_market import rest_quote
from market_data import MarketDataError

LOG = logging.getLogger(__name__)
SERVICE_URL = os.getenv("QSL_MARKET_URL", "").strip()


@st.cache_data(ttl=15, show_spinner=False, max_entries=10)
def cached_rest(symbol):
    return rest_quote(symbol)


def fresh(quote, now, max_age):
    return (isinstance(quote, dict) and isinstance(quote.get("timestamp"), int)
            and isinstance(quote.get("received"), int)
            and 0 <= now - quote["timestamp"] <= max_age
            and quote["timestamp"] <= quote["received"] <= now)


@st.fragment(run_every=5 if SERVICE_URL else 20)
def quote_panel(symbol):
    quote, metrics = None, {}
    origin = "REST 快照"
    service_state = "未配置独立 WebSocket 网关"
    if SERVICE_URL:
        try:
            response = requests.get(f"{SERVICE_URL.rstrip('/')}/ticker", params={"symbol": symbol}, timeout=(2, 3))
            response.raise_for_status()
            metrics = response.json()
            candidate = metrics.get("tick")
            service_state = ("WebSocket 已连接" if metrics.get("connected")
                             else "WebSocket 等待订阅或正在重连")
            now = pd.Timestamp.now(tz="UTC").value // 1_000_000
            if metrics.get("connected") and fresh(candidate, now, 15_000) and candidate.get("symbol") == symbol:
                quote, origin = candidate, "WebSocket"
        except (requests.RequestException, ValueError, AttributeError) as exc:
            service_state = "独立行情网关不可用"
            LOG.warning("Quote gateway failure: %s", type(exc).__name__)
    if quote is None:
        try:
            quote = cached_rest(symbol)
        except (MarketDataError, KeyError, ValueError) as exc:
            st.warning(f"Bitget 现货报价暂不可用：{exc}")
            return
    now = pd.Timestamp.now(tz="UTC").value // 1_000_000
    if not fresh(quote, now, 15_000 if origin == "WebSocket" else 35_000):
        st.warning(f"{service_state} · {origin} 时间异常或已过期")
        return
    stamp = pd.to_datetime(quote["timestamp"], unit="ms", utc=True)
    received = pd.to_datetime(quote["received"], unit="ms", utc=True)
    st.caption(f"Bitget · 现货 · {symbol} · {origin} · 最新成交 {quote['price']:g} USDT · {service_state}")
    st.caption(f"交易所行情时间 {stamp:%H:%M:%S.%f} UTC · 系统接收 {received:%H:%M:%S.%f} UTC · "
               f"页面取样时差 {(now - quote['timestamp']) / 1000:.2f}s")
    if metrics.get("samples"):
        p95 = metrics.get("p95_ms")
        st.caption(f"行情时间→网关接收 · 样本 {metrics['samples']} · 中位数 {metrics['median_ms']:g} ms · "
                   f"P95 {f'{p95:g} ms' if p95 is not None else '样本不足'} · 最大 {metrics['max_ms']:g} ms")
