"""Original EMA crossover demo strategy, retained for comparison."""
import numpy as np
import pandas as pd


def calc(d):
    d=d.copy()
    d['ema20']=d.close.ewm(span=20,adjust=False).mean();d['ema50']=d.close.ewm(span=50,adjust=False).mean()
    delta=d.close.diff();up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();down=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rsi = 100 - 100 / (1 + up / down.replace(0, np.nan))
    d['rsi'] = rsi.mask((down == 0) & (up > 0), 100).mask((down == 0) & (up == 0), 50)
    d['boll_mid']=d.close.rolling(20).mean();std=d.close.rolling(20).std();d['boll_up']=d.boll_mid+2*std;d['boll_low']=d.boll_mid-2*std
    macd=d.close.ewm(span=12,adjust=False).mean()-d.close.ewm(span=26,adjust=False).mean();d['macd']=macd;d['macd_signal']=macd.ewm(span=9,adjust=False).mean()
    tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1)
    d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    d['vol_avg']=d.volume.rolling(20).mean()
    d['signal']=(d.ema20>d.ema50)&(d.ema20.shift()<=d.ema50.shift())&(d.rsi>50)&(d.macd>d.macd_signal)
    return d

def backtest(data, capital, risk, fee, slip):
    cash, units, entry, stop, target = capital, 0., 0., 0., 0.
    trades, equity = [], []
    wins, losses, halted, peak = 0, 0, False, capital
    fee, slip, risk = fee / 100, slip / 100, risk / 100

    for index in range(1, len(data)):
        bar, previous = data.iloc[index], data.iloc[index - 1]
        was_open = units > 0
        if not was_open and not halted and previous.signal and np.isfinite(previous.atr) and previous.atr > 0:
            entry = bar.open * (1 + slip)
            stop, target = entry - 2 * previous.atr, entry + 4 * previous.atr
            if stop > 0:
                units = min(cash / (entry * (1 + fee)), cash * risk / (entry - stop))
                cash -= units * entry * (1 + fee)
        if units:
            exit_price, reason = None, ""
            if was_open and previous.ema20 < previous.ema50:
                exit_price, reason = bar.open, "趋势退出"
            elif bar.low <= stop:
                exit_price, reason = min(bar.open, stop), "止损"
            elif bar.high >= target:
                exit_price, reason = target, "止盈"
            if exit_price is not None:
                exit_price *= 1 - slip
                gross = units * exit_price
                cash += gross * (1 - fee)
                pnl = (exit_price - entry) * units - (entry * units + gross) * fee
                trades.append({"时间": bar.time, "退出原因": reason,
                               "盈亏 USDT": round(pnl, 2), "权益 USDT": round(cash, 2)})
                wins += pnl > 0
                losses += pnl <= 0
                units = 0
                halted = cash <= capital * .98 or (losses >= 3 and wins == 0)
        value = cash + units * bar.close
        peak = max(peak, value)
        equity.append({"time": bar.time, "equity": value, "drawdown": value / peak - 1})
    return pd.DataFrame(equity), pd.DataFrame(trades)
