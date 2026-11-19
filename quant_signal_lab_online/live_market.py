"""Independent Bitget spot ticker stream with opt-in read-only HTTP health API."""
import argparse
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlsplit

import websocket

from collections import deque
from statistics import median, quantiles

from market_data import SYMBOLS, URL, MarketDataError, request_data

LOG = logging.getLogger(__name__)
WS = "wss://ws.bitget.com/v2/ws/public"


class Feed:
    def __init__(self):
        self.lock = Lock()
        self.clients = {}
        self.ticks = {}
        self.latencies = {}
        self.error = None
        self.connected = False

    def request(self, symbol):
        with self.lock:
            if symbol not in self.clients and len(self.clients) >= 32:
                raise MarketDataError("实时订阅容量已满")
            self.clients[symbol] = time.monotonic() + 120
            tick = self.ticks.get(symbol)
            return tick if tick and 0 <= int(time.time() * 1000) - tick["timestamp"] <= 15_000 else None

    def subscriptions(self):
        now = time.monotonic()
        with self.lock:
            for symbol in tuple(self.clients):
                if self.clients[symbol] <= now:
                    del self.clients[symbol]
                    self.ticks.pop(symbol, None)
                    self.latencies.pop(symbol, None)
            return set(self.clients)

    def ingest(self, body):
        frame = json.loads(body)
        if frame.get("event") == "error":
            raise MarketDataError(f"Bitget WS 订阅错误 {frame.get('code')}")
        for row in frame.get("data", []):
            try:
                tick = normalize_quote(row)
            except (KeyError, TypeError, ValueError):
                LOG.warning("Malformed Bitget WebSocket ticker")
                continue
            symbol = tick["symbol"]
            lag = tick["received"] - tick["timestamp"]
            if not 0 <= lag <= 15_000:
                continue
            with self.lock:
                old = self.ticks.get(symbol)
                if symbol in self.clients and (old is None or old["timestamp"] < tick["timestamp"]):
                    self.ticks[symbol] = tick
                    self.latencies.setdefault(symbol, deque(maxlen=512)).append(lag)

    @staticmethod
    def send(ws, operation, symbols):
        if symbols:
            ws.send(json.dumps({"op": operation, "args": [
                {"instType": "SPOT", "channel": "ticker", "instId": symbol}
                for symbol in sorted(symbols)
            ]}))

    def run(self, stop=None):
        stop = Event() if stop is None else stop
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
                self.connected, self.error = True, None
                active = set()
                last_ping = last_pong = time.monotonic()
                next_sync = 0
                while not stop.is_set():
                    now = time.monotonic()
                    if now >= next_sync:
                        wanted = self.subscriptions()
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
                    if body == "pong":
                        last_pong = time.monotonic()
                    else:
                        self.ingest(body)
            except (OSError, ValueError, websocket.WebSocketException) as exc:
                self.error = type(exc).__name__
                LOG.warning("Bitget stream reconnecting: %s", exc)
                wait = retry
                retry = min(retry * 2, 30)
            finally:
                with self.lock:
                    self.connected = False
                    self.ticks.clear()
                if ws is not None:
                    try:
                        ws.close()
                    except (OSError, websocket.WebSocketException):
                        LOG.debug("Bitget stream already closed", exc_info=True)
            if wait:
                stop.wait(wait)


    def details(self, symbol):
        tick = self.request(symbol)
        with self.lock:
            values = list(self.latencies.get(symbol, ()))
        return {
            "tick": tick, "connected": self.connected, "error": self.error, "samples": len(values),
            "median_ms": median(values) if values else None,
            "p95_ms": quantiles(values, n=100, method="inclusive")[94] if len(values) >= 2 else None,
            "max_ms": max(values) if values else None,
        }


def normalize_quote(row, received=None):
    price = float(row["lastPr"])
    if not 0 < price < float("inf"):
        raise ValueError("Bitget 价格无效")
    return {"symbol": row.get("symbol") or row.get("instId"), "price": price,
            "timestamp": int(row["ts"]), "received": int(time.time() * 1000) if received is None else received}


def rest_quote(symbol):
    rows = request_data(URL.replace("/candles", "/tickers"), {"symbol": symbol})
    row = next((row for row in rows if row["symbol"] == symbol), None)
    if row is None:
        raise MarketDataError("Bitget 未返回该现货交易对")
    return normalize_quote(row)


def handler(feed):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urlsplit(self.path)
            if query.path == "/health":
                payload = {"connected": feed.connected, "error": feed.error,
                           "subscriptions": len(feed.subscriptions())}
            elif query.path == "/ticker":
                symbol = parse_qs(query.query).get("symbol", [""])[0]
                if symbol not in SYMBOLS:
                    self.send_error(400, "Unsupported spot symbol")
                    return
                payload = feed.details(symbol)
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
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    feed = Feed()
    Thread(target=feed.run, daemon=True).start()
    ThreadingHTTPServer((args.host, args.port), handler(feed)).serve_forever()


if __name__ == "__main__":
    main()
