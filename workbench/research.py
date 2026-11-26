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
from threading import Lock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

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
from swap_data import MIX_GRANULARITY, PRODUCT, PRODUCT_LABEL, ROOT, fetch_swap_candles

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
_BOARD = {"at": 0.0, "rows": None, "error": None}
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
    for symbol in ("BTCUSDT", "ETHUSDT"):
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
        for symbol in ("BTCUSDT", "ETHUSDT"):
            symbols[symbol] = _blank_snap(symbol, "断线", "行情流未启动")
    else:
        try:
            hub = get_feed()
            connected = bool(hub and hub.connected)
            for symbol in ("BTCUSDT", "ETHUSDT"):
                symbols[symbol] = _public_snap(hub.snapshot(symbol))
        except (MarketDataError, OSError, RuntimeError) as exc:
            error = str(exc)
            for symbol in ("BTCUSDT", "ETHUSDT"):
                symbols[symbol] = _blank_snap(symbol, "断线", error)
    payload = {
        "server_ms": now_ms(),
        "feed": {"connected": connected, "error": error, "product": PRODUCT_LABEL},
        "symbols": symbols,
        "badge": badge_for(symbols.get("BTCUSDT"), symbols.get("ETHUSDT")),
    }
    return payload


def core_assets(quotes=None):
    quotes = quotes or live_quotes()
    symbols = quotes["symbols"]
    return {
        "assets": [
            {
                "id": "BTC",
                "name": "比特币",
                "symbol": "BTCUSDT",
                "product_class": "crypto_perp",
                "product_label": "Bitget USDT 本位永续",
                "connected": True,
                "quote": symbols.get("BTCUSDT"),
                "reason": None,
            },
            {
                "id": "ETH",
                "name": "以太坊",
                "symbol": "ETHUSDT",
                "product_class": "crypto_perp",
                "product_label": "Bitget USDT 本位永续",
                "connected": True,
                "quote": symbols.get("ETHUSDT"),
                "reason": None,
            },
            {
                "id": "NVDA",
                "name": "英伟达",
                "symbol": "NVDA",
                "product_class": "cash_equity",
                "product_label": "美股正股",
                "connected": False,
                "quote": None,
                "reason": BLOCKED["NVDA"],
            },
            {
                "id": "XAU",
                "name": "现货黄金",
                "symbol": "XAU/USD",
                "product_class": "spot_gold",
                "product_label": "现货黄金 XAU/USD",
                "connected": False,
                "quote": None,
                "reason": BLOCKED["XAUUSD"],
            },
        ],
        "badge": quotes["badge"],
        "server_ms": quotes["server_ms"],
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
        if symbol in {"BTCUSDT", "ETHUSDT"} and interval in MIX_GRANULARITY:
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
    if symbol in {"BTCUSDT", "ETHUSDT"}:
        label, product_class = PRODUCT_LABEL, "crypto_perp"
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
        "warning": "最后一根若标记为未收盘，只供观察，不能当作正式信号。" if forming is not None else "",
        "banner": None if product_class == "crypto_perp" else f"图上是 {label}。它不是正股、现货黄金或 CFD。",
    }


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


def radar(symbol="BTCUSDT", settings=None):
    symbol = resolve_symbol(symbol)
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise WorkbenchError("正式评分只对已接通的 BTCUSDT 与 ETHUSDT 永续计算，不为其他产品编造分数。")
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
        "direction_4h": "多" if direction == 1 else "空" if direction == -1 else "中性",
        "reasons_long": None if pd.isna(getattr(last, "reasons_long", None)) else str(last.reasons_long),
        "reasons_short": None if pd.isna(getattr(last, "reasons_short", None)) else str(last.reasons_short),
        "time": beijing(last.time),
        "history": history,
        "environment": json_ready(regime),
        "shocks": json_ready(shock),
        "ema_boll": json_ready(cross) if cross else {"enabled": False, "note": "独立 EMA×布林未启用"},
        "pullback": json_ready(pull) if pull else {"enabled": False, "note": "多周期回踩未启用"},
        "plan": json_ready(plan),
        "weights_note": "评分函数使用固定权重 30/20/20/15/15。设置页里的权重会保存，但目前不会改写这个分数。",
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
        rows.append({
            "address": item.get("ethAddress"),
            "name": item.get("displayName") or "",
            "account_value": float(item["accountValue"]) if item.get("accountValue") not in (None, "") else None,
            "day_pnl": num("day", "pnl"),
            "day_roi": num("day", "roi"),
            "week_pnl": num("week", "pnl"),
            "week_roi": num("week", "roi"),
            "month_pnl": num("month", "pnl"),
            "month_roi": num("month", "roi"),
        })
    key = {"account": "account_value", "day": "day_roi", "week": "week_roi", "month": "month_roi"}.get(sort, "account_value")
    reverse = direction != "asc"
    rows.sort(key=lambda row: ((row.get(key) is None), -(row.get(key) or 0) if reverse else (row.get(key) or 0)))
    if not reverse:
        rows.sort(key=lambda row: ((row.get(key) is None), row.get(key) or 0))
    return rows[: max(1, min(int(limit), 50))]


def monitor(sort="account", limit=30, direction="desc"):
    now = time.time()
    with _LOCK:
        cached = _BOARD["rows"] if now - _BOARD["at"] < 60 else None
        error = _BOARD["error"]
    if cached is None:
        try:
            response = requests.get(BOARD_URL, timeout=(4, 20), headers={"User-Agent": "quant-signal-lab-research"})
            response.raise_for_status()
            cached = response.json()
            error = None
            with _LOCK:
                _BOARD.update(at=time.time(), rows=cached, error=None)
        except (requests.RequestException, ValueError) as exc:
            error = f"Hyperliquid 公开排行榜暂时取不到：{type(exc).__name__}"
            if cached is None:
                return {"ok": False, "error": error, "rows": [],
                        "notice": "这里只读公开数据，不是你的交易所账户，也不能下单。"}
    return {
        "ok": error is None,
        "error": error,
        "source": "Hyperliquid 公开排行榜 stats-data.hyperliquid.xyz",
        "notice": "只监控公开地址和公开排行榜，不是你的实盘账户，没有下单，也不需要交易密钥。",
        "rows": parse_leaderboard(cached, sort, limit, direction),
        "matched": len((cached or {}).get("leaderboardRows") or []),
    }


def public_wallet(address):
    if not isinstance(address, str) or len(address) != 42 or not address.startswith("0x"):
        raise WorkbenchError("请输入 0x 开头的 40 位公开地址。不要填写私钥或 API Key。")
    try:
        int(address[2:], 16)
    except ValueError as exc:
        raise WorkbenchError("地址格式无效") from exc
    try:
        response = requests.post(HL_INFO, json={"type": "clearinghouseState", "user": address}, timeout=(4, 12),
                                 headers={"User-Agent": "quant-signal-lab-research"})
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MarketDataError(f"公开账户暂时读不到：{type(exc).__name__}") from exc
    margin = payload.get("marginSummary") or {}
    positions = []
    for item in payload.get("assetPositions") or []:
        position = item.get("position") or {}
        positions.append({
            "coin": position.get("coin"),
            "size": position.get("szi"),
            "entry": position.get("entryPx"),
            "unrealized": position.get("unrealizedPnl"),
            "leverage": (position.get("leverage") or {}).get("value"),
        })
    return {
        "ok": True,
        "address": address,
        "account_value": margin.get("accountValue"),
        "withdrawable": payload.get("withdrawable"),
        "positions": positions[:20],
        "notice": "这是 Hyperliquid 上该地址的公开持仓快照，不是本网站用户的交易账户。",
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


def health():
    quotes = live_quotes() if feed_enabled() else {"badge": {"level": "down", "text": "行情流未启动"}, "feed": {"connected": False}}
    return {
        "ok": True,
        "name": "信号实验室 · 趋势判断工作台",
        "trading": False,
        "orders": False,
        "cloud_alerts": False,
        "feeds": {
            "btc_eth": "Bitget 官方 USDT 本位永续 WebSocket，REST 只作回补",
            "nvda": "未接通",
            "xau_usd": "未接通",
            "alpaca": "未购买、未授权",
        },
        "badge": quotes.get("badge"),
        "feed": quotes.get("feed"),
    }
