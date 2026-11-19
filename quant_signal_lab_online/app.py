import streamlit as st
import pandas as pd
import numpy as np
import logging

from market_data import INTERVALS, SYMBOLS, MarketDataError, closed_candles, fetch_candles

LOGGER = logging.getLogger(__name__)
st.set_page_config(page_title='Quant Signal Lab 3.0',layout='wide')
st.title('Quant Signal Lab 3.0 · 量化综合工作台')
st.caption('研究与模拟用途；无实盘下单功能。公开行情可能延迟或不可用。')
symbol=st.sidebar.selectbox('交易对',SYMBOLS)
interval=st.sidebar.selectbox('K线周期',list(INTERVALS),index=1)
capital=st.sidebar.number_input('模拟本金 USDT',100.,1000000.,1000.)
risk=st.sidebar.slider('单笔风险 %',0.1,2.0,0.5,0.1)
fee=st.sidebar.number_input('单边手续费 %',0.,1.,0.06,0.01)
slip=st.sidebar.number_input('单边滑点 %',0.,1.,0.03,0.01)
@st.cache_data(ttl=60)
def get_data(symbol, interval):
    return fetch_candles(symbol, interval)

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
def backtest(d):
    cash=capital;units=0.;entry=0.;stop=0.;target=0.;risk_budget=0.;trades=[];equity=[];wins=0;losses=0;halt=False;peak=capital
    for j in range(1,len(d)):
        row=d.iloc[j];prev=d.iloc[j-1];price=row.open
        if units:
            exit_price=None;why=''
            if row.low<=stop:exit_price=min(price,stop)*(1-slip/100);why='止损'
            elif row.high>=target:exit_price=target*(1-slip/100);why='止盈'
            elif prev.ema20<prev.ema50:exit_price=price*(1-slip/100);why='趋势退出'
            if exit_price is not None:
                gross=units*exit_price;exit_fee=gross*fee/100;cash+=gross-exit_fee
                pnl=(exit_price-entry)*units-(entry*units+gross)*fee/100
                trades.append({'时间':row.time,'退出原因':why,'盈亏 USDT':round(pnl,2),'权益 USDT':round(cash,2)})
                wins+=pnl>0;losses+=pnl<=0;units=0
                if cash<=capital*0.98 or losses>=3 and wins==0:halt=True
        if not units and not halt and bool(prev.signal) and np.isfinite(prev.atr) and prev.atr>0:
            buy=price*(1+slip/100);stop=buy-2*prev.atr;target=buy+4*prev.atr
            if stop>0:
                size=min(cash/(buy*(1+fee/100)),cash*risk/100/(buy-stop))
                if size>0:units=size;entry=buy;cash-=size*buy*(1+fee/100)
        eq=cash+units*row.close;peak=max(peak,eq);equity.append({'time':row.time,'equity':eq,'drawdown':eq/peak-1})
    return pd.DataFrame(equity),pd.DataFrame(trades)
try:
    data = calc(closed_candles(get_data(symbol, interval), interval))
    st.info('数据来自 Bitget 公开现货K线接口。仅使用已收盘且通过时间校验的K线。策略使用前一根K线的信号，并在下一根开盘模拟成交。')
    a,b,c=st.columns(3);a.metric('最近收盘价',f'{data.close.iloc[-1]:,.4f}');b.metric('EMA20',f'{data.ema20.iloc[-1]:,.4f}');c.metric('RSI14',f'{data.rsi.iloc[-1]:.1f}')
    st.subheader('价格及趋势指标');st.line_chart(data.set_index('time')[['close','ema20','ema50','boll_up','boll_low']])
    st.subheader('动量指标');st.line_chart(data.set_index('time')[['macd','macd_signal']]);st.line_chart(data.set_index('time')[['rsi']])
    st.subheader('历史模拟回测');st.caption('示例策略：EMA20上穿EMA50且RSI>50且MACD>信号线，下一根开盘做多；2ATR止损、4ATR止盈。现货单向，无杠杆。')
    curve,trades=backtest(data)
    if len(curve):
        st.line_chart(curve.set_index('time')['equity'])
        x,y,z=st.columns(3);x.metric('期末模拟权益',f'{curve.equity.iloc[-1]:,.2f} USDT');y.metric('最大回撤',f'{curve.drawdown.min()*100:.2f}%');z.metric('已平仓笔数',len(trades))
    st.dataframe(trades,use_container_width=True)
    st.download_button('导出K线 CSV',data.to_csv(index=False).encode(),'candles.csv','text/csv')
    if not trades.empty:st.download_button('导出交易记录 CSV',trades.to_csv(index=False).encode(),'trades.csv','text/csv')
    st.warning('这是演示级回测：仅约200根K线，未模拟资金费率、盘口深度、部分成交、交易所规则；末尾持仓按市价估值。不能据此推断策略盈利能力。')
except MarketDataError as exc:
    LOGGER.warning("行情加载失败 %s %s: %s", symbol, interval, exc)
    st.error(f"行情加载失败：{exc}。请稍后重试或检查 Bitget API 连通性。")
