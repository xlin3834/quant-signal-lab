"""Streamlit entry point for Quant Signal Lab."""
import streamlit as st

from backtest import Costs
from market_data import INTERVALS, SYMBOLS
from signals import Rules
from ui import backtest_panel, input_parameters, market_workspace, paper_panel
from quote_ui import quote_panel

st.set_page_config(page_title="Quant Signal Lab 4.0", layout="wide")
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
st.title("Quant Signal Lab 4.0")
st.caption("现货市场研究 · 历史回测 · 模拟资金")

asset, period, sorting = st.columns(3)
symbol = asset.selectbox("交易对", SYMBOLS)
interval = period.selectbox("K 线周期", list(INTERVALS), index=1)
sort = sorting.selectbox("市场列表排序", ("24h 成交额 USDT", "24h 涨跌 %"))
params = input_parameters()
with st.sidebar.expander("评分与计划"):
    threshold = st.number_input("候选阈值", 1, 100, 65)
    strong = st.number_input("强候选阈值", 1, 100, 80)
    minimum_rr = st.number_input("最低目标盈亏比", 1.0, 10.0, 1.5, .1)
with st.sidebar.expander("模拟回测资金与成本", expanded=True):
    capital = st.number_input("模拟本金 USDT", 100., 1000000., 1000.)
    risk = st.slider("单笔风险 %", .1, 2.0, .5, .1)
    fee = st.number_input("单边手续费 %", 0., 1., .06, .01)
    slip = st.number_input("单边滑点 %", 0., 1., .03, .01)

if params.macd[0] >= params.macd[1] or threshold > strong:
    st.error("MACD 快线周期须小于慢线，候选阈值须低于或等于强候选阈值。")
    st.stop()

rules = Rules(threshold, strong, minimum_rr)
costs = Costs(capital, risk / 100, fee / 100, slip / 100)
quote_panel(symbol)
market_workspace(symbol, interval, sort, params, rules, costs)
with st.expander("策略回测与交易记录"):
    backtest_panel(symbol, interval, params, rules, costs)
with st.expander("服务健康与模拟账户"):
    paper_panel()
