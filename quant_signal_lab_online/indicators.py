"""Causal indicators evaluated on completed candles."""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Parameters:
    ema: tuple = (7, 20, 50, 200)
    boll: int = 20
    rsi: int = 14
    macd: tuple = (12, 26, 9)
    atr: int = 14
    volume: int = 20
    structure: int = 20
    boll_mult: float = 2.0


def warmup_bars(p):
    return max(2 * max(p.ema), 4 * p.rsi + 1, 4 * p.atr,
               2 * sum(p.macd), p.boll, p.volume, 4 * p.structure + 1)


def _exponential(series, alpha, window):
    # A fixed historical seed makes completed values independent of the fetch window.
    if len(series) < window:
        return pd.Series(np.nan, index=series.index)
    decay = 1 - alpha
    weights = alpha * decay ** np.arange(window)
    weights[-1] = decay ** (window - 1)
    values = np.convolve(series.to_numpy(dtype=float), weights, mode="valid")
    return pd.Series(np.concatenate((np.full(window - 1, np.nan), values)), index=series.index)


def _ema(series, length):
    return _exponential(series, 2 / (length + 1), 2 * length)


def calculate(data, p=Parameters()):
    d = data.copy()
    close = d.close
    for label, length in zip((7, 20, 50, 200), p.ema):
        d[f"ema{label}"] = _ema(close, length)
    delta = close.diff()
    up = _exponential(delta.clip(lower=0), 1 / p.rsi, 4 * p.rsi)
    down = _exponential(-delta.clip(upper=0), 1 / p.rsi, 4 * p.rsi)
    d["rsi"] = (100 * up / (up + down)).mask((up == 0) & (down == 0), 50)
    d["boll_mid"] = close.rolling(p.boll).mean()
    spread = p.boll_mult * close.rolling(p.boll).std()
    d["boll_up"], d["boll_low"] = d.boll_mid + spread, d.boll_mid - spread
    fast, slow, signal = p.macd
    d["macd"] = _ema(close, fast) - _ema(close, slow)
    d["macd_signal"] = _ema(d.macd, signal)
    previous = close.shift()
    tr = pd.concat([d.high - d.low, (d.high - previous).abs(), (d.low - previous).abs()], axis=1)
    d["atr"] = _exponential(tr.max(axis=1), 1 / p.atr, 4 * p.atr)
    d["vol_avg"] = d.volume.rolling(p.volume).mean()
    d["support"] = d.low.shift().rolling(p.structure).min()
    d["resistance"] = d.high.shift().rolling(p.structure).max()
    d["signal"] = ((d.ema20 > d.ema50) & (d.ema20.shift() <= d.ema50.shift())
                   & (d.rsi > 50) & (d.macd > d.macd_signal))
    return d
