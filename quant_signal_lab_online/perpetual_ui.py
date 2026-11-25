"""Read-only perpetual market workbench."""
import logging
import time

import pandas as pd
import streamlit as st

from charts import market_figure
from indicators import Parameters, calculate
from market_data import INTERVALS, MarketDataError, active_forming, capture_forming, closed_candles
from perpetual_market import (VIEWS, build_rows, fetch_candles, load_directory, load_tickers, number,
                             read_favorites, visible_rows)
from ui import layer_picker

LOG = logging.getLogger(__name__)
PAGE_SIZE = 50
CHART_INTERVALS = ("15m", "5m", "1H", "4H", "1D")


def show_number(value, spec=",.6g"):
    parsed = number(value)
    return "—" if parsed is None else format(parsed, spec)


@st.cache_data(ttl=40, max_entries=16, show_spinner="正在读取永续 K 线…")
def contract_candles(symbol, interval):
    raw = fetch_candles(symbol, interval)
    observed = pd.Timestamp.now(tz="UTC")
    closed = closed_candles(raw, interval, observed, min_bars=30)
    return closed, capture_forming(raw, interval, observed, closed)


def research_panel(symbol, row):
    st.markdown(f"**{symbol} 研究 K 线**")
    st.caption("最新价 K 线，不是标记价或指数价。未收盘 K 线不进入指标。本图不是交易信号，"
               "也不包含资金费或滑点。图中指标使用默认参数，只供观察。")
    if row["state"] != "正常":
        st.caption(f"报价状态：{row['state']}。标记价、指数价和基差不参与排行。")
    mark, index, basis, holding = st.columns(4)
    mark.metric("标记价", show_number(row["mark"]))
    index.metric("指数价", show_number(row["index"]))
    basis.metric("基差 %", show_number(row["basis"], ".3f"), help="（最新价 − 指数价）÷ 指数价 × 100")
    unit = row["base"] or "基础币"
    holding.metric("持仓量", show_number(row["holding"], ",.4f"), help=f"单位 {unit}，取自交易所当前持仓量")
    interval = st.selectbox("研究 K 线周期", CHART_INTERVALS, key="perp_interval")
    try:
        closed, captured = contract_candles(symbol, interval)
        now = pd.Timestamp.now(tz="UTC")
        closed = closed_candles(closed, interval, now, min_bars=30)
        forming = active_forming(captured, interval, now, closed.time.iloc[-1])
        data = calculate(closed)
    except MarketDataError as exc:
        LOG.warning("Perpetual candles %s %s: %s", symbol, interval, exc)
        st.warning(f"永续 K 线暂不可用：{exc}")
        return
    duration = INTERVALS[interval][1]
    st.caption(f"最近已收盘 {(data.time.iloc[-1] + duration):%Y-%m-%d %H:%M} UTC · "
               f"收盘 {data.close.iloc[-1]:,.5g}")
    if forming is not None:
        opened = pd.Timestamp(forming["time"])
        st.caption(f"未收盘 K 线 {opened:%Y-%m-%d %H:%M} UTC 开盘 · 最新价 {forming['close']:,.5g}")
    show = layer_picker("perp_layers")
    st.plotly_chart(market_figure(data.tail(250), Parameters(), forming=forming, show=show),
                    width="stretch", config={"displaylogo": False, "responsive": True, "scrollZoom": True})
    export = data.drop(columns=["signal"], errors="ignore").assign(close_time=data.time + duration)
    st.download_button("导出已收盘 K 线 CSV", export.to_csv(index=False), f"{symbol}-{interval}-closed.csv",
                       "text/csv", key=f"perp_csv_{symbol}_{interval}", help="只含已收盘 K 线。")


@st.cache_data(ttl=60, max_entries=1, show_spinner=False)
def directory():
    return load_directory()


@st.cache_data(ttl=20, max_entries=1, show_spinner=False)
def tickers():
    return load_tickers()


def save_favorites(favorites):
    if favorites:
        st.query_params["favorites"] = ",".join(favorites)
    elif "favorites" in st.query_params:
        del st.query_params["favorites"]


@st.fragment(run_every=30)
def perpetual_workspace():
    st.subheader("Bitget USDT 永续合约")
    st.caption("Bitget 官方公开 REST 快照 · 只读市场研究 · 24 小时振幅 =（最高价 ÷ 最低价 − 1）× 100%")
    try:
        specs = directory()
    except MarketDataError as exc:
        LOG.warning("Perpetual contract directory: %s", exc)
        st.error(f"合约目录不可用：{exc}")
        return
    try:
        quotes, received = tickers()
        stamp = pd.to_datetime(received, unit="ms", utc=True)
        st.caption(f"快照接收 {stamp:%H:%M:%S} UTC · {len(specs)} 个正常交易的合约 · 页面约每 30 秒更新")
    except MarketDataError as exc:
        LOG.warning("Perpetual ticker snapshots: %s", exc)
        quotes = []
        st.warning(f"报价暂不可用，仍可查看合约目录：{exc}")

    search, mode = st.columns([3, 2])
    query = search.text_input("搜索合约或基础币")
    view = mode.selectbox("市场列表", VIEWS)
    favorites = read_favorites(st.query_params.get("favorites", ""))
    with st.expander("流动性筛选"):
        min_turnover = st.number_input("最低 24h 成交额 USDT（0 为不限）", min_value=0, step=100000)
        max_spread = st.number_input("最大买卖价差 %（0 为不限）", min_value=0., step=.05)
    now = int(time.time() * 1000)
    listed = visible_rows(build_rows(specs, quotes), query, view, favorites, now, min_turnover, max_spread)

    if view == "我的自选":
        st.caption("自选保存在当前网址参数中。收藏或分享该网址即可保留列表。")
        missing = [symbol for symbol in favorites if symbol not in specs]
        if missing:
            st.caption("暂不开放的自选：" + "、".join(missing))
            if st.button("移除暂不开放的自选"):
                save_favorites([symbol for symbol in favorites if symbol in specs])
                st.rerun()
    if not listed:
        st.info("没有符合当前筛选条件的合约")
        return

    pages = (len(listed) + PAGE_SIZE - 1) // PAGE_SIZE
    page = st.selectbox("页码", range(1, pages + 1), disabled=pages == 1)
    subset = listed[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    st.caption(f"共 {len(listed)} 个匹配 · 第 {page}/{pages} 页")
    table = pd.DataFrame([{
        "合约": row["symbol"], "自选": "★" if row["symbol"] in favorites else "",
        "最新价 USDT": row["price"], "24h 涨跌 %": row["change"], "24h 成交额 USDT": row["turnover"],
        "资金费率 %": row["funding"], "24h 振幅 %": row["range_pct"], "价差 %": row["spread_pct"],
        "行情时间 UTC": pd.to_datetime(row["timestamp"], unit="ms", utc=True),
        "时差 s": (now - row["timestamp"]) / 1000 if row["timestamp"] else None,
        "行情状态": row["state"],
    } for row in subset])
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "24h 成交额 USDT": st.column_config.NumberColumn(format="compact"),
        "24h 涨跌 %": st.column_config.NumberColumn(format="%.2f"),
        "资金费率 %": st.column_config.NumberColumn(format="%.5f"),
        "24h 振幅 %": st.column_config.NumberColumn(format="%.2f"),
        "价差 %": st.column_config.NumberColumn(format="%.3f"),
        "时差 s": st.column_config.NumberColumn(format="%.1f"),
    })
    selected = st.selectbox("查看合约规格或编辑自选", [row["symbol"] for row in subset])
    if selected in favorites:
        if st.button("移出自选"):
            save_favorites([symbol for symbol in favorites if symbol != selected])
            st.rerun()
    elif st.button("加入自选", disabled=len(favorites) >= 50, help="最多保存 50 个合约"):
        save_favorites([*favorites, selected])
        st.rerun()
    spec = specs[selected]
    with st.expander(f"{selected} 合约规格"):
        st.write(f"合约状态：{spec.get('symbolStatus', '—')} · 保证金 USDT · 永续")
        fields = {"价格小数位": "pricePlace", "价格步进字段": "priceEndStep", "数量小数位": "volumePlace",
                  "数量步进": "sizeMultiplier", "最小交易数量": "minTradeNum", "最小名义额 USDT": "minTradeUSDT",
                  "资金费间隔（小时）": "fundInterval", "交易所最大杠杆（仅规格）": "maxLever"}
        st.dataframe(pd.DataFrame([{"规格": label, "数值": str(spec.get(key) or "—")}
                                  for label, key in fields.items()]), hide_index=True, width="stretch")
    research_panel(selected, next(item for item in subset if item["symbol"] == selected))
