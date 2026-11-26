"""Incremental Streamlit quote panel for BTC and ETH USDT perpetuals."""
import pandas as pd
import streamlit as st

from realtime_engine import get_hub

REFRESH = 0.4


def _fmt_time(ms):
    if ms is None:
        return "—"
    return pd.to_datetime(ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"


def _fmt_ms(value):
    return "—" if value is None else f"{value:.1f} ms"


@st.fragment(run_every=REFRESH)
def perpetual_quote_panel(symbols=("BTCUSDT", "ETHUSDT")):
    hub = get_hub()
    st.caption("核心资产实时行情 · Bitget 官方 USDT 本位永续 · 仅增量刷新本区域，不整页重载 · 目标 200–500ms")
    columns = st.columns(len(symbols))
    paused = False
    snapshots = []
    for column, symbol in zip(columns, symbols):
        snap = hub.snapshot(symbol)
        snapshots.append(snap)
        paused = paused or not snap["official_candidates_ok"]
        with column:
            st.markdown(f"**{symbol}** · {snap['source']}")
            if snap["price"] is None:
                st.error(f"{snap['status']} · 报价不可用")
                continue
            book = ""
            if snap["bid"] is not None and snap["ask"] is not None:
                book = f"买一 {snap['bid']:g} / 卖一 {snap['ask']:g}"
            st.write(f"最新成交 **{snap['price']:g}**  {book}")
            color = {"实时": "🟢", "延迟": "🟡", "断线": "🔴", "过期": "🔴", "不可用": "⚪", "权限不足": "🟠", "休市": "⚫"}.get(snap["status"], "⚪")
            st.caption(f"{color} 状态 {snap['status']} · 来源 {snap['origin'] or '无'} · "
                       f"WebSocket {'已连接' if snap['connected'] else '未连接'}")
            st.caption(f"数据产生 {_fmt_time(snap['timestamp'])}")
            st.caption(f"服务端接收 {_fmt_time(snap['received'])}")
            st.caption(f"前端更新 {_fmt_time(snap['frontend'])}")
            st.caption(f"传输延迟 {_fmt_ms(snap['transport_ms'])} · 新鲜度 {_fmt_ms(snap['age_ms'])}")
            st.caption(f"交易时段 {snap['session']} · 订阅权限 {snap['permission']}")
            st.caption(f"样本 {snap['samples']} · 平均 {_fmt_ms(snap['avg_ms'])} · "
                       f"P95 {_fmt_ms(snap['p95_ms'])} · 断线 {snap['disconnects']} 次 · "
                       f"缺失 K 线 {snap['missing_candles']}")
            if snap["error"] and not snap["connected"]:
                st.caption(f"最近连接错误 {snap['error']}，正在指数退避重连")
    if paused:
        st.warning("行情过期、断线或未使用实时永续 WebSocket 时，已暂停生成新的正式交易候选。恢复后需重新校验。不会用旧价格冒充实盘实时行情。")
    return snapshots
