"""Streamlit entry point for Quant Signal Lab."""
import streamlit as st

from backtest import Costs
from market_data import INTERVALS, SYMBOLS
from presets import DEFAULT_NAME
from settings_ui import render_settings
from signals import Rules
from ui import backtest_panel, market_workspace, paper_panel
from quote_ui import quote_panel
from perpetual_ui import perpetual_workspace

st.set_page_config(page_title="Quant Signal Lab 5.0 · 实时行情与预设", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 2.4rem; max-width: 1640px;}
@media (max-width: 760px) {
  .block-container {padding: 1.1rem .75rem 3rem;}
  [data-testid="stHorizontalBlock"] {flex-wrap: wrap; gap: .6rem;}
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {flex: 1 0 100%; min-width: 0;}
}
</style>
""", unsafe_allow_html=True)
st.title("Quant Signal Lab 5.0")
st.caption("永续与现货市场研究 · 历史回测 · 模拟资金 · 不执行实盘交易 · 预设参数仅为初始测试值")
preset, modules = render_settings()
params = st.session_state.get("shortcut_params") or preset.parameters()
rules = preset.rules()
market = st.radio("市场", ("Bitget USDT 永续", "Bitget 现货"), horizontal=True)
if market == "Bitget USDT 永续":
    perpetual_workspace(params, rules, modules)
    with st.expander("现货模拟账户与服务健康"):
        paper_panel()
    st.stop()

asset, period, sorting = st.columns(3)
symbol = asset.selectbox("交易对", SYMBOLS)
interval = period.selectbox("K 线周期", list(INTERVALS), index=1)
sort = sorting.selectbox("市场列表排序", ("24h 成交额 USDT", "24h 涨跌 %"))
with st.sidebar.expander("评分与计划"):
    threshold = st.number_input("候选阈值", 1, 100, rules.threshold)
    strong = st.number_input("强候选阈值", 1, 100, rules.strong)
    minimum_rr = st.number_input("最低目标盈亏比", 1.0, 10.0, float(rules.minimum_rr), .1)
with st.sidebar.expander("模拟回测资金与成本", expanded=True):
    capital = st.number_input("模拟本金 USDT", 100., 1000000., 1000.)
    risk = st.slider("单笔风险 %", .1, 2.0, .5, .1)
    fee = st.number_input("单边手续费 %", 0., 1., .06, .01)
    slip = st.number_input("单边滑点 %", 0., 1., .03, .01)
    st.caption("切换预设不会自动改写本金、单笔风险或杠杆。")

if params.macd[0] >= params.macd[1] or threshold > strong:
    st.error("MACD 快线周期须小于慢线，候选阈值须低于或等于强候选阈值。")
    st.stop()

rules = Rules(threshold, strong, minimum_rr, rules.early, rules.rvol, rules.atr_stop, rules.weights, rules.extreme_atr)
costs = Costs(capital, risk / 100, fee / 100, slip / 100)
st.info("当前为 Bitget 现货研究视图。现货与 USDT 永续行情分开标注，不会静默混用。")
quote_panel(symbol)
market_workspace(symbol, interval, sort, params, rules, costs)
with st.expander("策略回测与交易记录"):
    backtest_panel(symbol, interval, params, rules, costs)
with st.expander("服务健康与模拟账户"):
    paper_panel()
