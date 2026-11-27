"""Shared watchlist. Stars are the alert list. No six-item cap.

One deployment shares one list so a refresh or another browser on the same
server sees the same stars. This is not a multi-user account system.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from workbench.engine_path import PACKAGE

DEFAULT_PATH = PACKAGE.parent / "workbench" / "data" / "watchlist.json"
MAX_SYMBOLS = 200
TIMEFRAMES = ("5m", "15m", "30m", "1H", "4H", "1D")
DEFAULT_TIMEFRAMES = ("5m", "15m")


def watchlist_path():
    override = os.environ.get("QSL_WATCHLIST_PATH")
    return Path(override) if override else DEFAULT_PATH


def _empty():
    return {"version": 1, "symbols": []}


def load_watchlist(path=None):
    path = path or watchlist_path()
    if not path.is_file():
        return _empty()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    payload.setdefault("version", 1)
    payload.setdefault("symbols", [])
    return payload


def save_watchlist(payload, path=None):
    path = path or watchlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _clean_symbol(symbol):
    text = (symbol or "").strip().upper()
    if not text or len(text) > 32:
        raise ValueError("代码无效")
    return text


def _find(payload, symbol):
    for item in payload["symbols"]:
        if item.get("symbol") == symbol:
            return item
    return None


def mutate(action, symbol, path=None, muted=None, timeframes=None):
    payload = load_watchlist(path)
    symbol = _clean_symbol(symbol)
    item = _find(payload, symbol)
    if action == "add":
        if item is None:
            if len(payload["symbols"]) >= MAX_SYMBOLS:
                raise ValueError(f"自选已达 {MAX_SYMBOLS} 个，先取消一些再加")
            payload["symbols"].append({
                "symbol": symbol,
                "muted": False,
                "timeframes": list(DEFAULT_TIMEFRAMES),
            })
        elif muted is not None:
            item["muted"] = bool(muted)
    elif action == "remove":
        payload["symbols"] = [row for row in payload["symbols"] if row.get("symbol") != symbol]
    elif action == "mute":
        if item is None:
            raise ValueError("不在自选里")
        item["muted"] = True
    elif action == "unmute":
        if item is None:
            raise ValueError("不在自选里")
        item["muted"] = False
    elif action == "timeframes":
        if item is None:
            raise ValueError("不在自选里")
        chosen = [frame for frame in (timeframes or []) if frame in TIMEFRAMES]
        if not chosen:
            raise ValueError("至少保留一个周期")
        item["timeframes"] = chosen
    else:
        raise ValueError("不支持的自选操作")
    return save_watchlist(payload, path)
