"""P1 read-only perpetual market workbench plus BTC/ETH realtime quotes."""
import logging
import time

import pandas as pd
import streamlit as st

from backtest import Costs
from charts import market_figure
from indicators import calculate
from market_data import INTERVALS, MarketDataError, closed_candles
from perpetual_market import VIEWS, build_rows, load_directory, load_tickers, read_favorites, visible_rows
from realtime_ui import perpetual_quote_panel
from signals import aligned_frames, score_with_config
from strategy_modules import dynamic_levels, ema_boll_plan, environment, pullback_plan, shocks
from macro_fib import FibParams, MacroParams, analyze_macro, fibonacci_grid, load_macro_frames
from swap_data import CORE_SYMBOLS, PRODUCT_LABEL, load_swap_frames
from ui import DIRECTION

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


@st.cache_data(ttl=20, show_spinner=False, max_entries=8)
def swap_frames(symbol):
    return load_swap_frames(symbol)


@st.cache_data(ttl=900, show_spinner=False, max_entries=8)
def macro_frames(symbol):
    return load_macro_frames(symbol)


def core_workspace(params, rules, modules):
    snapshots = perpetual_quote_panel(CORE_SYMBOLS)
    official_ok = all(item["official_candidates_ok"] for item in snapshots)
    symbol = st.selectbox("核心资产（USDT 永续）", CORE_SYMBOLS, index=0)
    interval = st.selectbox("K 线周期", ("5m", "15m", "1H", "4H"), index=1, key="swap_interval")
    shortcut, intraday, ema_boll, pullback, dyn_stop = modules
    costs = Costs(80.0, 0.01, 0.0005, 0.0003)
    try:
        frames = swap_frames(symbol)
        now = pd.Timestamp.now(tz="UTC")
        frames = {key: closed_candles(value, key, now) for key, value in frames.items()}
        scored = score_with_config(aligned_frames(frames, params), rules, costs.fee, costs.slip,
                                   market_ok=official_ok, range_mode=st.session_state.get("applied_preset") == "震荡行情型")
        chart = frames[interval]
        data = calculate(chart, params)
    except MarketDataError as exc:
        st.error(f"{PRODUCT_LABEL} 行情不可用，正式候选已暂停。{exc}")
        return
    last = scored.iloc[-1]
    st.caption(f"{PRODUCT_LABEL} · 已收盘 {data.time.iloc[-1] + INTERVALS[interval][1]:%Y-%m-%d %H:%M UTC} · "
               f"收盘 {data.close.iloc[-1]:,.5g}")
    env = environment(data)
    st.info(f"环境识别：{env['regime']} · {env['reason']} · 建议研究 {env['suggestion']}（只建议，不自动切换）")
    macro_params = st.session_state.get("macro_params") or MacroParams()
    fib_params = st.session_state.get("fib_params") or FibParams()
    try:
        mframes = macro_frames(symbol)
        report = analyze_macro(mframes, macro_params)
        fib_frame = mframes.get(fib_params.interval, mframes["1D"])
        fib = fibonacci_grid(fib_frame, fib_params)
        st.session_state.macro_report = report
        st.session_state.fib_report = fib
        st.caption(
            "宏观周期："
            + " · ".join(
                part for part in (
                    f"日线 {report['daily']['stage']}" if "daily" in report else None,
                    f"周线 {report['weekly']['stage']}" if "weekly" in report else None,
                    f"月线 {report['monthly']['phase']}" if "monthly" in report else None,
                    f"季度 {report['quarterly']['phase']}" if "quarterly" in report else None,
                    f"年度 {report['yearly']['phase']}" if "yearly" in report else None,
                ) if part
            )
            + f" · {report['summary']} · 建议研究 {report['suggestion']}（只建议，不自动切换）"
        )
        if fib.get("ready"):
            st.caption(
                f"斐波那契（{fib['interval']}，摆动已确认于 {pd.Timestamp(fib['confirmed_time']).tz_convert('UTC'):%Y-%m-%d}）："
                f"{fib['direction']} {fib['start_price']:,.5g} → {fib['end_price']:,.5g} · 现价 {fib['close']:,.5g} · "
                f"最近水平 {fib['nearest']} @ {fib['nearest_price']:,.5g}"
            )
            with st.expander("斐波那契水平"):
                levels = {**{f"回撤 {ratio:g}": price for ratio, price in fib["retracements"].items()},
                          **{f"延伸 {ratio:g}": price for ratio, price in fib["extensions"].items()}}
                st.dataframe(pd.DataFrame({"水平": list(levels), "价格": list(levels.values())}),
                             hide_index=True, width="stretch")
        else:
            st.caption(f"斐波那契：{fib.get('reason', '尚未就绪')}")
    except MarketDataError as exc:
        st.caption(f"宏观周期或斐波那契暂不可用：{exc}")
    flags = shocks(frames, intraday)
    st.caption("异动：" + "；".join(f"{name} 波幅 {item['move']:.2%} / 阈值 {item['threshold']:.2%} · {item['volume_flag']}"
                                for name, item in flags.items()))
    left, right = st.columns([3, 2])
    with left:
        st.plotly_chart(market_figure(data.tail(250) if interval != "15m" else scored.tail(250), params, scored),
                        width="stretch", config={"displaylogo": False, "responsive": True, "scrollZoom": True})
    with right:
        st.subheader("多周期信号")
        st.write(f"做多 **{last.score_long}/100** · 做空 **{last.score_short}/100**")
        st.caption(f"4小时 {DIRECTION.get(last.direction_4H, '缺失')} · 1小时 {DIRECTION.get(last.direction_1H, '缺失')}")
        if not official_ok:
            st.warning("正式候选已暂停，待实时行情恢复后重新校验。盘中临时预警可单独显示。")
        elif last.get("alert"):
            st.info(str(last.alert) + "（非正式候选，默认仍需收盘确认）")
        if not last.side:
            st.info("观望，或条件未满足。")
        else:
            side = "long" if last.side == 1 else "short"
            plan = dynamic_levels(last, dyn_stop, int(last.side), float(last.close),
                                  float(last[f"stop_{side}"]), float(last[f"target1_{side}"]),
                                  float(last[f"target2_{side}"]), float(last.atr))
            st.markdown(f"**{'做多' if last.side == 1 else '做空'} · {last.grade} · {plan['scheme']}**")
            st.write(f"止损 {plan['stop']:,.6g} · 止盈一 {plan['target1']:,.6g} · 止盈二 {plan['target2']:,.6g}")
        plan_eb = ema_boll_plan(frames["15m"], ema_boll, params)
        if plan_eb:
            st.caption(f"EMA×布林：方向 {plan_eb['side']} · 入场 {plan_eb.get('entry', '—')} · 止损 {plan_eb.get('stop', '—')}")
        plan_pb = pullback_plan(frames, pullback, params)
        if plan_pb and plan_pb["entries"]:
            for item in plan_pb["entries"]:
                st.caption(f"回踩：{item['label']} · 参考入场 {item['entry']:,.6g} · RSI {item['rsi']:.1f}")
        elif plan_pb:
            st.caption("回踩：当前无符合条件的入场价。")
    if st.button("校验行情并刷新已收盘数据"):
        swap_frames.clear()
        st.rerun()


@st.fragment(run_every=30)
def perpetual_workspace(params=None, rules=None, modules=None):
    if params is not None:
        core_workspace(params, rules, modules)
        st.divider()
    st.subheader("Bitget USDT 永续合约目录")
    st.caption("Bitget 官方公开 REST 快照 · 只读市场研究 · 与上方核心实时行情分开标注 · 24 小时振幅 =（最高价 ÷ 最低价 − 1）× 100%")
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
