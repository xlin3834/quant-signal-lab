"""Always-on cross monitor. Run this on a process that does not sleep.

Render's free web service sleeps when idle. Starting this module inside that
web process does not make it 24/7. Use a worker or a machine that stays up.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from workbench.crosses import baseline_seen, confirmed_crosses, event_key, new_events
from workbench.engine_path import PACKAGE
from workbench.notify import deliver_event, enabled_channels, runtime_status
from workbench.watchlist import load_watchlist

STATE_PATH = PACKAGE.parent / "workbench" / "data" / "monitor_state.json"
LOG_PATH = PACKAGE.parent / "workbench" / "data" / "alert_log.jsonl"


def load_state(path=STATE_PATH):
    if not path.is_file():
        return {"initialized": {}, "seen": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"initialized": {}, "seen": []}
    payload.setdefault("initialized", {})
    payload.setdefault("seen", [])
    return payload


def save_state(state, path=STATE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"initialized": state["initialized"], "seen": list(state["seen"])[-5000:]}, ensure_ascii=False), encoding="utf-8")


def append_log(record, path=LOG_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def evaluate_symbol(symbol, interval, bars, state, exchange="Bitget"):
    """First sight of a pair only records history. Later new crosses can notify."""
    key = f"{exchange}|{symbol}|{interval}"
    tagged = []
    for event in confirmed_crosses(bars):
        tagged.append({**event, "symbol": symbol, "interval": interval, "key": event_key(event, exchange, symbol, interval)})
    if not state["initialized"].get(key):
        state["initialized"][key] = True
        state["seen"] = list(set(state["seen"]) | baseline_seen(tagged))
        return []
    fresh, seen = new_events(tagged, state["seen"])
    state["seen"] = list(seen)
    return fresh


def run_once(load_bars, channels=None, watchlist=None, state=None):
    if channels is None:
        channels = tuple(enabled_channels())
    watchlist = watchlist if watchlist is not None else load_watchlist()
    state = state if state is not None else load_state()
    emitted = []
    for item in watchlist.get("symbols") or []:
        if item.get("muted"):
            continue
        symbol = item.get("symbol")
        for interval in item.get("timeframes") or ["15m"]:
            try:
                bars = load_bars(symbol, interval)
            except Exception as exc:
                emitted.append({"symbol": symbol, "interval": interval, "error": str(exc), "sent": False})
                continue
            events = evaluate_symbol(symbol, interval, bars, state)
            for event in events:
                delivery = deliver_event(event, channels)
                record = {"event": event, "delivery": delivery["results"], "text": delivery["text"]}
                append_log(record)
                emitted.append(record)
    save_state(state)
    return {"emitted": emitted, "runtime": runtime_status()}


def main():
    from workbench.research import load_closed

    def load_bars(symbol, interval):
        closed, _forming = load_closed(symbol, interval, 240)
        return [{"t": int(row.time.timestamp() * 1000), "c": float(row.close)} for row in closed.itertuples(index=False)]

    print(runtime_status()["reason"])
    print("常驻命令只在不休眠的进程里持续有效。")
    while True:
        try:
            result = run_once(load_bars, channels=("in_app", "email", "telegram_channel", "telegram_dm"))
            print(json.dumps({"count": len(result["emitted"])}, ensure_ascii=False))
        except Exception as exc:
            print(f"monitor loop {type(exc).__name__}")
        time.sleep(30)


if __name__ == "__main__":
    main()
