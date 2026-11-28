"""Read-only market research on top of the existing engines.

No orders, no trade API keys, and no paid data purchases. BTC and ETH use the
Bitget USDT-M public stream. Cash NVDA and spot XAU/USD stay unlabeled as live
when those feeds are not authorized.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from workbench.crosses import attach_indicators, confirmed_crosses, latest_cross, rank_by_turnover, relation_snapshot
from workbench.honesty import GATE_NOTE, LATENCY_NOTE, WEIGHTS_NOTE
from workbench.notify import channel_config, runtime_status
from workbench.catalog import (
    badge_for,
    classify_contract,
    filter_catalog,
    freshness_from_status,
    gap_rows,
    overlay_live,
    summarize,
    ticker_freshness,
)
from workbench.engine_path import PACKAGE
from workbench.store import WorkbenchError, load_settings, preset_file

from backtest import Costs, simulate
from indicators import Parameters, calculate
from macro_fib import FibParams, MacroParams, analyze_macro, fibonacci_grid, load_macro_frames
from market_data import INTERVALS, MarketDataError, parse_candles, request_data
from perpetual_market import build_rows, load_directory, load_tickers, quote_state
from preset_backtest import compare_frames
from realtime_engine import now_ms
from signals import Rules, aligned_frames, score_with_config
from strategy_modules import (
    DynamicStop,
    EmaBoll,
    IntradayScore,
    Pullback,
    dynamic_levels,
    ema_boll_plan,
    environment,
    pullback_plan,
    shocks,
)
from swap_data import CORE_SYMBOLS, MIX_GRANULARITY, PRODUCT, PRODUCT_LABEL, ROOT, fetch_swap_candles

BJ = ZoneInfo("Asia/Shanghai")
BLOCKED = {
    "NVDA": "NVDA 正股没有已授权行情。不会用 NVDAUSDT 代币化永续顶替。",
    "XAUUSD": "XAU/USD 现货没有已授权行情。不会用 XAUUSDT、XAUTUSDT 或 PAXGUSDT 顶替。",
    "XAU/USD": "XAU/USD 现货没有已授权行情。不会用代币化黄金永续顶替。",
}
DURATION = {
    "1m": pd.Timedelta(minutes=1),
    "5m": INTERVALS["5m"][1],
    "15m": INTERVALS["15m"][1],
    "30m": pd.Timedelta(minutes=30),
    "1H": INTERVALS["1H"][1],
    "4H": INTERVALS["4H"][1],
    "1D": INTERVALS["1D"][1],
    "1W": pd.Timedelta(days=7),
}
GRANULARITY = {**MIX_GRANULARITY, "1m": "1m", "30m": "30m", "1W": "1W"}
_LOCK = Lock()
_DIR = {"at": 0.0, "rows": None, "fetched": None, "error": None}
_FRAMES = {}
_RADAR = {}
_CAL = {"at": 0.0, "payload": None}
_BOARD = {
    "at": 0.0, "rows": None, "error": None, "fetched_at": None,
    "positions": [], "omitted": [], "equities": {},
    "refreshing": False, "event": None,
}
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
BOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
HL_INFO = "https://api.hyperliquid.xyz/info"


def json_ready(value):
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if not math.isfinite(value) else value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int)):
        return value
    return str(value)


def beijing(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = datetime.fromtimestamp(float(value) / (1000 if float(value) > 10**11 else 1), BJ)
        return stamp.strftime("%Y-%m-%d %H:%M:%S")
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.tz_convert(BJ).strftime("%Y-%m-%d %H:%M:%S")


def feed_enabled():
    return os.environ.get("QSL_DISABLE_FEED") != "1"


def get_feed():
    if not feed_enabled():
        return None
    from realtime_engine import get_hub

    hub = get_hub()
    for symbol in CORE_SYMBOLS:
        try:
            hub.request(symbol)
        except MarketDataError:
            continue
    return hub


def _blank_snap(symbol, freshness, message):
    return {
        "symbol": symbol,
        "product": PRODUCT,
        "product_label": PRODUCT_LABEL,
        "price": None,
        "bid": None,
        "ask": None,
        "timestamp": None,
        "received": None,
        "origin": None,
        "status": freshness,
        "freshness": freshness,
        "age_ms": None,
        "transport_ms": None,
        "samples": 0,
        "avg_ms": None,
        "median_ms": None,
        "p95_ms": None,
        "official_candidates_ok": False,
        "message": message,
    }


def _public_snap(record):
    freshness = freshness_from_status(record.get("status"))
    if freshness == "实时" and record.get("origin") != "WebSocket":
        freshness = "延迟"
    return {
        "symbol": record.get("symbol"),
        "product": record.get("product"),
        "product_label": record.get("product_label") or PRODUCT_LABEL,
        "price": record.get("price"),
        "bid": record.get("bid"),
        "ask": record.get("ask"),
        "timestamp": record.get("timestamp"),
        "received": record.get("received"),
        "origin": record.get("origin"),
        "status": record.get("status"),
        "freshness": freshness,
        "age_ms": record.get("age_ms"),
        "transport_ms": record.get("transport_ms"),
        "samples": record.get("samples"),
        "avg_ms": record.get("avg_ms"),
        "median_ms": record.get("median_ms"),
        "p95_ms": record.get("p95_ms"),
        "max_ms": record.get("max_ms"),
        "connected": record.get("connected"),
        "official_candidates_ok": bool(record.get("official_candidates_ok")),
        "exchange_time": beijing(record.get("timestamp")),
        "received_time": beijing(record.get("received")),
        "message": record.get("error"),
    }


def live_quotes():
    symbols = {}
    error = None
    connected = False
    if not feed_enabled():
        for symbol in CORE_SYMBOLS:
            symbols[symbol] = _blank_snap(symbol, "断线", "行情流未启动")
    else:
        try:
            hub = get_feed()
            connected = bool(hub and hub.connected)
            for symbol in CORE_SYMBOLS:
                symbols[symbol] = _public_snap(hub.snapshot(symbol))
        except (MarketDataError, OSError, RuntimeError) as exc:
            error = str(exc)
            for symbol in CORE_SYMBOLS:
                symbols[symbol] = _blank_snap(symbol, "断线", error)
    payload = {
        "server_ms": now_ms(),
        "feed": {"connected": connected, "error": error, "product": PRODUCT_LABEL},
        "symbols": symbols,
        "badge": _stream_badge(symbols),
    }
    return payload


def _stream_badge(symbols):
    snaps = [symbols.get(symbol) or {} for symbol in CORE_SYMBOLS]
    if any(item.get("price") is None or item.get("freshness") in {"断线", "不可用", "过期", "权限不足", "数据异常"} for item in snaps):
        return {"level": "down", "text": "Bitget价格流 · 已暂停"}
    if all(item.get("freshness") == "实时" and item.get("origin") == "WebSocket" for item in snaps):
        return {"level": "live", "text": "Bitget价格流 · 实时"}
    return {"level": "delay", "text": "Bitget价格流 · 延迟"}


def _live_asset(asset_id, name, symbol, quote, not_substitute):
    freshness = None if quote is None else quote.get("freshness")
    origin = None if quote is None else quote.get("origin")
    if freshness == "实时" and origin == "WebSocket":
        connection = "已连接"
    elif freshness in {"延迟", "快照"}:
        connection = "已连接但不是实时流"
    else:
        connection = "未保持实时连接"
    return {
        "id": asset_id,
        "name": name,
        "symbol": symbol,
        "product_class": "crypto_perp",
        "product_label": "Bitget USDT 本位永续",
        "connected": True,
        "quote": quote,
        "reason": None,
        "source": f"Bitget 官方 USDT 本位永续 · {origin or '无来源'}",
        "updated_at": None if quote is None else quote.get("exchange_time"),
        "connection": connection,
        "freshness_label": freshness or "无行情",
        "not_substitute": not_substitute,
        "latency_note": LATENCY_NOTE,
    }


def _closed_asset(asset_id, name, symbol, product_class, product_label, reason, not_substitute):
    return {
        "id": asset_id,
        "name": name,
        "symbol": symbol,
        "product_class": product_class,
        "product_label": product_label,
        "connected": False,
        "quote": None,
        "reason": reason,
        "source": "未接通",
        "updated_at": None,
        "connection": "未连接",
        "freshness_label": "无行情",
        "not_substitute": not_substitute,
        "latency_note": LATENCY_NOTE,
    }


def core_assets(quotes=None):
    quotes = quotes or live_quotes()
    symbols = quotes["symbols"]
    return {
        "assets": [
            _live_asset("BTC", "比特币", "BTCUSDT", symbols.get("BTCUSDT"), "这是 Bitget USDT 本位永续，不是现货。"),
            _live_asset("ETH", "以太坊", "ETHUSDT", symbols.get("ETHUSDT"), "这是 Bitget USDT 本位永续，不是现货。"),
            _live_asset("SOL", "索拉纳", "SOLUSDT", symbols.get("SOLUSDT"), "这是 Bitget 的 SOLUSDT 永续，不是其他交易所的现货。"),
            _live_asset("BNB", "BNB", "BNBUSDT", symbols.get("BNBUSDT"), "这是 Bitget 的 BNBUSDT 永续，不是币安现货。"),
            _closed_asset(
                "NVDA", "英伟达", "NVDA", "cash_equity", "美股正股", BLOCKED["NVDA"],
                "NVDAUSDT 是代币化股票永续，不能代替这只正股，这里也不引用它的价格。",
            ),
            _closed_asset(
                "XAU", "现货黄金", "XAU/USD", "spot_gold", "现货黄金 XAU/USD", BLOCKED["XAUUSD"],
                "XAUUSDT、XAUTUSDT、PAXGUSDT 是不同产品，不能代替 XAU/USD 现货，这里也不引用它们的价格。",
            ),
        ],
        "badge": quotes["badge"],
        "server_ms": quotes["server_ms"],
        "latency_note": LATENCY_NOTE,
    }


def _present_directory_row(row, now):
    spec = row.get("spec") or {}
    meta = classify_contract(spec.get("baseCoin") or row.get("base"), spec.get("isRwa"))
    state = quote_state(row, now)
    return {
        "symbol": row.get("symbol"),
        "base": row.get("base"),
        "price": row.get("price"),
        "turnover": row.get("turnover"),
        "change": row.get("change"),
        "range_pct": row.get("range_pct"),
        "spread_pct": row.get("spread_pct"),
        "funding": row.get("funding"),
        "timestamp": row.get("timestamp"),
        "quote_state": state,
        "freshness": ticker_freshness(state),
        "price_source": "Bitget ticker 快照",
        "source": f"{meta['product_label']} · Bitget 公开快照",
        "updated_label": beijing(row.get("timestamp")) if row.get("timestamp") else None,
        "connection": ticker_freshness(state),
        **meta,
    }


def directory(force=False):
    now = time.time()
    with _LOCK:
        cached = _DIR["rows"]
        fresh = cached is not None and now - _DIR["at"] < 30 and not force
        error = _DIR["error"]
    if fresh:
        rows = cached
    else:
        try:
            specs = load_directory()
            ticks, fetched = load_tickers()
            rows = [_present_directory_row(row, now_ms()) for row in build_rows(specs, ticks)]
            error = None
            with _LOCK:
                _DIR.update(at=time.time(), rows=rows, fetched=fetched, error=None)
        except MarketDataError as exc:
            error = str(exc)
            if cached is None:
                raise
            rows = cached
            with _LOCK:
                _DIR["error"] = error
    stamped = [_present_directory_row({**row, "spec": {"baseCoin": row.get("base"), "isRwa": "YES" if row["product_class"] != "crypto_perp" else "NO"}}, now_ms()) if False else dict(row) for row in rows]
    # Recompute freshness against the current clock without rebuilding classes.
    current = now_ms()
    refreshed = []
    for row in stamped:
        state = quote_state(row, current)
        item = dict(row)
        if item.get("price_source") == "Bitget ticker 快照" or item.get("origin") is None:
            item["quote_state"] = state
            item["freshness"] = ticker_freshness(state)
        refreshed.append(item)
    quotes = live_quotes()
    merged = overlay_live(refreshed, quotes["symbols"])
    return {
        "rows": merged,
        "gaps": gap_rows(),
        "error": error,
        "server_ms": quotes["server_ms"],
        "summary": summarize(merged),
        "badge": quotes["badge"],
    }


def catalog_view(query="", product="", exchange="bitget", sort="turnover", page=1, page_size=40):
    payload = directory()
    rows, note = filter_catalog(payload["rows"], payload["gaps"], query, product, exchange)
    if sort == "turnover":
        rows = sorted(rows, key=lambda row: (row.get("freshness") != "快照" and row.get("freshness") != "实时" and row.get("freshness") != "延迟", -(row.get("turnover") or 0), row.get("symbol") or ""))
    elif sort == "change_desc":
        rows = sorted(rows, key=lambda row: (-(row.get("change") if row.get("change") is not None else -10**9), row.get("symbol") or ""))
    elif sort == "change_asc":
        rows = sorted(rows, key=lambda row: ((row.get("change") if row.get("change") is not None else 10**9), row.get("symbol") or ""))
    elif sort == "range":
        rows = sorted(rows, key=lambda row: (-(row.get("range_pct") or 0), row.get("symbol") or ""))
    elif sort == "name":
        rows = sorted(rows, key=lambda row: row.get("symbol") or "")
    total = summarize(rows)
    page = max(1, int(page))
    page_size = min(1000, max(1, int(page_size)))
    start = (page - 1) * page_size
    visible = rows[start:start + page_size]
    return {
        "rows": visible,
        "page": page,
        "pages": max(1, math.ceil(len(rows) / page_size)) if rows else 1,
        "matched": len(rows),
        "summary": summarize(visible),
        "filtered_summary": total,
        "note": note,
        "error": payload["error"],
        "server_ms": payload["server_ms"],
        "turnover_change": "快照没有成交额增幅字段，这项不计算。",
    }


def _split_closed(frame, interval):
    duration = DURATION[interval]
    now = pd.Timestamp.now(tz="UTC")
    closed = frame.loc[frame["time"] + duration <= now].reset_index(drop=True)
    forming = frame.loc[frame["time"] + duration > now]
    return closed, (None if forming.empty else forming.iloc[-1])


def _fetch_raw(symbol, interval, limit):
    if interval not in GRANULARITY:
        raise WorkbenchError(f"不支持的周期 {interval}")
    if interval in MIX_GRANULARITY:
        return fetch_swap_candles(symbol, interval, limit)
    return parse_candles(request_data(f"{ROOT}/candles", {
        "symbol": symbol, "productType": PRODUCT,
        "granularity": GRANULARITY[interval], "limit": str(limit),
    }))


def load_closed(symbol, interval, limit=200):
    key = (symbol, interval)
    now = time.time()
    with _LOCK:
        cached = _FRAMES.get(key)
    if cached and now - cached["at"] < 20:
        frame = cached["frame"]
    else:
        frame = None
        if symbol in CORE_SYMBOLS and interval in MIX_GRANULARITY:
            hub = get_feed()
            if hub is not None:
                try:
                    frame = hub.closed_candles(symbol, interval)
                except MarketDataError:
                    frame = None
        if frame is None or len(frame) < 80:
            frame = _fetch_raw(symbol, interval, limit)
        with _LOCK:
            _FRAMES[key] = {"at": time.time(), "frame": frame}
    closed, forming = _split_closed(frame, interval)
    if limit:
        closed = closed.tail(limit).reset_index(drop=True)
    return closed, forming


def _params_rules(settings):
    intra = settings["modules"]["intraday"]
    extra = settings["extra"]
    params = Parameters(
        tuple(intra["ema"]), int(intra["boll"]), int(intra["rsi"]), tuple(intra["macd"]),
        int(intra["atr"]), int(intra["volume"]), int(extra["structure"]), float(intra["boll_mult"]),
    )
    rules = Rules(
        int(intra["threshold"]), int(extra["strong"]), float(extra["minimum_rr"]), int(extra["early"]),
        float(intra["rvol"]), float(extra["atr_stop"]), tuple(intra["weights"]),
        float(intra["extreme_atr_pct"]) / 100,
    )
    return params, rules


def _modules(settings):
    raw_e = settings["modules"]["ema_boll"]
    raw_p = settings["modules"]["pullback"]
    raw_d = settings["modules"]["dynamic_stop"]
    ema = EmaBoll(
        bool(raw_e["enabled"]), bool(raw_e["only_two_lines"]), bool(raw_e["show_levels"]),
        int(raw_e["ema"]), raw_e["interval"], bool(raw_e["inherit"]), float(raw_e["capital"]),
        float(raw_e["risk_pct"]) / 100, float(raw_e["leverage"]), float(raw_e["atr_stop"]),
        float(raw_e["fee_pct"]) / 100, float(raw_e["slip_pct"]) / 100,
    )
    pull = Pullback(
        bool(raw_p["enabled"]), bool(raw_p["both_sides"]), bool(raw_p["show_levels"]),
        bool(raw_p["inherit"]), float(raw_p["capital"]), float(raw_p["risk_pct"]) / 100,
        float(raw_p["leverage"]), float(raw_p["rsi_long"]), float(raw_p["rsi_short"]),
        float(raw_p["fee_pct"]) / 100, float(raw_p["slip_pct"]) / 100,
    )
    stop = DynamicStop(
        raw_d["scheme"], int(raw_d["boll"]), float(raw_d["boll_mult"]), int(raw_d["atr"]),
        float(raw_d["minimum_rr"]), float(raw_d["structure_buffer"]), float(raw_d["atr_guard"]),
        float(raw_d["second_atr"]), float(raw_d["max_stop_pct"]) / 100, float(raw_d["trail_start_r"]),
        raw_d["trail_method"], float(raw_d["trail_atr"]), bool(raw_d["breakeven"]), bool(raw_d["trailing"]),
    )
    score = IntradayScore(
        tuple(settings["modules"]["intraday"]["ema"]),
        tuple(settings["modules"]["intraday"]["macd"]),
        int(settings["modules"]["intraday"]["rsi"]),
        int(settings["modules"]["intraday"]["atr"]),
        int(settings["modules"]["intraday"]["boll"]),
        float(settings["modules"]["intraday"]["boll_mult"]),
        int(settings["modules"]["intraday"]["volume"]),
        int(settings["modules"]["intraday"]["threshold"]),
        float(settings["modules"]["intraday"]["rvol"]),
        float(settings["modules"]["intraday"]["extreme_atr_pct"]) / 100,
        float(settings["modules"]["intraday"]["shock_5m_pct"]) / 100,
        float(settings["modules"]["intraday"]["shock_15m_pct"]) / 100,
        float(settings["modules"]["intraday"]["shock_1h_pct"]) / 100,
        float(settings["modules"]["intraday"]["volume_shock"]),
        float(settings["modules"]["intraday"]["volume_high"]),
        tuple(settings["modules"]["intraday"]["weights"]),
    )
    return ema, pull, stop, score


def _num_or_none(value):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    if pd.isna(value):
        return None
    return float(value)


def _bar_records(frame, lengths, forming=None):
    records = []
    for row in frame.itertuples(index=False):
        records.append({
            "t": int(pd.Timestamp(row.time).timestamp() * 1000),
            "o": float(row.open), "h": float(row.high), "l": float(row.low),
            "c": float(row.close), "v": float(row.volume),
            "ema_fast": _num_or_none(getattr(row, "ema7", None)),
            "ema_short": _num_or_none(getattr(row, "ema20", None)),
            "ema_mid": _num_or_none(getattr(row, "ema50", None)),
            "ema_bg": _num_or_none(getattr(row, "ema200", None)),
            "ema7": _num_or_none(getattr(row, "disp_ema7", None)),
            "ema10": _num_or_none(getattr(row, "disp_ema10", None)),
            "ema120": _num_or_none(getattr(row, "disp_ema120", None)),
            "boll18_mid": _num_or_none(getattr(row, "disp_boll_mid", None)),
            "boll18_up": _num_or_none(getattr(row, "disp_boll_up", None)),
            "boll18_low": _num_or_none(getattr(row, "disp_boll_low", None)),
            "boll_mid": _num_or_none(getattr(row, "boll_mid", None)),
            "boll_up": _num_or_none(getattr(row, "boll_up", None)),
            "boll_low": _num_or_none(getattr(row, "boll_low", None)),
            "macd": _num_or_none(getattr(row, "macd", None)),
            "macd_signal": _num_or_none(getattr(row, "macd_signal", None)),
            "rsi": _num_or_none(getattr(row, "rsi", None)),
            "atr": _num_or_none(getattr(row, "atr", None)),
            "forming": False,
        })
    if forming is not None:
        records.append({
            "t": int(pd.Timestamp(forming.time).timestamp() * 1000),
            "o": float(forming.open), "h": float(forming.high), "l": float(forming.low),
            "c": float(forming.close), "v": float(forming.volume),
            "ema_fast": None, "ema_short": None, "ema_mid": None, "ema_bg": None,
            "ema7": None, "ema10": None, "ema120": None,
            "boll18_mid": None, "boll18_up": None, "boll18_low": None,
            "boll_mid": None, "boll_up": None, "boll_low": None,
            "macd": None, "macd_signal": None, "rsi": None, "atr": None,
            "forming": True,
        })
    return records


def resolve_symbol(symbol):
    raw = (symbol or "").strip().upper()
    if raw in {"BITCOIN", "BTC"}:
        raw = "BTCUSDT"
    elif raw in {"ETHEREUM", "ETH"}:
        raw = "ETHUSDT"
    elif raw in {"SOL", "SOLANA"}:
        raw = "SOLUSDT"
    elif raw == "BNB":
        raw = "BNBUSDT"
    if raw in BLOCKED or raw.replace("-", "") in {"XAUUSD"}:
        key = "XAUUSD" if "XAU" in raw and "USDT" not in raw else raw
        if raw in {"XAU", "XAUUSD", "XAU/USD"}:
            key = "XAUUSD"
        raise WorkbenchError(BLOCKED.get(key) or BLOCKED.get(raw) or "该产品没有已授权行情")
    return raw


def chart(symbol, interval="15m"):
    try:
        symbol = resolve_symbol(symbol)
    except WorkbenchError as exc:
        return {"ok": False, "blocked": True, "symbol": symbol, "reason": str(exc), "bars": []}
    settings = load_settings()
    params, _rules = _params_rules(settings)
    closed, forming = load_closed(symbol, interval, 240)
    if closed.empty:
        raise MarketDataError("没有已收盘 K 线")
    calculated = calculate(closed, params)
    display = attach_indicators([float(value) for value in calculated.close])
    for key, values in display.items():
        calculated[f"disp_{key}"] = values
    if symbol in CORE_SYMBOLS:
        label, product_class = PRODUCT_LABEL, "crypto_perp"
    elif symbol in {"XAUUSDT", "XAGUSDT"}:
        metal = "黄金" if symbol == "XAUUSDT" else "白银"
        label, product_class = f"Bitget USDT 本位代币化{metal}永续", "tokenized_metal"
    else:
        if _label_from_directory(symbol) is None:
            try:
                directory()
            except MarketDataError:
                pass
        label = _label_from_directory(symbol) or "Bitget USDT 永续（目录里没有这个代码，不能当成正股或现货）"
        product_class = _class_from_directory(symbol) or "unknown"
    duration = DURATION[interval]
    last_close = pd.Timestamp(calculated.time.iloc[-1])
    age = pd.Timestamp.now(tz="UTC") - (last_close + duration)
    stale = age > duration + pd.Timedelta(seconds=90)
    return {
        "ok": True,
        "symbol": symbol,
        "interval": interval,
        "product_label": label,
        "product_class": product_class,
        "lengths": {
            "ema": list(params.ema),
            "macd": list(params.macd),
            "rsi": params.rsi,
            "boll": [params.boll, params.boll_mult],
        },
        "bars": _bar_records(calculated, params.ema, forming if not stale else None),
        "forming": forming is not None and not stale,
        "stale": bool(stale),
        "last_closed": beijing(last_close),
        "default_layers": ["ema7", "ema10", "ema120", "boll18"],
        "indicator_style": {"ema7": "#f5c542", "ema10": "#3aa0ff", "ema120": "#b07cff", "boll": "#7f93a8"},
        "warning": "最后一根若标记为未收盘，只供观察，不能当作正式信号。" if forming is not None else "",
        "banner": None if product_class == "crypto_perp" else f"图上是 {label}。它不是 XAU/USD 现货，也不是股票或 CFD。",
        "cross_outcomes": _recent_outcomes(calculated),
        "outcome_note": "下面是交叉之后的价格变化、最大有利波动和最大不利波动。没有规定进场、出场、止损和费用，所以不能称为胜率。",
    }


def _recent_outcomes(frame):
    bars = []
    for row in frame.itertuples(index=False):
        bars.append({
            "t": int(pd.Timestamp(row.time).timestamp() * 1000),
            "c": float(row.close),
            "h": float(row.high),
            "l": float(row.low),
        })
    from workbench.crosses import forward_path
    reports = []
    for event in confirmed_crosses(bars)[-3:]:
        reports.append({
            "label": event["label"],
            "note": event["note"],
            "windows": [forward_path(bars, event["index"], horizon) for horizon in (2, 4, 8)],
        })
    return reports


def _directory_index():
    with _LOCK:
        rows = _DIR["rows"] or []
    return {row["symbol"]: row for row in rows}


def _label_from_directory(symbol):
    row = _directory_index().get(symbol)
    return None if row is None else row.get("product_label")


def _class_from_directory(symbol):
    row = _directory_index().get(symbol)
    return None if row is None else row.get("product_class")


def _side_name(side):
    return {1: "多", -1: "空", 0: "观望"}.get(int(side or 0), "观望")


def build_conclusion(market_ok, side, score_long, score_short, reasons_long, reasons_short,
                     direction_4h, direction_1h, alert="", forming=False):
    """Closed-bar stance. Missing scores stay 无法判断. Unclosed alerts stay provisional."""
    higher = f"4小时{direction_4h or '未知'} · 1小时{direction_1h or '未知'}"
    if score_long is None or score_short is None:
        return {
            "action": "无法判断", "status": "数据不足", "score_long": score_long, "score_short": score_short,
            "reason": "已收盘数据不足，不能给出做多、做空或观望。", "higher_tf": higher, "provisional": "",
        }
    if not market_ok:
        return {
            "action": "无法判断", "status": "行情不可核验", "score_long": score_long, "score_short": score_short,
            "reason": f"行情过期或断线，暂停方向判断。多头 {score_long:g} / 空头 {score_short:g}。分数不是胜率。",
            "higher_tf": higher, "provisional": "",
        }
    tail = "分数不是胜率，也不会自动下单。"
    background = f"更高周期只作背景：{higher}。"
    if side == 1:
        return {
            "action": "做多", "status": "已收盘确认", "score_long": score_long, "score_short": score_short,
            "reason": f"已收盘多头 {score_long:g}，空头 {score_short:g}。{reasons_long or ''} {background}{tail}",
            "higher_tf": higher,
            "provisional": "另有未收盘K线，不改变这条已收盘结论。" if forming else "",
        }
    if side == -1:
        return {
            "action": "做空", "status": "已收盘确认", "score_long": score_long, "score_short": score_short,
            "reason": f"已收盘空头 {score_short:g}，多头 {score_long:g}。{reasons_short or ''} {background}{tail}",
            "higher_tf": higher,
            "provisional": "另有未收盘K线，不改变这条已收盘结论。" if forming else "",
        }
    provisional = ""
    if alert == "临时预警·多":
        provisional = "未确认做多倾向"
    elif alert == "临时预警·空":
        provisional = "未确认做空倾向"
    reason = f"多头 {score_long:g} / 空头 {score_short:g}，未形成已收盘正式候选。{background}"
    if provisional:
        reason += f"{provisional}，不能当成已经确认的方向。"
    reason += tail
    return {
        "action": "观望", "status": "未确认" if provisional else "已收盘",
        "score_long": score_long, "score_short": score_short, "reason": reason,
        "higher_tf": higher, "provisional": provisional,
    }


def radar(symbol="BTCUSDT", settings=None):
    symbol = resolve_symbol(symbol)
    if symbol not in CORE_SYMBOLS:
        raise WorkbenchError("正式评分只对已接通的 BTC、ETH、SOL、BNB USDT 永续计算。其他产品不会编造方向。")
    settings = settings or load_settings()
    key = (symbol, json.dumps(settings["modules"]["intraday"], sort_keys=True), settings["applied_preset"])
    now = time.time()
    with _LOCK:
        cached = _RADAR.get(key)
    if cached and now - cached["at"] < 15:
        return cached["payload"]
    params, rules = _params_rules(settings)
    frames = {}
    forming = None
    for interval in ("15m", "5m", "1H", "4H"):
        closed, form = load_closed(symbol, interval, 240)
        frames[interval] = closed
        if interval == "15m":
            forming = form
    quotes = live_quotes()
    snap = quotes["symbols"][symbol]
    continuous = _continuous(frames["15m"], "15m")
    market_ok = bool(snap.get("official_candidates_ok")) and continuous and len(frames["15m"]) >= 80
    base = aligned_frames(frames, params)
    scored = score_with_config(
        base, rules, market_ok=market_ok, range_mode=settings.get("applied_preset") == "震荡行情型",
    )
    last = scored.iloc[-1]
    history = []
    for row in scored.tail(8).itertuples(index=False):
        history.append({
            "time": beijing(row.time),
            "grade": "当时已收盘的研究分级" if not market_ok else (row.grade or "观望"),
            "stored_grade": None if pd.isna(row.grade) else str(row.grade),
            "side": _side_name(row.side),
            "score_long": _num_or_none(row.score_long),
            "score_short": _num_or_none(row.score_short),
            "alert": "" if not market_ok else ("" if pd.isna(getattr(row, "alert", "")) else str(getattr(row, "alert", "") or "")),
        })
    if not market_ok and history:
        history[-1]["grade"] = "数据不可核验，已暂停正式候选"
    ema_mod, pull_mod, stop_mod, score_mod = _modules(settings)
    if settings["modules"]["ui"].get("atr_adaptive") and _num_or_none(last.atr) and last.close:
        floor = 1.5 * float(last.atr) / float(last.close)
        score_mod = IntradayScore(
            score_mod.ema, score_mod.macd, score_mod.rsi, score_mod.atr, score_mod.boll, score_mod.boll_mult,
            score_mod.volume, score_mod.threshold, score_mod.rvol, score_mod.extreme_atr,
            max(score_mod.shock_5m, floor), max(score_mod.shock_15m, floor), max(score_mod.shock_1h, floor),
            score_mod.volume_shock, score_mod.volume_high, score_mod.weights,
        )
    shock = shocks(frames, score_mod)
    regime = environment(frames["15m"])
    regime["auto_apply"] = False
    cross = ema_boll_plan(frames.get(ema_mod.interval, frames["15m"]), ema_mod, params) if ema_mod.enabled else None
    pull = pullback_plan(frames, pull_mod, params) if pull_mod.enabled else None
    plan = None
    side = int(last.side) if market_ok and not pd.isna(last.side) else 0
    if side:
        suffix = "long" if side == 1 else "short"
        plan = dynamic_levels(
            last, stop_mod, side, float(last.close), float(last[f"stop_{suffix}"]),
            float(last[f"target1_{suffix}"]), float(last[f"target2_{suffix}"]), float(last.atr),
        )
        plan["note"] = "参考演算，不是委托，也不会发送到交易所。"
    direction = _num_or_none(getattr(last, "direction_4H", None))
    direction_1h = _num_or_none(getattr(last, "direction_1H", None))
    direction_1h_text = "多" if direction_1h == 1 else "空" if direction_1h == -1 else "中性"
    direction_4h_text = "多" if direction == 1 else "空" if direction == -1 else "中性"
    alert = "" if not market_ok or pd.isna(getattr(last, "alert", "")) else str(getattr(last, "alert", "") or "")
    conclusion = build_conclusion(
        market_ok, side, _num_or_none(last.score_long), _num_or_none(last.score_short),
        None if pd.isna(getattr(last, "reasons_long", None)) else str(last.reasons_long),
        None if pd.isna(getattr(last, "reasons_short", None)) else str(last.reasons_short),
        direction_4h_text, direction_1h_text, alert, forming is not None,
    )
    payload = {
        "ok": True,
        "symbol": symbol,
        "product_label": PRODUCT_LABEL,
        "preset": settings.get("applied_preset"),
        "market_ok": market_ok,
        "continuous": continuous,
        "quote": snap,
        "forming": forming is not None,
        "forming_note": "有未收盘 K 线。正式分数来自最近一根已收盘 K 线，未收盘部分只作预警。" if forming is not None else "",
        "pause_note": None if market_ok else "行情未达到可核验的实时或短暂延迟 WebSocket 条件，或 K 线不连续。正式候选已暂停。",
        "grade": history[-1]["grade"] if history else "观望",
        "side": _side_name(side),
        "score_long": _num_or_none(last.score_long),
        "score_short": _num_or_none(last.score_short),
        "rsi": _num_or_none(last.rsi),
        "close": _num_or_none(last.close),
        "support": _num_or_none(getattr(last, "support", None)) or _num_or_none(getattr(last, "swing_low", None)),
        "resistance": _num_or_none(getattr(last, "resistance", None)) or _num_or_none(getattr(last, "swing_high", None)),
        "direction_4h": direction_4h_text,
        "direction_1h": direction_1h_text,
        "conclusion": conclusion,
        "reasons_long": None if pd.isna(getattr(last, "reasons_long", None)) else str(last.reasons_long),
        "reasons_short": None if pd.isna(getattr(last, "reasons_short", None)) else str(last.reasons_short),
        "time": beijing(last.time),
        "history": history,
        "environment": json_ready(regime),
        "shocks": json_ready(shock),
        "ema_boll": json_ready(cross) if cross else {"enabled": False, "note": "独立 EMA×布林未启用"},
        "pullback": json_ready(pull) if pull else {"enabled": False, "note": "多周期回踩未启用"},
        "plan": json_ready(plan),
        "weights_note": WEIGHTS_NOTE,
        "gate_note": GATE_NOTE,
        "gate_active": False,
        "latency_note": LATENCY_NOTE,
        "auto_apply": False,
    }
    with _LOCK:
        _RADAR[key] = {"at": time.time(), "payload": payload}
    return payload


def _continuous(frame, interval):
    if frame is None or len(frame) < 3:
        return False
    deltas = frame["time"].diff().iloc[1:]
    return bool(deltas.eq(DURATION[interval]).all())


def macro_view(symbol):
    symbol = resolve_symbol(symbol)
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise WorkbenchError("宏观周期只对已接通的 BTC、ETH 永续计算。")
    settings = load_settings()
    macro = settings["modules"]["macro"]
    fib = settings["modules"]["fib"]
    try:
        frames = load_macro_frames(symbol)
    except MarketDataError as exc:
        return {"ok": False, "symbol": symbol, "error": str(exc), "auto_apply": False}
    report = analyze_macro(frames, MacroParams(
        int(macro["daily_fast"]), int(macro["daily_slow"]), int(macro["weekly_fast"]),
        int(macro["weekly_slow"]), int(macro["swing"]),
    ))
    daily = frames.get(fib["interval"], frames.get("1D"))
    grid = {"ready": False, "reason": "没有对应周期"}
    if daily is not None:
        grid = fibonacci_grid(daily, FibParams(int(fib["swing"]), int(fib["lookback"]), fib["interval"]))
    public_fib = {
        "ready": bool(grid.get("ready")),
        "reason": grid.get("reason"),
        "direction": grid.get("direction"),
        "nearest": grid.get("nearest"),
        "nearest_price": grid.get("nearest_price"),
        "close": grid.get("close"),
        "retracements": grid.get("retracements"),
        "extensions": grid.get("extensions"),
    }
    stages = {}
    for key in ("daily", "weekly", "monthly", "quarterly", "yearly"):
        if key in report and isinstance(report[key], dict):
            stages[key] = {item: report[key].get(item) for item in ("label", "stage", "phase", "reason")}
    return json_ready({
        "ok": True,
        "symbol": symbol,
        "product_label": PRODUCT_LABEL,
        "summary": report.get("summary"),
        "suggestion": report.get("suggestion"),
        "auto_apply": False,
        "stages": stages,
        "fibonacci": public_fib,
        "note": "宏观和斐波那契只给出研究建议，不会自动切换预设。",
    })


def _metric_row(row):
    keep = ("净收益 USDT", "最大回撤 %", "交易次数", "胜率 %", "平均盈亏比", "盈利因子",
            "官方信号数", "取消次数", "期望收益 USDT", "假信号比例 %", "平均持仓", "期末权益 USDT")
    return {key: json_ready(row.get(key)) for key in keep}


def run_backtest(symbol):
    symbol = resolve_symbol(symbol)
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise WorkbenchError("短样本回测只接受 BTCUSDT 与 ETHUSDT。不会用演示收益或其他产品填结果。")
    frames = {}
    for interval in ("15m", "5m", "1H", "4H"):
        # A longer page than the chart cache. 5 分钟必须盖住 15 分钟的预热，否则对比样本会被滤光。
        closed, _forming = _split_closed(fetch_swap_candles(symbol, interval, 1000), interval)
        frames[interval] = closed.reset_index(drop=True)
    costs = Costs()
    results, _scored, _mask = compare_frames(frames, costs, path=preset_file())
    window = results["window"]
    presets = {}
    for name, item in results["presets"].items():
        presets[name] = {"full": _metric_row(item["full"]), "is": _metric_row(item["is"]), "oos": _metric_row(item["oos"])}
    return {
        "ok": True,
        "symbol": symbol,
        "product_label": PRODUCT_LABEL,
        "sample": "短样本：每个周期最多 1000 根公开已收盘 K 线。不是完整历史，也不是仓库里的存档对比。",
        "costs": {"capital": costs.capital, "risk": costs.risk, "fee": costs.fee, "slip": costs.slip,
                  "funding": "未计入资金费、借币息、强平和深度不足"},
        "rules": "已收盘 K 线评分。下一根开盘价模拟成交。同一根先止损、后止盈。样本按时间 70/30 划分，没有在样本内搜索参数。",
        "lookahead": "没有使用未来 K 线重绘指标，也没有把演示收益写进结果。",
        "custom_source": results.get("custom_source"),
        "window": {
            "start": beijing(window["start"]),
            "split": beijing(window["split"]),
            "end": beijing(window["end"]),
            "bars": window["bars"],
            "is_bars": window["is_bars"],
            "oos_bars": window["oos_bars"],
        },
        "presets": presets,
        "optimal": False,
    }


def archive_report():
    path = PACKAGE / "PRESET_COMPARISON.json"
    if not path.is_file():
        return {"ok": False, "error": "仓库里没有预存的对比文件。"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    jobs = []
    for job in payload.get("jobs", []):
        presets = {}
        for name, item in (job.get("presets") or {}).items():
            presets[name] = {part: _metric_row(item.get(part) or {}) for part in ("full", "is", "oos")}
        jobs.append({
            "symbol": job.get("symbol"),
            "venue": job.get("venue"),
            "fetched_at": job.get("fetched_at"),
            "window": {key: beijing(job.get("window", {}).get(key)) if key in {"start", "split", "end"} else job.get("window", {}).get(key)
                       for key in ("start", "split", "end", "bars", "is_bars", "oos_bars")},
            "custom_source": job.get("custom_source"),
            "presets": presets,
        })
    return {
        "ok": True,
        "kind": "存档",
        "generated": "2026-11-26",
        "note": "这是仓库里已完成的一次历史对比，不是刚刚自动跑出来的演示收益。需要更长样本时，在本地运行 python quant_signal_lab_online/preset_backtest.py。",
        "costs": payload.get("costs"),
        "jobs": jobs,
    }


def cross_market(left, right, interval="1H"):
    left_raw, right_raw = left, right
    try:
        left = resolve_symbol(left)
        right = resolve_symbol(right)
    except WorkbenchError as exc:
        return {"ok": False, "error": str(exc), "left": left_raw, "right": right_raw}
    if left == right:
        raise WorkbenchError("请选择两个不同产品。")
    if interval not in {"15m", "1H", "4H", "1D"}:
        raise WorkbenchError("相关计算只使用 15 分钟、1 小时、4 小时或日线已收盘数据。")
    left_frame, _ = load_closed(left, interval, 240)
    right_frame, _ = load_closed(right, interval, 240)
    merged = pd.merge(
        left_frame[["time", "close"]].rename(columns={"close": "left"}),
        right_frame[["time", "close"]].rename(columns={"close": "right"}),
        on="time", how="inner",
    )
    returns = np.log(merged[["left", "right"]]).diff().dropna()
    if len(returns) < 30:
        return {"ok": False, "error": "对齐后的已收盘收益不足 30 根，不报告相关。", "samples": int(len(returns))}
    corr = float(returns["left"].corr(returns["right"]))
    lead = float(returns["left"].iloc[:-1].reset_index(drop=True).corr(returns["right"].iloc[1:].reset_index(drop=True)))
    labels = {
        left: _label_from_directory(left) or (PRODUCT_LABEL if left in {"BTCUSDT", "ETHUSDT"} else left),
        right: _label_from_directory(right) or (PRODUCT_LABEL if right in {"BTCUSDT", "ETHUSDT"} else right),
    }
    tail = returns.tail(180)
    times = merged.loc[tail.index, "time"]
    points = [
        {"t": int(pd.Timestamp(stamp).timestamp() * 1000), "x": float(x_value), "y": float(y_value)}
        for stamp, x_value, y_value in zip(times, tail["left"], tail["right"])
    ]
    return {
        "ok": True,
        "left": {"symbol": left, "label": labels[left]},
        "right": {"symbol": right, "label": labels[right]},
        "interval": interval,
        "samples": int(len(returns)),
        "pearson": corr,
        "lead_lag": lead,
        "start": beijing(merged.time.iloc[1]),
        "end": beijing(merged.time.iloc[-1]),
        "points": points,
        "statement": "皮尔逊相关描述这段样本里两边收益是否一起变动。它不是因果关系，也不是资金流向。领先一根本身也不是交易信号。",
        "flows": None,
    }


def parse_calendar(items, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    events = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("title") or not item.get("date"):
            continue
        stamp = pd.Timestamp(item["date"])
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        events.append({
            "title": str(item.get("title") or ""),
            "country": str(item.get("country") or ""),
            "impact": str(item.get("impact") or ""),
            "forecast": item.get("forecast") or "",
            "previous": item.get("previous") or "",
            "time": beijing(stamp),
            "past": bool(stamp <= now),
        })
    events.sort(key=lambda row: row["time"])
    return events


def calendar():
    now = time.time()
    with _LOCK:
        cached = _CAL["payload"] if now - _CAL["at"] < 600 else None
    if cached is not None:
        return cached
    try:
        response = requests.get(CALENDAR_URL, timeout=(3, 8), headers={"User-Agent": "quant-signal-lab-research"})
        response.raise_for_status()
        items = response.json()
    except (requests.RequestException, ValueError) as exc:
        payload = {"ok": False, "error": f"经济日历暂时取不到：{type(exc).__name__}", "events": [],
                   "source": "Fair Economy 公开周历 JSON"}
        return payload
    payload = {
        "ok": True,
        "source": "Fair Economy 公开周历 JSON（非交易所官方日历，可能不完整或延迟）",
        "timezone": "北京时间",
        "events": parse_calendar(items),
    }
    with _LOCK:
        _CAL.update(at=time.time(), payload=payload)
    return payload


def parse_leaderboard(payload, sort="account", limit=30, direction="desc"):
    rows = []
    for item in payload.get("leaderboardRows") or []:
        windows = {key: value for key, value in (item.get("windowPerformances") or [])}
        def num(window, field):
            raw = (windows.get(window) or {}).get(field)
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        day = num("day", "vlm")
        display_name = (item.get("displayName") or "").strip()
        label, source = trader_label(display_name, "", item.get("ethAddress"))
        rows.append({
            "address": item.get("ethAddress"),
            "display_name": display_name or None,
            "name": label,
            "name_source": source,
            "max_drawdown": None,
            "trade_count": None,
            "win_rate": None,
            "stats_note": "暂无数据",
            "account_value": float(item["accountValue"]) if item.get("accountValue") not in (None, "") else None,
            "account_value_basis": "Hyperliquid 排行榜 accountValue，不是永续 clearinghouse 权益",
            "day_vlm": day,
            "day_pnl": num("day", "pnl"),
            "day_roi": num("day", "roi"),
            "week_pnl": num("week", "pnl"),
            "week_roi": num("week", "roi"),
            "month_pnl": num("month", "pnl"),
            "month_roi": num("month", "roi"),
        })
    key = {"account": "account_value", "day": "day_roi", "week": "week_roi", "month": "month_roi", "day_vlm": "day_vlm"}.get(sort, "account_value")
    reverse = direction != "asc"
    rows.sort(key=lambda row: ((row.get(key) is None), -(row.get(key) or 0) if reverse else (row.get(key) or 0)))
    if not reverse:
        rows.sort(key=lambda row: ((row.get(key) is None), row.get(key) or 0))
    return rows[: max(1, min(int(limit), 50))]


def short_address(address):
    if not isinstance(address, str) or len(address) < 10:
        return address or ""
    return f"{address[:6]}…{address[-4:]}"


def trader_label(display_name, note, address):
    """Nickname order is displayName, then a saved note, then the short address.

    A missing public name is not replaced with an invented trader name.
    """
    display = display_name.strip() if isinstance(display_name, str) else ""
    if display:
        return display, "displayName"
    remark = note.strip() if isinstance(note, str) else ""
    if remark:
        return remark, "note"
    short = short_address(address)
    if short:
        return short, "address"
    return "未公开昵称", "none"


def _optional_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_positions(payload, address, name="", dex=""):
    """Open positions from a public Hyperliquid clearinghouse snapshot."""
    updated = beijing(payload.get("time")) if isinstance(payload, dict) and payload.get("time") else None
    display = name.strip() if isinstance(name, str) else ""
    rows = []
    for item in (payload or {}).get("assetPositions") or []:
        position = item.get("position") or {}
        size = _optional_float(position.get("szi"))
        if size is None or size == 0:
            continue
        entry = _optional_float(position.get("entryPx"))
        mark = _optional_float(position.get("markPx"))
        value = _optional_float(position.get("positionValue"))
        mark_note = None
        if mark is None and value is not None and size != 0:
            mark = abs(value / size)
            mark_note = "由公开持仓名义价值除以数量得到，不是另接的现货价"
        leverage = position.get("leverage")
        if isinstance(leverage, dict):
            leverage = leverage.get("value")
        rows.append({
            "address": address,
            "display_name": display or None,
            "name": display,
            "name_source": "displayName" if display else ("address" if address else "none"),
            "label": display or short_address(address),
            "venue": "Hyperliquid",
            "coin": position.get("coin"),
            "side": "多" if size > 0 else "空",
            "size": abs(size),
            "entry": entry,
            "mark": mark,
            "mark_note": mark_note,
            "leverage": _optional_float(leverage),
            "unrealized_pnl": _optional_float(position.get("unrealizedPnl")),
            "updated_at": updated,
            "dex": dex or "默认永续",
            "source": "Hyperliquid 公开永续持仓" + (f"（{dex}）" if dex else ""),
        })
    return rows


def open_positions_from_states(states, per_address=12):
    """Turn clearinghouse snapshots into open positions. Flat accounts add nothing."""
    rows = []
    equities = {}
    for item in states or []:
        payload = item.get("payload") or {}
        address = item.get("address") or ""
        margin = payload.get("marginSummary") or {}
        if address:
            equities[address] = margin.get("accountValue")
        rows.extend(parse_positions(payload, address, item.get("name") or "", item.get("dex") or ""))
    grouped = {}
    for row in rows:
        grouped.setdefault(row["address"], []).append(row)
    shown = []
    omitted = []
    for address, items in grouped.items():
        items.sort(key=lambda row: abs((row.get("entry") or 0) * (row.get("size") or 0)), reverse=True)
        shown.extend(items[:per_address])
        if len(items) > per_address:
            omitted.append({"address": address, "shown": per_address, "total": len(items)})
    return shown, omitted, equities


def _split_addresses(value):
    found = []
    for part in str(value or "").split(","):
        address = part.strip()
        if len(address) == 42 and address.startswith("0x"):
            try:
                int(address[2:], 16)
            except ValueError:
                continue
            if address not in found:
                found.append(address)
    return found[:12]


def _post_info(body, timeout=(2, 3)):
    response = requests.post(
        HL_INFO, json=body, timeout=timeout, headers={"User-Agent": "quant-signal-lab-research"},
    )
    response.raise_for_status()
    return response.json()


def perp_dex_names():
    now = time.time()
    with _LOCK:
        cached = _BOARD.get("dexes")
        fresh = cached is not None and now - _BOARD.get("dexes_at", 0) < 3600
    if fresh:
        return list(cached)
    try:
        payload = _post_info({"type": "perpDexs"}, timeout=(2, 6))
    except (requests.RequestException, ValueError):
        return list(cached or [])
    names = []
    for item in payload or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    with _LOCK:
        _BOARD["dexes"] = names
        _BOARD["dexes_at"] = time.time()
    return names


def _fetch_states(traders, include_other_dexes=False, dex_limit=3, timeout=(2, 3)):
    from concurrent.futures import ThreadPoolExecutor

    dexes = [""]
    if include_other_dexes:
        dexes.extend(list(perp_dex_names())[:dex_limit])

    def one(job):
        trader, dex = job
        address = trader.get("address")
        body = {"type": "clearinghouseState", "user": address}
        if dex:
            body["dex"] = dex
        try:
            payload = _post_info(body, timeout=timeout)
        except (requests.RequestException, ValueError):
            return None
        display = (trader.get("display_name") or trader.get("name") or "")
        return {
            "address": address,
            "name": display if trader.get("display_name") else (trader.get("name") or ""),
            "display_name": trader.get("display_name") or "",
            "dex": dex,
            "payload": payload,
        }

    jobs = [(trader, dex) for trader in traders if trader.get("address") for dex in dexes]
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [item for item in pool.map(one, jobs) if item]


def _load_leaderboard(timeout=(3, 8)):
    response = requests.get(BOARD_URL, timeout=timeout, headers={"User-Agent": "quant-signal-lab-research"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("leaderboard")
    return payload


def _remember_board(payload):
    stamp = beijing(now_ms())
    with _LOCK:
        _BOARD.update(at=time.time(), rows=payload, error=None, fetched_at=stamp)
    return stamp


def _refresh_public_positions(payload):
    """Clearinghouse reads stay off the first HTTP response."""
    try:
        active = parse_leaderboard(payload, sort="day_vlm", limit=6, direction="desc")
        seen = set()
        batch = []
        for trader in active:
            address = trader.get("address")
            if not address or address in seen:
                continue
            seen.add(address)
            batch.append({
                "address": address,
                "display_name": trader.get("display_name") or "",
                "name": trader.get("display_name") or "",
            })
        states = _fetch_states(batch, include_other_dexes=False)
        positions, omitted, equities = open_positions_from_states(states)
        with _LOCK:
            _BOARD["positions"] = positions
            _BOARD["omitted"] = omitted
            _BOARD["equities"] = equities
            _BOARD["positions_checked"] = True
            _BOARD["position_error"] = None
    except Exception as exc:
        with _LOCK:
            _BOARD["positions_checked"] = False
            _BOARD["position_error"] = f"公开持仓这次没有读完：{type(exc).__name__}"


def _kick_board_refresh():
    """One background refresh. Callers can wait briefly, never for the proxy timeout."""
    with _LOCK:
        if _BOARD.get("refreshing") and _BOARD.get("event") is not None:
            return _BOARD["event"]
        event = Event()
        _BOARD["refreshing"] = True
        _BOARD["event"] = event

    def run():
        try:
            payload = _load_leaderboard()
            _remember_board(payload)
            _refresh_public_positions(payload)
        except (requests.RequestException, ValueError) as exc:
            with _LOCK:
                _BOARD["error"] = f"Hyperliquid 公开排行榜暂时取不到：{type(exc).__name__}"
        finally:
            with _LOCK:
                _BOARD["refreshing"] = False
            event.set()

    Thread(target=run, daemon=True).start()
    return event


def warm_monitor():
    """Start the public board before the first visitor so the page is not cold."""
    try:
        _kick_board_refresh()
    except Exception:
        return


def _board_snapshot():
    now = time.time()
    with _LOCK:
        payload = _BOARD.get("rows")
        age = None if payload is None else now - _BOARD.get("at", 0)
        return {
            "payload": payload,
            "age": age,
            "stored_at": _BOARD.get("fetched_at"),
            "positions": list(_BOARD.get("positions") or []),
            "omitted": list(_BOARD.get("omitted") or []),
            "equities": dict(_BOARD.get("equities") or {}),
            "error": _BOARD.get("error"),
            "refreshing": bool(_BOARD.get("refreshing")),
            "positions_checked": bool(_BOARD.get("positions_checked")),
            "position_error": _BOARD.get("position_error"),
        }


def monitor(sort="account", limit=30, direction="desc", watched="", wait_seconds=4):
    """Return public Hyperliquid data without holding the request open.

    A cold leaderboard waits at most ``wait_seconds``. Position fan-out runs
    in the background. Upstream failure stays ``ok: true`` so the page can
    explain it instead of surfacing a gateway 502.
    """
    snap = _board_snapshot()
    old = snap["payload"] is not None and (snap["age"] or 0) >= 60
    if snap["payload"] is None or old:
        event = _kick_board_refresh()
        if snap["payload"] is None and event is not None:
            event.wait(timeout=max(0, float(wait_seconds)))
            snap = _board_snapshot()
    payload = snap["payload"]
    error = snap["error"]
    positions = snap["positions"]
    omitted = snap["omitted"]
    equities = snap["equities"]
    pending = bool(snap["refreshing"]) and not snap.get("positions_checked") and not positions
    if payload is None and snap["refreshing"] and not error:
        pending = True
    position_error = snap.get("position_error")
    if payload is not None and not positions and not snap.get("positions_checked") and not snap["refreshing"] and not position_error:
        position_error = "公开持仓这次没有读完。"
    rows = parse_leaderboard(payload, sort, limit, direction) if isinstance(payload, dict) else []
    for row in rows:
        if row.get("address") in equities:
            row["perp_account_value"] = equities[row["address"]]
    watched_positions = []
    watched_errors = []
    watched_rows = [{"address": address, "display_name": "", "name": ""} for address in _split_addresses(watched)]
    if watched_rows:
        try:
            states = _fetch_states(watched_rows[:6], include_other_dexes=False, timeout=(1.5, 2))
            watched_positions, _ignored, _equities = open_positions_from_states(states, per_address=20)
            failed = {item["address"] for item in watched_rows} - {item.get("address") for item in states}
            watched_errors.extend({"address": address, "error": "读取失败"} for address in failed)
        except Exception:
            watched_errors = [{"address": item["address"], "error": "读取失败"} for item in watched_rows]
    if payload is None and not pending and not error:
        error = "Hyperliquid 公开排行榜暂时取不到。"
    return {
        "ok": True,
        "stale": bool(error) and payload is not None,
        "error": error,
        "positions_pending": pending,
        "position_error": position_error,
        "updated_at": snap["stored_at"],
        "source": "Hyperliquid 公开持仓与排行榜",
        "venue": "Hyperliquid",
        "notice": "这里是 Hyperliquid 公开交易员持仓，不是你的 Bitget 账户，也不能下单。没有交易密钥。刷新公开榜不会改你保存在浏览器里的自选地址。",
        "account_value_note": "榜单 account_value 是 Hyperliquid 排行榜字段。perp_account_value 才是同一地址永续 clearinghouse 的权益，两者经常不一致。",
        "rows": rows,
        "positions": positions,
        "positions_omitted": omitted,
        "watched_positions": watched_positions,
        "watched_errors": watched_errors,
        "matched": len((payload or {}).get("leaderboardRows") or []) if isinstance(payload, dict) else 0,
    }


def public_wallet(address):
    if not isinstance(address, str) or len(address) != 42 or not address.startswith("0x"):
        raise WorkbenchError("请输入 0x 开头的 40 位公开地址。不要填写私钥或 API Key。")
    try:
        int(address[2:], 16)
    except ValueError as exc:
        raise WorkbenchError("地址格式无效") from exc
    states = _fetch_states([{"address": address, "name": ""}], include_other_dexes=True)
    if not states:
        raise MarketDataError("公开账户暂时读不到")
    default = next((item.get("payload") or {} for item in states if not item.get("dex")), {})
    margin = default.get("marginSummary") or {}
    positions, omitted, _equities = open_positions_from_states(states, per_address=20)
    flat_note = None if positions else "官方永续账户当前没有持仓。这不是请求失败，也没有用其他数据编造仓位。"
    return {
        "ok": True,
        "address": address,
        "name": "",
        "label": short_address(address),
        "account_value": margin.get("accountValue"),
        "account_value_basis": "Hyperliquid 永续 clearinghouseState.marginSummary.accountValue。排行榜 accountValue 是另一个字段，不能当成这笔权益。",
        "withdrawable": default.get("withdrawable"),
        "positions": positions,
        "positions_omitted": omitted,
        "flat_note": flat_note,
        "updated_at": beijing(default.get("time")) if default.get("time") else None,
        "notice": "这是 Hyperliquid 上该地址的公开永续持仓，不是你的 Bitget 账户，也不会下单。",
    }


def reference_position(body):
    if not isinstance(body, dict):
        raise WorkbenchError("请提交计算器字段")
    equity = _required(body, "equity", 10, 100_000_000)
    risk_pct = _required(body, "risk_pct", 0.1, 5) / 100
    entry = _required(body, "entry", 0.0000001, 10_000_000)
    leverage = _required(body, "leverage", 1, 20)
    fee = _required(body, "fee_pct", 0, 1) / 100
    slip = _required(body, "slip_pct", 0, 1) / 100
    atr_mult = _required(body, "atr_mult", 0.5, 8)
    min_qty = _required(body, "min_qty", 0.00000001, 1_000_000)
    min_rr = _required(body, "min_rr", 0.5, 10)
    mode = body.get("mode") or "ATR与结构取较宽"
    stop = body.get("stop")
    stop = None if stop in (None, "") else _required({"stop": stop}, "stop", 0.0000001, 10_000_000)
    atr = body.get("atr")
    atr = None if atr in (None, "") else _required({"atr": atr}, "atr", 0.0000001, 10_000_000)
    if mode == "ATR与结构取较宽":
        if atr is None:
            raise WorkbenchError("这个止损模式需要填写 ATR")
        atr_distance = atr_mult * atr
        structure = abs(entry - stop) if stop else 0
        distance = max(atr_distance, structure)
        used = "ATR 距离与结构距离中的较大者" if stop else "ATR 距离"
    elif mode == "固定止损价":
        if stop is None or stop == entry:
            raise WorkbenchError("固定止损需要一个与入场价不同的止损价")
        distance = abs(entry - stop)
        used = "固定止损价"
    else:
        raise WorkbenchError("止损模式无效")
    side = 1 if stop is None or stop < entry else -1
    planned_stop = entry - side * distance
    risk_per_unit = distance + entry * fee * 2 + entry * slip * 2
    budget = equity * risk_pct
    raw_qty = budget / risk_per_unit if risk_per_unit > 0 else 0
    steps = math.floor(raw_qty / min_qty)
    qty = steps * min_qty
    notional = qty * entry
    margin = notional / leverage if leverage else None
    return {
        "ok": True,
        "qty": qty,
        "raw_qty": raw_qty,
        "notional": notional,
        "margin": margin,
        "stop": planned_stop,
        "distance": distance,
        "risk_budget": budget,
        "risk_per_unit": risk_per_unit,
        "side": "多" if side == 1 else "空",
        "mode": used,
        "min_rr": min_rr,
        "leverage_note": "杠杆只换算参考保证金，不放大止损对应的亏损预算。",
        "cost_note": "手续费和滑点是假设值，按入场价的双边近似计入每单位风险。不含资金费和借币息。",
        "order_note": "这是参考计算器，不是账户风控，也不会下单。",
        "rr_note": "最小净盈亏比只作为你的计划约束提示。这里不编造目标价。",
        "min_qty_note": None if qty > 0 else "按最小数量步长取整后数量为 0，说明风险预算盖不住一档最小数量。",
    }


def _required(body, key, lo, hi):
    if key not in body or body[key] in (None, ""):
        raise WorkbenchError(f"{key} 不能为空")
    try:
        value = float(body[key])
    except (TypeError, ValueError):
        raise WorkbenchError(f"{key} 不是数字") from None
    if not math.isfinite(value) or value < lo or value > hi:
        raise WorkbenchError(f"{key} 超出允许范围")
    return value


def unscored_stance(symbol):
    """Scanner lean stays empty unless the closed-bar scorer produced it.

    Calling this does not score the symbol and does not loosen the 做多/做空 gate.
    Core symbols stay pending so the page can read /api/radar. Everything else
    gets an explicit reason instead of a made-up direction.
    """
    if symbol in CORE_SYMBOLS:
        return {
            "scored": False,
            "pending": True,
            "lean": None,
            "action": None,
            "score_long": None,
            "score_short": None,
            "reason": "正在读取已收盘分数。读不到之前不会写成做多或做空。",
        }
    return {
        "scored": False,
        "pending": False,
        "lean": None,
        "action": None,
        "score_long": None,
        "score_short": None,
        "reason": "只给 BTC、ETH、SOL、BNB 打分。这一行不编造方向或分数。",
    }


def market_scan(product="crypto_perp", interval="15m", cross_filter="all"):
    """Top 20 by USDT turnover inside one product class, plus cross state."""
    payload = directory()
    pool = [row for row in payload["rows"] if row.get("product_class") == product]
    prepared = []
    for row in pool:
        prepared.append({**row, "turnover_unit": "USDT", "quote": "USDT"})
    ranked, blocked = rank_by_turnover(prepared, 20)
    if product in {"spot_gold", "cash_equity", "cfd"}:
        ranked, blocked = [], [{**row, "rank": None, "rank_reason": "没有可靠的现货或正股成交额，不编造排名"} for row in gap_rows() if row.get("product_class") == product]
    for row in ranked:
        try:
            closed, _forming = load_closed(row["symbol"], interval, 240)
            bars = [{"t": int(item.time.timestamp() * 1000), "c": float(item.close)} for item in closed.itertuples(index=False)]
            snap = relation_snapshot([bar["c"] for bar in bars])
            last = latest_cross(confirmed_crosses(bars))
            row.update({
                "ema7_vs_mid": snap.get("ema7_vs_mid"),
                "ema10_vs_mid": snap.get("ema10_vs_mid"),
                "ema120_vs_mid": snap.get("ema120_vs_mid"),
                "last_cross": last["type"],
                "last_cross_time": beijing(last["time"]) if last.get("time") not in (None, "") else None,
                "kline_state": "已用收盘K线" if snap.get("ready") else (snap.get("reason") or "K线不足"),
                "stance": unscored_stance(row.get("symbol")),
            })
        except Exception as exc:
            row.update({
                "ema7_vs_mid": "数据不足", "ema10_vs_mid": "数据不足", "ema120_vs_mid": "数据不足",
                "last_cross": "暂无交叉", "last_cross_time": None,
                "kline_state": f"K线不可用：{exc}",
                "stance": unscored_stance(row.get("symbol")),
            })
    if cross_filter == "up":
        ranked = [row for row in ranked if "上穿" in (row.get("last_cross") or "")]
    elif cross_filter == "down":
        ranked = [row for row in ranked if "下穿" in (row.get("last_cross") or "")]
    elif cross_filter == "none":
        ranked = [row for row in ranked if row.get("last_cross") in (None, "暂无交叉")]
    return {
        "ok": True,
        "product": product,
        "interval": interval,
        "limit": 20,
        "turnover_unit": "USDT" if ranked else None,
        "rows": ranked,
        "unranked": blocked,
        "note": "只在同一产品类别、同一 USDT 成交额口径内取前 20。不足 20 个就显示实际数量。没有可靠成交额的不编排名。",
    }


def health():
    quotes = live_quotes() if feed_enabled() else {"badge": {"level": "down", "text": "行情流未启动"}, "feed": {"connected": False}}
    return {
        "ok": True,
        "name": "信号实验室 · 趋势判断工作台",
        "trading": False,
        "orders": False,
        "cloud_alerts": False,
        "monitor": runtime_status(),
        "channels": channel_config(),
        "feeds": {
            "btc_eth": "Bitget 官方 USDT 本位永续 WebSocket，REST 只作回补",
            "nvda": "未接通",
            "xau_usd": "未接通",
            "alpaca": "未购买、未授权",
        },
        "badge": quotes.get("badge"),
        "feed": quotes.get("feed"),
    }
