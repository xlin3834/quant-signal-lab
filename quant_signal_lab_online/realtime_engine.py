"""In-process Bitget USDT-perpetual WebSocket cache with REST backfill.

Spot and perpetual data are stored under distinct product labels and are never mixed.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from statistics import median, quantiles
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import websocket

from market_data import INTERVALS, MarketDataError, parse_candles
from swap_data import (
    CORE_SYMBOLS, MIX_GRANULARITY, PRODUCT, PRODUCT_LABEL, ROOT,
    fetch_swap_candles, fetch_swap_ticker,
)

LOG = logging.getLogger(__name__)
WS = "wss://ws.bitget.com/v2/ws/public"
LIVE_MS = 2_000
STALE_WS_MS = 15_000
STALE_REST_MS = 35_000
CLOCK_SKEW_MS = 2_000
CANDLE_CHANNELS = {interval: f"candle{MIX_GRANULARITY[interval]}" for interval in MIX_GRANULARITY}
CHANNEL_INTERVAL = {value: key for key, value in CANDLE_CHANNELS.items()}


def now_ms():
    return int(time.time() * 1000)


def lag_ok(exchange_ts, received, max_age=STALE_WS_MS, now=None):
    now = now_ms() if now is None else now
    return (-CLOCK_SKEW_MS <= received - exchange_ts
            and 0 <= now - exchange_ts <= max_age
            and exchange_ts <= received + CLOCK_SKEW_MS)


def percentile(values, p):
    if len(values) < 2:
        return None
    return quantiles(values, n=100, method="inclusive")[p - 1]


def classify(connected, error, origin, age_ms, permission="公开行情，无需交易密钥"):
    if error and not connected and origin is None:
        return "不可用"
    if permission and "不足" in permission:
        return "权限不足"
    if not connected:
        if origin in {"REST 快照", "WebSocket 缓存（已断线）"} and age_ms is not None:
            if age_ms <= (STALE_REST_MS if origin == "REST 快照" else STALE_WS_MS):
                return "延迟"
            return "过期"
        return "断线"
    if age_ms is None:
        return "不可用"
    if age_ms < -CLOCK_SKEW_MS:
        return "不可用"
    if origin == "WebSocket" and connected and age_ms <= LIVE_MS:
        return "实时"
    if origin == "WebSocket" and connected and age_ms <= STALE_WS_MS:
        return "延迟"
    if origin == "REST 快照" and age_ms <= STALE_REST_MS:
        return "延迟"
    return "过期"


def normalize_swap_quote(row, received=None, origin="WebSocket"):
    price = float(row["lastPr"])
    if not 0 < price < float("inf"):
        raise ValueError("Bitget 永续价格无效")
    bid = float(row["bidPr"]) if row.get("bidPr") not in (None, "") else None
    ask = float(row["askPr"]) if row.get("askPr") not in (None, "") else None
    if bid is not None and not 0 < bid < float("inf"):
        bid = None
    if ask is not None and not 0 < ask < float("inf"):
        ask = None
    symbol = row.get("symbol") or row.get("instId")
    if not symbol:
        raise ValueError("缺少合约代码")
    return {
        "symbol": symbol,
        "product": PRODUCT,
        "product_label": PRODUCT_LABEL,
        "price": price,
        "bid": bid,
        "ask": ask,
        "timestamp": int(row["ts"]),
        "received": now_ms() if received is None else int(received),
        "origin": origin,
    }


class SwapFeed:
    def __init__(self):
        self.lock = Lock()
        self.clients = {}
        self.ticks = {}
        self.rest_ticks = {}
        self.candles = {}
        self.forming = {}
        self.latencies = {}
        self.ui_lags = {}
        self.missing = {}
        self.disconnects = 0
        self.error = None
        self.connected = False
        self.stop = Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.stop = Event()
        self._thread = Thread(target=self.run, daemon=True, name="bitget-usdt-futures-ws")
        self._thread.start()

    def request(self, symbol, intervals=("15m", "5m", "1H", "4H")):
        if symbol not in CORE_SYMBOLS:
            raise MarketDataError("本阶段实时行情仅覆盖 BTCUSDT 与 ETHUSDT 的 USDT 永续")
        with self.lock:
            if symbol not in self.clients and len(self.clients) >= 32:
                raise MarketDataError("实时订阅容量已满")
            self.clients[symbol] = {"until": time.monotonic() + 120, "intervals": tuple(intervals)}
        self._ensure_history(symbol, intervals)

    def subscriptions(self):
        now = time.monotonic()
        with self.lock:
            for symbol in tuple(self.clients):
                if self.clients[symbol]["until"] <= now:
                    del self.clients[symbol]
            return {symbol: dict(meta) for symbol, meta in self.clients.items()}

    def _channels(self, wanted):
        args = []
        for symbol, meta in wanted.items():
            args.append(("ticker", symbol))
            for interval in meta["intervals"]:
                if interval in CANDLE_CHANNELS:
                    args.append((CANDLE_CHANNELS[interval], symbol))
        return set(args)

    def send(self, ws, operation, channels):
        if not channels:
            return
        ws.send(json.dumps({"op": operation, "args": [
            {"instType": PRODUCT, "channel": channel, "instId": symbol}
            for channel, symbol in sorted(channels)
        ]}))

    def ingest(self, body):
        if body in ("pong", "ping"):
            return "pong" if body == "pong" else "ping"
        frame = json.loads(body)
        if frame.get("event") == "error":
            raise MarketDataError(f"Bitget WS 订阅错误 {frame.get('code')} {frame.get('msg', '')}")
        if frame.get("event") == "subscribe":
            return "subscribe"
        arg = frame.get("arg") or {}
        if arg.get("instType") not in (None, PRODUCT):
            LOG.warning("Ignored non-perpetual Bitget frame %s", arg)
            return "ignored"
        channel = arg.get("channel")
        data = frame.get("data") or []
        if channel == "ticker":
            self._ingest_tickers(data)
        elif channel in CHANNEL_INTERVAL:
            self._ingest_candles(arg.get("instId"), CHANNEL_INTERVAL[channel], data, frame.get("action"))
        return channel

    def _ingest_tickers(self, rows):
        for row in rows:
            try:
                tick = normalize_swap_quote(row)
            except (KeyError, TypeError, ValueError):
                LOG.warning("Malformed Bitget perpetual ticker")
                continue
            lag = tick["received"] - tick["timestamp"]
            if not -CLOCK_SKEW_MS <= lag <= STALE_WS_MS:
                continue
            with self.lock:
                if tick["symbol"] not in self.clients:
                    continue
                old = self.ticks.get(tick["symbol"])
                if old is None or old["timestamp"] <= tick["timestamp"]:
                    if old and old["timestamp"] == tick["timestamp"] and old["price"] == tick["price"]:
                        continue
                    self.ticks[tick["symbol"]] = tick
                    self.latencies.setdefault(tick["symbol"], deque(maxlen=1024)).append(max(lag, 0))

    def _ingest_candles(self, symbol, interval, rows, action):
        if not symbol or not rows:
            return
        try:
            incoming = parse_candles(rows)
        except MarketDataError:
            LOG.warning("Malformed Bitget perpetual candle %s %s", symbol, interval)
            return
        duration = INTERVALS[interval][1]
        now = pd.Timestamp.now(tz="UTC")
        forming = incoming.loc[incoming["time"] + duration > now]
        closed = incoming.loc[incoming["time"] + duration <= now]
        with self.lock:
            if symbol not in self.clients:
                return
            key = (symbol, interval)
            current = self.candles.get(key)
            if action == "snapshot" or current is None:
                if current is None:
                    self.candles[key] = incoming.sort_values("time").drop_duplicates("time").reset_index(drop=True)
                else:
                    merged = pd.concat([current, incoming], ignore_index=True)
                    self.candles[key] = merged.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
            else:
                merged = pd.concat([current, incoming], ignore_index=True)
                self.candles[key] = merged.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
            if not forming.empty:
                self.forming[key] = forming.iloc[-1].to_dict()
            self.missing[key] = self._gap_count(self.candles[key], interval)

    def _gap_count(self, data, interval):
        if data is None or len(data) < 2:
            return 0
        duration = INTERVALS[interval][1]
        deltas = data["time"].diff().iloc[1:]
        extra = (deltas / duration).round() - 1
        return int(extra[extra > 0].sum()) if not extra.empty else 0

    def _ensure_history(self, symbol, intervals):
        for interval in intervals:
            key = (symbol, interval)
            with self.lock:
                have = self.candles.get(key)
            if have is not None and len(have) >= 50:
                continue
            try:
                history = fetch_swap_candles(symbol, interval, 200)
            except MarketDataError as exc:
                LOG.warning("REST backfill failed %s %s: %s", symbol, interval, exc)
                continue
            if history is None or getattr(history, "empty", False):
                continue
            with self.lock:
                current = self.candles.get(key)
                merged = history if current is None or current.empty else pd.concat([history, current], ignore_index=True)
                self.candles[key] = merged.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
                self.missing[key] = self._gap_count(self.candles[key], interval)

    def rest_fallback(self, symbol):
        try:
            tick = normalize_swap_quote(fetch_swap_ticker(symbol), origin="REST 快照")
        except (MarketDataError, KeyError, TypeError, ValueError) as exc:
            LOG.warning("Perpetual REST ticker failed %s: %s", symbol, exc)
            return None
        with self.lock:
            self.rest_ticks[symbol] = tick
        return tick

    def snapshot(self, symbol, front_ms=None):
        self.request(symbol)
        front_ms = now_ms() if front_ms is None else front_ms
        with self.lock:
            tick = self.ticks.get(symbol)
            rest = self.rest_ticks.get(symbol)
            connected = self.connected
            error = self.error
            disconnects = self.disconnects
            values = list(self.latencies.get(symbol, ()))
            missing = sum(self.missing.get((symbol, interval), 0) for interval in MIX_GRANULARITY)
            candles = {interval: (None if (symbol, interval) not in self.candles
                                  else len(self.candles[(symbol, interval)]))
                       for interval in ("5m", "15m", "1H", "4H")}
        origin = None
        chosen = None
        if tick and lag_ok(tick["timestamp"], tick["received"], STALE_WS_MS) and connected:
            chosen, origin = tick, "WebSocket"
        elif tick and lag_ok(tick["timestamp"], tick["received"], STALE_WS_MS):
            chosen, origin = {**tick, "origin": "WebSocket 缓存（已断线）"}, "WebSocket 缓存（已断线）"
        if chosen is None:
            chosen = rest if rest is not None else self.rest_fallback(symbol)
            origin = None if chosen is None else "REST 快照"
        age = None if chosen is None else front_ms - chosen["timestamp"]
        status = classify(connected, error, origin, age)
        if chosen is None:
            transport = None
        else:
            transport = chosen["received"] - chosen["timestamp"]
        official_ok = status == "实时" or (status == "延迟" and origin == "WebSocket" and age is not None and age <= STALE_WS_MS)
        # Official candidates require a live or briefly delayed WebSocket quote, never a stale REST price labelled live.
        if status in {"过期", "断线", "不可用", "权限不足"} or origin != "WebSocket":
            official_ok = False
        record = {
            "symbol": symbol,
            "product": PRODUCT,
            "product_label": PRODUCT_LABEL,
            "source": PRODUCT_LABEL,
            "permission": "公开行情，无需交易密钥",
            "session": "USDT 永续 24 小时连续交易",
            "status": status,
            "origin": origin,
            "connected": connected,
            "error": error,
            "price": None if chosen is None else chosen["price"],
            "bid": None if chosen is None else chosen.get("bid"),
            "ask": None if chosen is None else chosen.get("ask"),
            "timestamp": None if chosen is None else chosen["timestamp"],
            "received": None if chosen is None else chosen["received"],
            "frontend": front_ms,
            "transport_ms": transport,
            "age_ms": age,
            "samples": len(values),
            "avg_ms": (sum(values) / len(values)) if values else None,
            "median_ms": median(values) if values else None,
            "p95_ms": percentile(values, 95),
            "max_ms": max(values) if values else None,
            "disconnects": disconnects,
            "missing_candles": missing,
            "candle_bars": candles,
            "official_candidates_ok": official_ok,
        }
        if values is not None:
            with self.lock:
                self.ui_lags.setdefault(symbol, deque(maxlen=512)).append(max(age or 0, 0))
        return record

    def closed_candles(self, symbol, interval):
        self.request(symbol, (interval, "15m", "5m", "1H", "4H") if interval in MIX_GRANULARITY else ("15m", "5m", "1H", "4H"))
        key = (symbol, interval)
        with self.lock:
            data = self.candles.get(key)
            data = None if data is None else data.copy()
        if data is None or data.empty:
            data = fetch_swap_candles(symbol, interval, 200)
            with self.lock:
                self.candles[key] = data
        return data

    def run(self, stop=None):
        stop = self.stop if stop is None else stop
        retry = 1
        while not stop.is_set():
            if not self.subscriptions():
                stop.wait(1)
                continue
            ws = None
            wait = 0
            try:
                ws = websocket.create_connection(WS, timeout=6)
                ws.settimeout(1)
                with self.lock:
                    self.connected, self.error = True, None
                active = set()
                last_ping = last_pong = time.monotonic()
                next_sync = 0
                while not stop.is_set():
                    now = time.monotonic()
                    if now >= next_sync:
                        wanted = self._channels(self.subscriptions())
                        if not wanted:
                            break
                        self.send(ws, "unsubscribe", active - wanted)
                        self.send(ws, "subscribe", wanted - active)
                        active = wanted
                        next_sync = now + 1
                    if now - last_ping >= 25:
                        ws.send("ping")
                        last_ping = now
                    if now - last_pong > 50:
                        raise TimeoutError("Bitget WS pong 超时")
                    try:
                        body = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not body:
                        raise ConnectionError("Bitget WS closed")
                    retry = 1
                    if body == "pong" or self.ingest(body) == "pong":
                        last_pong = time.monotonic()
            except (OSError, ValueError, websocket.WebSocketException, MarketDataError, TimeoutError) as exc:
                with self.lock:
                    self.error = type(exc).__name__
                    self.disconnects += 1
                LOG.warning("Bitget perpetual stream reconnecting: %s", exc)
                wait = retry
                retry = min(retry * 2, 30)
            finally:
                with self.lock:
                    self.connected = False
                if ws is not None:
                    try:
                        ws.close()
                    except (OSError, websocket.WebSocketException):
                        LOG.debug("Bitget perpetual stream already closed", exc_info=True)
            if wait:
                stop.wait(wait)


_HUB = None
_HUB_LOCK = Lock()


def get_hub():
    global _HUB
    with _HUB_LOCK:
        if _HUB is None:
            _HUB = SwapFeed()
            _HUB.start()
        return _HUB


def handler(feed):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            LOG.debug(format, *args)

        def do_GET(self):
            query = urlsplit(self.path)
            if query.path == "/health":
                payload = {"connected": feed.connected, "error": feed.error,
                           "product": PRODUCT, "subscriptions": len(feed.subscriptions())}
            elif query.path in ("/ticker", "/status"):
                symbol = parse_qs(query.query).get("symbol", [""])[0]
                if symbol not in CORE_SYMBOLS:
                    self.send_error(400, "Only BTCUSDT and ETHUSDT USDT perpetuals are enabled")
                    return
                payload = feed.snapshot(symbol)
            else:
                self.send_error(404)
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    feed = SwapFeed()
    feed.start()
    ThreadingHTTPServer((args.host, args.port), handler(feed)).serve_forever()


if __name__ == "__main__":
    main()
