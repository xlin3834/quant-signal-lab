"""Compare Bitget spot candles and history-candles for a common closed window."""
import argparse
from pathlib import Path

import pandas as pd

from comparison import compare
from market_data import INTERVALS, SYMBOLS, URL, MarketDataError, closed_candles, fetch_candles, parse_candles, request_data


def compare_official(symbol, interval, count=100):
    granularity, duration = INTERVALS[interval]
    recent = closed_candles(fetch_candles(symbol, interval, count + 4), interval, min_bars=count)
    recent = recent.tail(count).copy()
    recent["close_time"] = recent.time + duration
    end = recent.close_time.iloc[-1].value // 1_000_000
    rows = request_data(URL.replace("/candles", "/history-candles"),
                        {"symbol": symbol, "granularity": granularity,
                         "limit": str(count + 2), "endTime": str(end)})
    older = parse_candles(rows)
    older = older[older.time.between(recent.time.iloc[0], recent.time.iloc[-1])].copy()
    older["close_time"] = older.time + duration
    return compare(recent, older, compare_indicators=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    differences = []
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            try:
                count, report = compare_official(symbol, interval)
                print(f"{symbol:8} {interval:>3} | matched={count:3d} | differences={len(report)}")
                if not report.empty:
                    differences.append(report.assign(symbol=symbol, interval=interval))
            except MarketDataError as exc:
                print(f"{symbol:8} {interval:>3} | ERROR {exc}")
                differences.append(pd.DataFrame([{"symbol": symbol, "interval": interval, "原因": str(exc)}]))
    if args.output:
        output = pd.concat(differences, ignore_index=True) if differences else pd.DataFrame(
            columns=["symbol", "interval", "时间 UTC", "字段", "本地", "参考", "原因"])
        output.to_csv(args.output, index=False)
    return int(bool(differences))


if __name__ == "__main__":
    raise SystemExit(main())
