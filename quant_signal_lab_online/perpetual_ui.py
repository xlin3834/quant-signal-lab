"""P1 read-only perpetual market workbench."""
import logging
import time

import pandas as pd
import streamlit as st

from market_data import MarketDataError
from perpetual_market import VIEWS, build_rows, load_directory, load_tickers, read_favorites, visible_rows

LOG = logging.getLogger(__name__)
PAGE_SIZE = 50


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
