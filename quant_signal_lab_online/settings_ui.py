"""Sidebar controls for presets and the five original parameter modules."""
import json

import pandas as pd
import streamlit as st

from presets import (
    BUILTINS, DEFAULT_NAME, PRESET_NAMES, compare_presets, copy_custom, delete_custom,
    export_custom, import_custom, list_custom, load_preset, preset_table, rename_custom,
    save_custom,
)
from macro_fib import FibParams, MacroParams
from strategy_modules import (
    DynamicStop, EmaBoll, IntradayScore, Pullback, ShortcutMA, default_modules,
    shortcut_from_params, sync_shortcut,
)

RISK_CAP_KEYS = ("capital", "risk", "leverage")


def _state(key, default):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def current_preset_name():
    return st.session_state.get("preset_name", DEFAULT_NAME)


def applied_preset():
    name = st.session_state.get("applied_preset", DEFAULT_NAME)
    try:
        return load_preset(name)
    except KeyError:
        return load_preset(DEFAULT_NAME)


def render_settings():
    preset = applied_preset()
    options = [*PRESET_NAMES, *list_custom()]
    name = st.sidebar.selectbox("配置预设", options, index=options.index(current_preset_name()) if current_preset_name() in options else 0)
    st.session_state.preset_name = name
    if name == "自定义":
        incoming = st.session_state.get("custom_draft") or applied_preset()
    else:
        incoming = load_preset(name)
    st.sidebar.caption(incoming.note or "自定义配置可独立修改全部参数。以上内置值为初始测试配置，不得称为最优。")
    if name != "自定义":
        diff = compare_presets(preset, incoming)
        with st.sidebar.expander("参数变更对比", expanded=bool(diff) and name != st.session_state.get("applied_preset")):
            if not diff:
                st.caption("与当前已应用配置相同。")
            else:
                st.dataframe(pd.DataFrame(diff), hide_index=True, width="stretch")
        if st.sidebar.button("应用预设", type="primary"):
            st.session_state.applied_preset = name
            st.session_state.applied_snapshot = incoming
            # Never raise leverage or per-trade risk automatically.
            st.sidebar.success(f"已应用 {name}。图表、扫描、评分、回测与提醒将使用同一套参数。杠杆和单笔风险保持你当前的设置。")
            preset = incoming
    else:
        st.sidebar.caption("自定义模式：保存多个配置，支持重命名、复制、删除、导入导出和恢复默认。")
        with st.sidebar.expander("自定义配置管理"):
            label = st.text_input("配置名称", st.session_state.get("custom_label", "我的配置"))
            if st.button("保存当前为自定义"):
                saved = save_custom(label, incoming)
                st.session_state.applied_preset = saved
                st.success(f"已保存 {saved}")
            profiles = list(list_custom())
            if profiles:
                selected = st.selectbox("已保存配置", profiles)
                col_a, col_b, col_c = st.columns(3)
                if col_a.button("加载"):
                    st.session_state.applied_preset = selected
                    st.session_state.preset_name = selected
                new_name = col_b.text_input("新名称", selected, label_visibility="collapsed")
                if col_b.button("重命名"):
                    rename_custom(selected, new_name)
                if col_c.button("复制"):
                    copy_custom(selected, f"{selected} 副本")
                if st.button("删除"):
                    delete_custom(selected)
            st.download_button("导出 JSON", export_custom(), "signal-lab-presets.json", "application/json")
            uploaded = st.file_uploader("导入 JSON", type="json")
            if uploaded is not None and st.button("确认导入"):
                import_custom(uploaded.read().decode("utf-8"))
                st.success("已导入，不会覆盖内置预设。")
            if st.button("恢复默认（均衡型）"):
                st.session_state.applied_preset = DEFAULT_NAME
                st.session_state.preset_name = DEFAULT_NAME

    with st.sidebar.expander("内置预设对照（截图验收）"):
        st.dataframe(pd.DataFrame(preset_table()), hide_index=True, width="stretch")
        st.caption("对照用户提供的四列默认值。不会覆盖你已保存的自定义配置。")

    shortcut, intraday, ema_boll, pullback, dyn_stop = st.session_state.get("modules") or default_modules()
    if "modules" not in st.session_state:
        st.session_state.modules = (shortcut, intraday, ema_boll, pullback, dyn_stop)

    with st.sidebar.expander("1. MA 与 RSI 快捷参数"):
        short = st.number_input("短期 MA", 2, 350, shortcut.short)
        long = st.number_input("长期 MA", 2, 350, shortcut.long)
        rsi = st.number_input("RSI 周期", 2, 150, shortcut.rsi)
        shortcut = ShortcutMA(short, long, rsi)
        if st.button("保存快捷参数"):
            params = sync_shortcut(shortcut, preset.parameters())
            st.session_state.shortcut_params = params
            st.caption("已与统一指标中的节奏 EMA 和 RSI 同步，避免重复冲突。")
        if st.button("恢复快捷默认"):
            shortcut = ShortcutMA()

    with st.sidebar.expander("2. 标准日内评分与异动参数"):
        st.caption("默认值对齐均衡型 / 截图基准，仅作初始测试。")
        e1 = st.number_input("节奏 EMA", 2, 350, preset.ema[0], key="intraday_e1")
        e2 = st.number_input("短线 EMA", 2, 350, preset.ema[1], key="intraday_e2")
        e3 = st.number_input("中线 EMA", 2, 350, preset.ema[2], key="intraday_e3")
        e4 = st.number_input("背景 EMA", 2, 350, preset.ema[3], key="intraday_e4")
        m1 = st.number_input("MACD 快线", 2, 150, preset.macd[0], key="intraday_m1")
        m2 = st.number_input("MACD 慢线", 2, 150, preset.macd[1], key="intraday_m2")
        m3 = st.number_input("MACD 信号", 2, 150, preset.macd[2], key="intraday_m3")
        rsi_i = st.number_input("RSI", 2, 150, preset.rsi, key="intraday_rsi")
        atr_i = st.number_input("ATR", 2, 150, preset.atr, key="intraday_atr")
        boll_i = st.number_input("BOLL 周期", 2, 150, preset.boll, key="intraday_boll")
        bm_i = st.number_input("BOLL 倍数", 0.5, 5.0, float(preset.boll_mult), 0.1, key="intraday_bm")
        vol_i = st.number_input("成交量均值周期", 2, 150, preset.volume, key="intraday_vol")
        thr = st.number_input("正式候选最低分", 1, 100, preset.threshold, key="intraday_thr")
        rvol = st.number_input("交易确认 RVOL", 0.5, 5.0, float(preset.rvol), 0.1, key="intraday_rvol")
        ext = st.number_input("极端 ATR% 暂停阈值", 0.5, 20.0, 4.0, 0.1, key="intraday_ext")
        s5 = st.number_input("5 分钟异动 %", 0.1, 10.0, 0.8, 0.1, key="intraday_s5")
        s15 = st.number_input("15 分钟异动 %", 0.1, 20.0, 1.5, 0.1, key="intraday_s15")
        s1h = st.number_input("1 小时异动 %", 0.1, 30.0, 2.5, 0.1, key="intraday_s1h")
        vs = st.number_input("量能异动 RVOL", 0.5, 10.0, 1.8, 0.1, key="intraday_vs")
        vh = st.number_input("高等级量能 RVOL", 0.5, 10.0, 2.5, 0.1, key="intraday_vh")
        w1 = st.number_input("权重·短线结构", 0, 100, 30, key="w1")
        w2 = st.number_input("权重·1小时趋势", 0, 100, 20, key="w2")
        w3 = st.number_input("权重·动能", 0, 100, 20, key="w3")
        w4 = st.number_input("权重·量能", 0, 100, 15, key="w4")
        w5 = st.number_input("权重·触发", 0, 100, 15, key="w5")
        intraday = IntradayScore((e1, e2, e3, e4), (m1, m2, m3), rsi_i, atr_i, boll_i, bm_i, vol_i,
                                 thr, rvol, ext / 100, s5 / 100, s15 / 100, s1h / 100, vs, vh, (w1, w2, w3, w4, w5))
        if st.button("恢复日内评分默认"):
            intraday = IntradayScore()

    with st.sidebar.expander("3. 独立 EMA×布林策略参数"):
        enabled = st.checkbox("独立策略启用", ema_boll.enabled)
        inherit = st.radio("参数来源", ("继承预设", "使用独立参数"), index=0 if ema_boll.inherit else 1, key="ema_inherit")
        only_two = st.checkbox("仅显示两条策略线", ema_boll.only_two_lines)
        show_lv = st.checkbox("显示本策略参考位", ema_boll.show_levels)
        ema_n = st.selectbox("EMA", (10, 7, "自定义"), index=0 if ema_boll.ema == 10 else 1 if ema_boll.ema == 7 else 2)
        ema_custom = st.number_input("自定义 EMA", 2, 350, ema_boll.ema if ema_boll.ema not in (7, 10) else 10, key="ema_custom")
        interval = st.selectbox("默认触发周期", ("5m", "15m", "1H", "4H"), index=1)
        capital = st.number_input("模拟资金 USDT", 10.0, 100000.0, float(ema_boll.capital), key="eb_cap")
        risk = st.number_input("单笔风险 %", 0.1, 5.0, ema_boll.risk * 100, key="eb_risk")
        leverage = st.number_input("杠杆", 1.0, 20.0, float(ema_boll.leverage), key="eb_lev")
        atr_stop = st.number_input("ATR 止损倍数", 0.5, 8.0, float(ema_boll.atr_stop), key="eb_atr")
        fee = st.number_input("单边手续费 %", 0.0, 1.0, ema_boll.fee * 100, key="eb_fee")
        slip = st.number_input("单边滑点 %", 0.0, 1.0, ema_boll.slip * 100, key="eb_slip")
        ema_boll = EmaBoll(enabled, only_two, show_lv, ema_custom if ema_n == "自定义" else int(ema_n),
                           interval, inherit == "继承预设", capital, risk / 100, leverage, atr_stop, fee / 100, slip / 100)
        st.caption("应用策略设置后参与计算。切换总预设不会自动提高杠杆或单笔风险。")

    with st.sidebar.expander("4. 多周期回踩策略参数"):
        pb_on = st.checkbox("启用双向回踩", pullback.enabled or pullback.both_sides, key="pb_on")
        pb_show = st.checkbox("显示参考线", pullback.show_levels, key="pb_show")
        pb_inherit = st.radio("回踩参数来源", ("继承预设", "使用独立参数"), index=0 if pullback.inherit else 1, key="pb_inherit")
        pb_cap = st.number_input("模拟资金 USDT", 10.0, 100000.0, float(pullback.capital), key="pb_cap")
        pb_risk = st.number_input("单笔风险 %", 0.1, 5.0, pullback.risk * 100, key="pb_risk")
        pb_lev = st.number_input("杠杆", 1.0, 20.0, float(pullback.leverage), key="pb_lev")
        pb_long = st.number_input("做多 RSI 低位上限", 1.0, 50.0, float(pullback.rsi_long), key="pb_long")
        pb_short = st.number_input("做空 RSI 高位下限", 50.0, 99.0, float(pullback.rsi_short), key="pb_short")
        pb_fee = st.number_input("单边手续费 %", 0.0, 1.0, pullback.fee * 100, key="pb_fee")
        pb_slip = st.number_input("单边滑点 %", 0.0, 1.0, pullback.slip * 100, key="pb_slip")
        pullback = Pullback(pb_on, True, pb_show, pb_inherit == "继承预设", pb_cap, pb_risk / 100, pb_lev, pb_long, pb_short, pb_fee / 100, pb_slip / 100)
        st.caption("入场价由回踩模块计算，需点应用后重新校验行情。")

    with st.sidebar.expander("5. 布林动态止盈止损参数"):
        scheme = st.selectbox("计划方案", ("ATR/结构", "布林动态"), 0 if dyn_stop.scheme == "ATR/结构" else 1)
        b_per = st.number_input("布林周期", 2, 150, dyn_stop.boll, key="ds_b")
        b_mult = st.number_input("标准差倍数", 0.5, 5.0, float(dyn_stop.boll_mult), 0.1, key="ds_bm")
        ds_atr = st.number_input("ATR 周期", 2, 150, dyn_stop.atr, key="ds_atr")
        ds_rr = st.number_input("最低净盈亏比", 0.5, 10.0, float(dyn_stop.minimum_rr), 0.1, key="ds_rr")
        ds_buf = st.number_input("结构缓冲 ATR 倍数", 0.0, 5.0, float(dyn_stop.structure_buffer), 0.1, key="ds_buf")
        ds_guard = st.number_input("ATR 保护倍数", 0.5, 8.0, float(dyn_stop.atr_guard), 0.1, key="ds_guard")
        ds_sec = st.number_input("趋势第二目标 ATR 空间", 0.5, 10.0, float(dyn_stop.second_atr), 0.1, key="ds_sec")
        ds_max = st.number_input("最大计划止损距离 %", 0.5, 20.0, dyn_stop.max_stop_pct * 100, 0.1, key="ds_max")
        ds_start = st.number_input("移动止损启动（R）", 0.1, 5.0, float(dyn_stop.trail_start_r), 0.1, key="ds_start")
        ds_method = st.selectbox("追踪方法", ("布林中轨", "ATR 跟踪"), 0 if dyn_stop.trail_method == "布林中轨" else 1)
        ds_trail = st.number_input("ATR 跟踪倍数", 0.5, 8.0, float(dyn_stop.trail_atr), 0.1, key="ds_trail")
        ds_be = st.checkbox("成本保本位", dyn_stop.breakeven)
        ds_tr = st.checkbox("移动止损", dyn_stop.trailing)
        dyn_stop = DynamicStop(scheme, b_per, b_mult, ds_atr, ds_rr, ds_buf, ds_guard, ds_sec, ds_max / 100, ds_start, ds_method, ds_trail, ds_be, ds_tr)

    with st.sidebar.expander("宏观周期与斐波那契（独立）"):
        st.caption("参数独立管理，不随预设自动改写，也不替代上述五大模块。周期与斐波那契结论只建议，不自动切换预设，启动时不会写入 user_presets.json。")
        macro = st.session_state.get("macro_params") or MacroParams()
        fib = st.session_state.get("fib_params") or FibParams()
        d_fast = st.number_input("日线趋势快线", 2, 200, int(macro.daily_fast), key="macro_d_fast")
        d_slow = st.number_input("日线趋势慢线", 3, 400, int(macro.daily_slow), key="macro_d_slow")
        w_fast = st.number_input("周线趋势快线", 2, 200, int(macro.weekly_fast), key="macro_w_fast")
        w_slow = st.number_input("周线趋势慢线", 3, 400, int(macro.weekly_slow), key="macro_w_slow")
        m_swing = st.number_input("宏观摆动确认根数", 2, 30, int(macro.swing), key="macro_swing")
        f_interval = st.selectbox("斐波那契基准周期", ("1D", "1W"), 0 if fib.interval == "1D" else 1, key="fib_interval")
        f_swing = st.number_input("斐波那契摆动确认根数", 2, 30, int(fib.swing), key="fib_swing")
        f_look = st.number_input("斐波那契回看根数", 20, 500, int(fib.lookback), key="fib_lookback")
        st.session_state.macro_params = MacroParams(d_fast, d_slow, w_fast, w_slow, m_swing)
        st.session_state.fib_params = FibParams(f_swing, f_look, f_interval)
        report = st.session_state.get("macro_report")
        fib_report = st.session_state.get("fib_report")
        if report:
            st.caption(f"{report.get('summary', '')} · 建议研究 {report.get('suggestion', '')}（不自动应用）")
        if fib_report and fib_report.get("ready"):
            st.caption(f"斐波那契：{fib_report['direction']} · 最近水平 {fib_report['nearest']} @ {fib_report['nearest_price']:,.5g}")
        elif fib_report:
            st.caption(fib_report.get("reason") or "斐波那契尚未就绪")
        st.caption("五种预设的历史对比见 PRESET_COMPARISON.md，可用 python preset_backtest.py 复现。参数未经寻优。")
    with st.sidebar.expander("独立交叉提醒（可选继承）"):
        st.radio("交叉策略参数", ("继承预设", "使用独立参数"), key="cross_inherit")
        st.caption("交叉提醒将使用已应用预设或独立周期，不会替代五大模块。")

    st.session_state.modules = (shortcut, intraday, ema_boll, pullback, dyn_stop)
    return preset, st.session_state.modules
