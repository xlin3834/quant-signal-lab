"""Sidebar controls for the five strategy configurations."""
import streamlit as st

from indicators import Parameters
from presets import PRESET_ORDER, from_session
from research_store import connect, default_path, load_presets, reset_preset, save_preset


def _apply_selected():
    conn = connect(default_path())
    try:
        config = load_presets(conn)[st.session_state.preset_choice]
    finally:
        conn.close()
    for key, value in config.session_values().items():
        st.session_state[key] = value


def _field(label, key, default, minimum, maximum, step=None):
    if key not in st.session_state:
        st.session_state[key] = default
    kwargs = {"min_value": minimum, "max_value": maximum, "key": key}
    if step is not None:
        kwargs["step"] = step
    return st.number_input(label, **kwargs)


def spot_sidebar():
    conn = connect(default_path())
    try:
        if not st.session_state.get("preset_hydrated"):
            for key, value in load_presets(conn)["均衡"].session_values().items():
                st.session_state[key] = value
            st.session_state.preset_choice = "均衡"
            st.session_state.preset_hydrated = True
        st.sidebar.selectbox("策略配置", PRESET_ORDER, key="preset_choice", on_change=_apply_selected)
        st.sidebar.caption("模拟服务仍使用原来的趋势评分，不跟随均值回归预设。")
        choice = st.sidebar.columns(2)
        if choice[0].button("保存当前参数", width="stretch"):
            config = from_session(st.session_state.preset_choice, st.session_state)
            problems = config.errors()
            if problems:
                st.sidebar.error("；".join(problems))
            else:
                save_preset(conn, config)
                st.sidebar.success("已写入研究库")
        if choice[1].button("恢复该策略默认", width="stretch"):
            reset_preset(conn, st.session_state.preset_choice)
            _apply_selected()
            st.rerun()
    finally:
        conn.close()

    with st.sidebar.expander("指标参数"):
        ema = tuple(_field(f"EMA {n} 周期", key, n, 2, 350) for n, key in (
            (7, "ema_7"), (20, "ema_20"), (50, "ema_50"), (200, "ema_200")))
        boll = _field("BOLL 周期", "boll_n", 20, 2, 150)
        rsi = _field("RSI 周期", "rsi_n", 14, 2, 150)
        macd = tuple(_field(label, key, n, 2, 150) for label, key, n in (
            ("MACD 快线", "macd_fast", 12), ("MACD 慢线", "macd_slow", 26), ("MACD 信号线", "macd_signal", 9)))
        atr = _field("ATR 周期", "atr_n", 14, 2, 150)
        volume = _field("均量周期", "volume_n", 20, 2, 150)
        structure = _field("支撑／压力回溯周期", "structure_n", 20, 5, 90)
    with st.sidebar.expander("评分与计划"):
        _field("候选阈值", "rule_threshold", 65, 1, 100)
        _field("强候选阈值", "rule_strong", 80, 1, 100)
        _field("最低目标盈亏比", "rule_rr", 1.5, 1.0, 10.0, 0.1)
        st.caption("趋势类预设使用下面的权重。均值回归不使用这五项。")
        for label, key, default in (
            ("趋势结构", "w_trend", 30), ("动量指标", "w_momentum", 20),
            ("成交量确认", "w_volume", 20), ("市场形态", "w_pattern", 15), ("波动率与风险", "w_risk", 15),
        ):
            _field(label, key, default, 0, 100)
    with st.sidebar.expander("均值回归参数"):
        _field("RSI 低阈值", "mr_rsi_low", 30.0, 1.0, 50.0, 1.0)
        _field("RSI 高阈值", "mr_rsi_high", 70.0, 50.0, 99.0, 1.0)
        _field("ATR 止损倍数", "mr_atr_stop", 1.5, 0.1, 10.0, 0.1)
    with st.sidebar.expander("模拟回测资金与成本", expanded=True):
        if "cost_capital" not in st.session_state:
            st.session_state.cost_capital = 1000.0
        if "cost_risk" not in st.session_state:
            st.session_state.cost_risk = 0.5
        if "cost_fee" not in st.session_state:
            st.session_state.cost_fee = 0.06
        if "cost_slip" not in st.session_state:
            st.session_state.cost_slip = 0.03
        st.number_input("模拟本金 USDT", min_value=100.0, max_value=1000000.0, key="cost_capital")
        st.slider("单笔风险 %", min_value=0.1, max_value=2.0, step=0.1, key="cost_risk")
        st.number_input("单边手续费 %", min_value=0.0, max_value=1.0, step=0.01, key="cost_fee")
        st.number_input("单边滑点 %", min_value=0.0, max_value=1.0, step=0.01, key="cost_slip")
    return from_session(st.session_state.preset_choice, st.session_state), Parameters(
        ema, int(boll), int(rsi), macd, int(atr), int(volume), int(structure))
