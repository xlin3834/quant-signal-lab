"""Persistent, single-market simulated trading worker. No exchange order access."""
import argparse
import json
import logging
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone

import pandas as pd

from backtest import Costs, mark, new_position, process_bar
from market_data import INTERVALS, MarketDataError, SYMBOLS, fetch_history, load_frames
from signals import Rules, aligned_frames, make_plan, score_frames

LOGGER = logging.getLogger(__name__)
COSTS = Costs()
DAY_LOSS = .02
MAX_LOSSES = 3
BAR = INTERVALS["15m"][1]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        LOGGER.warning("SQLite WAL 不可用", exc_info=True)
    return conn


@contextmanager
def transaction(path):
    conn = connect(path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS equity (time TEXT PRIMARY KEY, value REAL NOT NULL)")
            yield conn
    finally:
        conn.close()


def read_account(conn):
    row = conn.execute("SELECT state FROM account WHERE id=1").fetchone()
    return json.loads(row[0]) if row else {
        "cash": COSTS.capital, "equity": COSTS.capital, "last_bar": None, "position": None,
        "pending": None, "losses": 0, "day": None, "day_start": COSTS.capital, "day_halt": False,
        "mode": "running", "heartbeat": None, "error": None, "symbol": None,
    }


def save_account(conn, state):
    conn.execute("INSERT OR REPLACE INTO account VALUES (1, ?)", (json.dumps(state, allow_nan=False),))


def control(path, action):
    with transaction(path) as conn:
        state = read_account(conn)
        if action in ("pause", "stop"):
            state["mode"] = action
            state["pending"] = None
        elif action == "resume":
            state["mode"] = "running"
        elif action == "reset-streak":
            if state["mode"] == "running":
                raise ValueError("先暂停，再重置连续亏损熔断")
            state["losses"] = 0
        save_account(conn, state)


def record_failure(path, exc):
    """Remember the poll error without dropping a plan, position, or saved progress."""
    with transaction(path) as conn:
        state = read_account(conn)
        state.update(heartbeat=utc_now(), error=str(exc)[:500])
        save_account(conn, state)
    return state


def snapshot(path):
    with closing(connect(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='account'").fetchone():
            return None, [], []
        conn.execute("BEGIN")
        state = read_account(conn)
        trades = [json.loads(row[0]) for row in conn.execute("SELECT data FROM trades ORDER BY rowid DESC LIMIT 100")]
        equity = conn.execute("SELECT * FROM equity ORDER BY time DESC LIMIT 2000").fetchall()[::-1]
    return state, trades, equity


def advance(path, symbol, data, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    newest = data.time.iloc[-1]
    if not newest + BAR <= now < newest + 2 * BAR + pd.Timedelta(seconds=60):
        raise MarketDataError("模拟交易行情尚未收盘或已过期")
    with transaction(path) as conn:
        state = read_account(conn)
        if state["symbol"] not in (None, symbol):
            raise ValueError("该模拟账户已绑定其他交易对")
        state["symbol"] = symbol
        previous = pd.Timestamp(state["last_bar"]) if state["last_bar"] else None
        missed = data[data.time > previous] if previous is not None else data.tail(1)
        if ((previous is not None and not missed.empty and missed.time.iloc[0] != previous + BAR)
                or not missed.time.diff().iloc[1:].eq(BAR).all()):
            raise MarketDataError("模拟交易历史缺失，保留仓位并等待补齐")
        for _, bar in missed.iterrows():
            _apply_bar(conn, state, bar, bar.time == newest, now)
        state["heartbeat"], state["error"] = now.isoformat(), None
        save_account(conn, state)
    return state


def _apply_bar(conn, state, bar, latest, now):
    stamp = bar.time.isoformat()
    day = bar.time.date().isoformat()
    if day != state["day"]:
        state.update(day=day, day_start=state["equity"], day_halt=False)
    can_open = (state["mode"] == "running" and not state["day_halt"]
                and state["losses"] < MAX_LOSSES and latest)
    pending = state["pending"]
    if state["position"] is None and can_open and pending and stamp == pending["open_time"]:
        state["position"] = new_position(pending["plan"], bar.open, state["cash"], COSTS, Rules().minimum_rr)
    if pending and stamp >= pending["open_time"]:
        state["pending"] = None

    position = state["position"]
    if position is not None:
        fills, state["position"] = process_bar(position, bar, COSTS)
        net = sum(fill["pnl"] for fill in fills)
        state["cash"] += net
        for fill in fills:
            fill["time"] = stamp
            fill["symbol"] = state["symbol"]
            conn.execute("INSERT INTO trades VALUES (?, ?)",
                         (f"{stamp}:{fill['reason']}", json.dumps(fill, allow_nan=False)))
        if state["position"] is None:
            state["losses"] = state["losses"] + 1 if position["realized"] + net < 0 else 0

    state["equity"] = mark(state["position"], bar.close, state["cash"], COSTS)
    state["day_halt"] = state["day_halt"] or state["equity"] <= state["day_start"] * (1 - DAY_LOSS)
    state["last_bar"] = stamp
    if (state["position"] is None and can_open and state["losses"] < MAX_LOSSES
            and not state["day_halt"] and state["pending"] is None and bar.side):
        # Schedule a strictly future open relative to when the worker saw the signal.
        open_time = now.floor("15min") + BAR
        state["pending"] = {"open_time": open_time.isoformat(), "plan": make_plan(bar)}
    conn.execute("INSERT INTO equity VALUES (?, ?)", (stamp, state["equity"]))


def tick(path, symbol):
    state, _, _ = snapshot(path) if os.path.exists(path) else (None, [], [])
    frames = load_frames(symbol)
    base = score_frames(aligned_frames(frames))
    if state and state["last_bar"]:
        last = pd.Timestamp(state["last_bar"])
        if base.time.iloc[0] > last + BAR:
            count = int((base.time.iloc[-1] - last) / BAR) + 1
            if count > 5000:
                raise MarketDataError("停机超过 5000 根 K 线，需人工处置模拟账户")
            older = fetch_history(symbol, "15m", count)
            # Historical replay manages existing risk only; current scores come from fresh frames.
            records = older[older.time < base.time.iloc[0]].assign(side=0)
            base = pd.concat([records, base], ignore_index=True)
    advance(path, symbol, base)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("QSL_PAPER_DB", "paper.sqlite3"))
    parser.add_argument("--symbol", choices=SYMBOLS, default=SYMBOLS[0])
    parser.add_argument("action", choices=("run", "status", "pause", "stop", "resume", "reset-streak"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.action == "status":
        print(json.dumps(snapshot(args.db)[0], indent=2, ensure_ascii=False))
    elif args.action != "run":
        control(args.db, args.action)
    else:
        while True:
            try:
                tick(args.db, args.symbol)
            except Exception as exc:
                LOGGER.exception("模拟服务异常")
                try:
                    record_failure(args.db, exc)
                except Exception:
                    LOGGER.exception("无法记录模拟服务异常")
            time.sleep(30)


if __name__ == "__main__":
    main()
