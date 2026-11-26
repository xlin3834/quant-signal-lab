"""Measure Bitget USDT perpetual WebSocket lag for BTC and ETH. Read-only, no orders."""
import json
import time
from statistics import mean, median, quantiles

import websocket

from realtime_engine import PRODUCT, WS, now_ms, normalize_swap_quote
from swap_data import CORE_SYMBOLS, PRODUCT_LABEL

DURATION = 25


def percentile(values, p):
    if len(values) < 2:
        return None
    return quantiles(values, n=100, method="inclusive")[p - 1]


def measure(duration=DURATION):
    ws = websocket.create_connection(WS, timeout=8)
    ws.settimeout(1)
    ws.send(json.dumps({"op": "subscribe", "args": [
        {"instType": PRODUCT, "channel": "ticker", "instId": symbol} for symbol in CORE_SYMBOLS
    ]}))
    lags = {symbol: [] for symbol in CORE_SYMBOLS}
    start = time.monotonic()
    connected = True
    error = None
    try:
        while time.monotonic() - start < duration:
            try:
                body = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if body in ("pong", "ping", None):
                continue
            frame = json.loads(body)
            if frame.get("event") in {"subscribe", "error"}:
                if frame.get("event") == "error":
                    error = frame
                continue
            for row in frame.get("data") or []:
                try:
                    tick = normalize_swap_quote(row)
                except (KeyError, TypeError, ValueError):
                    continue
                lags[tick["symbol"]].append(tick["received"] - tick["timestamp"])
    except Exception as exc:
        connected = False
        error = type(exc).__name__
    finally:
        try:
            ws.close()
        except Exception:
            pass
    report = {"product": PRODUCT_LABEL, "duration_s": duration, "connected": connected, "error": error,
              "measured_at_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
    for symbol, values in lags.items():
        clean = values
        report[symbol] = {
            "samples": len(clean),
            "avg_ms": mean(clean) if clean else None,
            "median_ms": median(clean) if clean else None,
            "p95_ms": percentile(clean, 95),
            "min_ms": min(clean) if clean else None,
            "max_ms": max(clean) if clean else None,
        }
    return report


if __name__ == "__main__":
    result = measure()
    print(json.dumps(result, ensure_ascii=False, indent=2))
