"""Streamlit views for market research and simulated trading."""
import logging
import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from backtest import Costs, evaluate
from charts import market_figure
from comparison import compare
from indicators import Parameters, calculate, warmup_bars
from legacy import backtest as legacy_backtest
from legacy import calc as legacy_calculate
from market_data import INTERVALS, MarketDataError, closed_candles, fetch_candles, fetch_tickers, load_frames
from mean_reversion import score_mean_reversion
from paper import snapshot
from signals import WEIGHTS, aligned_frames, score_frames

LOGGER = logging.getLogger(__name__)
DIRECTION = {1: "多", -1: "空", 0: "中性"}


@st.cache_data(ttl=40, show_spinner=False, max_entries=32)
def recent(symbol):
    return load_frames(symbol)


@st.cache_data(ttl=40, show_spinner=False, max_entries=32)
def chart_data(symbol, interval):
    return fetch_candles(symbol, interval, 950)


@st.cache_data(ttl=40, show_spinner=False, max_entries=2)
def tickers():
    return fetch_tickers()


@st.cache_data(ttl=600, show_spinner=False, max_entries=8)
def history(symbol, count, params):
    warmup = warmup_bars(params)
    base_count = count + warmup
    frames = load_frames(symbol, {"15m": base_count, "5m": base_count * 3 + 3,
                                  "1H": base_count // 4 + warmup + 2,
                                  "4H": base_count // 16 + warmup + 2})
    return aligned_frames(frames, params).tail(count)


def input_parameters():
    with st.sidebar.expander("指标参数"):
        ema = tuple(st.number_input(f"EMA {n} 周期", 2, 350, n) for n in (7, 20, 50, 200))
        boll = st.number_input("BOLL 周期", 2, 150, 20)
        rsi = st.number_input("RSI 周期", 2, 150, 14)
        macd = tuple(st.number_input(label, 2, 150, n) for label, n in
                     (("MACD 快线", 12), ("MACD 慢线", 26), ("MACD 信号线", 9)))
        atr = st.number_input("ATR 周期", 2, 150, 14)
        volume = st.number_input("均量周期", 2, 150, 20)
        structure = st.number_input("支撑／压力回溯周期", 5, 90, 20)
    return Parameters(ema, boll, rsi, macd, atr, volume, structure)


def market_list(sort):
    st.subheader("市场")
    try:
        table = tickers()
        now = pd.Timestamp.now(tz="UTC")
        if not (now - table["时间 UTC"]).between(pd.Timedelta(seconds=-5), pd.Timedelta(minutes=2)).all():
            raise MarketDataError("行情列表时间戳过期或异常")
        st.dataframe(table.sort_values(sort, ascending=False).drop(columns="时间 UTC"),
                     hide_index=True, width="stretch",
                     column_config={"24h 成交额 USDT": st.column_config.NumberColumn("24h 成交额", format="compact"),
                                    "24h 涨跌 %": st.column_config.NumberColumn("24h 涨跌 %", format="%.2f")})
    except MarketDataError as exc:
        LOGGER.warning("Ticker list: %s", exc)
        st.warning(str(exc))


def signal_panel(last, costs, rules, model="趋势评分", weights=None):
    st.subheader("多周期信号" if model != "均值回归" else "均值回归信号")
    st.caption(f"4小时 {DIRECTION.get(last.direction_4H, '缺失')} · "
               f"1小时 {DIRECTION.get(last.direction_1H, '缺失')} · 5分钟 RSI {last.rsi_5m:.1f}")
    st.write(f"做多 **{last.score_long}/100** · 做空 **{last.score_short}/100**")
    if model == "均值回归":
        st.caption("回归条件只看 15 分钟已收盘的布林带和 RSI。上方周期只作背景，不参与这条规则。")
    else:
        st.caption("评分表示规则满足程度。15 分钟收盘触发。")
    st.caption(last.decision_time.strftime("%Y-%m-%d %H:%M UTC"))
    with st.expander("评分依据", expanded=True):
        st.write(f"做多 · {last.reasons_long or '无'}")
        st.write(f"做空 · {last.reasons_short or '无'}")
        if model == "均值回归":
            st.caption("止损使用 ATR 倍数，两段目标使用布林中轨和外轨。模拟服务不使用这条规则。")
        else:
            shown = weights or WEIGHTS
            st.caption(" · ".join(f"{name} {shown[name]}" for name in WEIGHTS))
    if not last.side:
        st.info(f"观望 · 需方向一致、评分达标且结构位盈亏比 ≥ {rules.minimum_rr:.2f}")
        return
    side = "long" if last.side == 1 else "short"
    st.markdown(f"**{'做多' if last.side == 1 else '做空'} · {last.grade}**")
    price = lambda field: f"{last[f'{field}_{side}']:,.6g}"
    st.write(f"参考入场 {last.close:,.6g} · 止损 {price('stop')}")
    st.write(f"止盈一 {price('target1')} · 止盈二 {price('target2')}")
    st.write(f"目标一净盈亏比 {price('rr')} · 模拟风险预算 {costs.capital * costs.risk:,.2f} USDT")
    st.caption("触及止损、行情过期或开盘计入成本后盈亏比不足时失效。")
    st.caption("当前候选尚未经单独验证。策略样本内外结果见回测。")


@st.fragment(run_every=45)
def market_workspace(symbol, interval, sort, params, rules, costs, strategy=None):
    try:
        now = pd.Timestamp.now(tz="UTC")
        frames = recent(symbol)
        # Validate cached entries on every request. Use a fresh clock after network calls.
        now = max(now, pd.Timestamp.now(tz="UTC"))
        frames = {key: closed_candles(value, key, now) for key, value in frames.items()}
        aligned = aligned_frames(frames, params)
        if strategy is not None and strategy.style == "mean_reversion":
            ready = score_mean_reversion(aligned, rules, costs.fee, costs.slip, strategy)
            model = "均值回归"
            weight_map = None
        else:
            weight_map = None if strategy is None else dict(zip(WEIGHTS, strategy.weights))
            ready = score_frames(aligned, rules, costs.fee, costs.slip, weight_map)
            model = "趋势评分"
        chart = frames[interval] if interval in frames else closed_candles(chart_data(symbol, interval), interval)
        data = calculate(chart, params)
    except MarketDataError as exc:
        LOGGER.warning("行情不可用 %s %s: %s", symbol, interval, exc)
        st.error(f"Bitget 行情不可用，信号已暂停。{exc}")
        return
    last = ready.iloc[-1]
    st.caption(f"● Bitget 公开现货 · 最近已收盘 "
               f"{(data.time.iloc[-1] + INTERVALS[interval][1]):%Y-%m-%d %H:%M UTC} "
               f"· 收盘 {data.close.iloc[-1]:,.5g} · ATR {data.atr.iloc[-1]:,.5g}")
    left, center, right = st.columns([1.6, 4.4, 2])
    with left:
        market_list(sort)
    with center:
        if interval == "15m":
            data = ready
        st.plotly_chart(market_figure(data.tail(250), params, ready), width="stretch",
                        config={"displaylogo": False, "responsive": True, "scrollZoom": True})
        data = data.assign(close_time=data.time + INTERVALS[interval][1])
        st.download_button("导出 K 线 CSV", data.to_csv(index=False), "candles.csv", "text/csv")
        with st.expander("行情一致性报告"):
            st.caption("上传与此品种、周期及成交量单位一致的参考 CSV。请使用带时区的开盘和收盘时间。")
            file = st.file_uploader("参考 K 线 CSV", type="csv")
            if file is not None:
                try:
                    matched, report = compare(data, pd.read_csv(file), params)
                except (ValueError, TypeError, pd.errors.ParserError) as exc:
                    st.error(f"无法比较参考行情：{exc}")
                else:
                    st.write(f"时间匹配 {matched} 根 K 线 · {len(report)} 项差异")
                    st.dataframe(report, hide_index=True, width="stretch")
                    st.download_button("下载差异 CSV", report.to_csv(index=False), "differences.csv", "text/csv")
    with right:
        signal_panel(last, costs, rules, model, weight_map)


def backtest_panel(symbol, interval, params, rules, costs, strategy=None):
    st.subheader("历史模拟回测")
    modes = ["多周期评分", "原版 EMA 交叉"]
    if strategy is not None and strategy.style == "mean_reversion":
        modes = ["均值回归（研究）", *modes]
    mode = st.radio("策略", modes, horizontal=True)
    if mode == "原版 EMA 交叉":
        try:
            bars = closed_candles(chart_data(symbol, interval), interval).tail(199)
        except MarketDataError as exc:
            st.error(str(exc))
            return
        curve, trades = legacy_backtest(legacy_calculate(bars), costs.capital, costs.risk * 100,
                                        costs.fee * 100, costs.slip * 100)
        st.caption("原版演示策略使用前根 EMA/RSI/MACD 信号，下一根开盘模拟。"
                   "使用原版指标周期，末尾持仓按市价估值。")
        st.line_chart(curve.set_index("time").equity)
        st.write(f"期末权益 {curve.equity.iloc[-1]:,.2f} USDT · "
                 f"最大回撤 {curve.drawdown.min():.2%} · 已平仓 {len(trades)} 笔")
        st.dataframe(trades, width="stretch")
        if not trades.empty:
            st.download_button("导出原版交易记录 CSV", trades.to_csv(index=False), "legacy-trades.csv", "text/csv")
        return
    count = st.selectbox("15 分钟历史 K 线", (3000, 4000, 5000))
    optimize = st.checkbox("仅在样本内比较候选评分阈值", disabled=mode == "均值回归（研究）")
    if mode == "均值回归（研究）":
        optimize = False
        st.caption("均值回归不搜索评分阈值。样本仍按时间 70/30 切开，指标只用当时及以前的已收盘 K 线。")

    def score_fn(frame, selected, fee, slip):
        if mode == "均值回归（研究）":
            return score_mean_reversion(frame, selected, fee, slip, strategy)
        weights = None if strategy is None else dict(zip(WEIGHTS, strategy.weights))
        return score_frames(frame, selected, fee, slip, weights)

    key = (symbol, count, params, rules, costs, optimize, mode, strategy)
    if st.button("运行回测", type="primary"):
        st.session_state.pop("evaluation", None)
        try:
            with st.spinner("正在从 Bitget 分页加载历史 K 线并核对时间戳…"):
                st.session_state.evaluation = (key, evaluate(
                    history(symbol, count, params), costs, rules, optimize, score_fn))
        except MarketDataError as exc:
            LOGGER.warning("历史分页 %s: %s", symbol, exc)
            st.error(f"回测数据不可用。{exc}")
    if "evaluation" not in st.session_state or st.session_state.evaluation[0] != key:
        return
    threshold, training, testing = st.session_state.evaluation[1]
    st.caption(f"评分阈值 {threshold} · 时间顺序切分 70% / 30%，两段独立本金。"
               "下一根开盘成交，同根同时触发时先止损，跳空止损按较差开盘价。"
               "止盈一平一半，止盈二平余量，期末按含退出成本的市价估值。")
    for name, result in (("样本内", training), ("样本外", testing)):
        stats, curve, trades = result
        st.markdown(f"#### {name}")
        st.caption(f"{curve.time.iloc[0]:%Y-%m-%d %H:%M} 至 {curve.time.iloc[-1]:%Y-%m-%d %H:%M} UTC")
        st.dataframe(pd.DataFrame([stats]), hide_index=True, width="stretch")
        st.line_chart(curve.set_index("time").equity)
        st.line_chart(curve.set_index("time").units)
        st.dataframe(trades, hide_index=True, width="stretch")
        st.download_button(f"导出{name}交易记录 CSV", trades.to_csv(index=False),
                           f"{name}-trades.csv", "text/csv")
    st.caption("样本外结果仅是历史模拟，阈值比较可能过拟合。")


@st.fragment(run_every=45)
def paper_panel():
    st.subheader("独立模拟服务")
    path = os.environ.get("QSL_PAPER_DB")
    if not path or not Path(path).is_file():
        st.info("未连接独立后台。Streamlit 页面只读展示状态，持续模拟需独立进程和持久磁盘。")
        return
    try:
        state, trades, equity = snapshot(path)
    except (OSError, ValueError, sqlite3.Error) as exc:
        LOGGER.warning("Paper state: %s", exc)
        st.error("无法读取模拟账户状态")
        return
    if state is None:
        st.info("模拟账户尚未初始化")
        return
    heartbeat = pd.Timestamp(state["heartbeat"]) if state["heartbeat"] else None
    healthy = heartbeat is not None and pd.Timestamp.now(tz="UTC") - heartbeat < pd.Timedelta(minutes=3)
    status = "运行中" if healthy and not state["error"] else "停用或异常"
    st.caption(f"服务 {status} · {state['symbol'] or '未绑定'} · 最近心跳 {heartbeat or '无'}")
    st.write(f"权益 **{state['equity']:,.2f} USDT** · 连续亏损 {state['losses']} 次")
    if state["mode"] != "running" or state["day_halt"] or state["losses"] >= 3:
        st.warning("新开仓已暂停")
    if state["error"]:
        st.error(state["error"])
    if state["position"]:
        st.write(pd.DataFrame([state["position"]]))
    if equity:
        values = pd.DataFrame(equity, columns=["time", "权益 USDT"])
        values["time"] = pd.to_datetime(values.time)
        st.line_chart(values.set_index("time"))
    if trades:
        st.dataframe(pd.DataFrame(trades), width="stretch")
    st.caption("使用服务主机 CLI 暂停、恢复或紧急停止开仓。")
