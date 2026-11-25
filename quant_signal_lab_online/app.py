"""Streamlit entry point for Quant Signal Lab."""
import streamlit as st

from backtest import Costs
from market_data import INTERVALS, SYMBOLS
from preset_ui import spot_sidebar
from signals import Rules
from ui import backtest_panel, market_workspace, paper_panel
from quote_ui import quote_panel
from perpetual_ui import perpetual_workspace

st.set_page_config(page_title="Quant Signal Lab 5.0 · P1", layout="wide")
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
st.title("Quant Signal Lab 5.0 · P1")
st.caption("永续与现货市场研究 · 历史回测 · 模拟资金 · 研究平台")
market = st.radio("市场", ("Bitget USDT 永续", "Bitget 现货", "研究平台"), horizontal=True)
if market == "Bitget USDT 永续":
    perpetual_workspace()
    with st.expander("现货模拟账户与服务健康"):
        paper_panel()
    st.stop()
if market == "研究平台":
    from platform_ui import platform_page
    platform_page()
    st.stop()

asset, period, sorting = st.columns(3)
symbol = asset.selectbox("交易对", SYMBOLS)
interval = period.selectbox("K 线周期", list(INTERVALS), index=1)
sort = sorting.selectbox("市场列表排序", ("24h 成交额 USDT", "24h 涨跌 %"))
strategy, params = spot_sidebar()
problems = strategy.errors()
if problems:
    st.error("；".join(problems))
    st.stop()

rules = Rules(strategy.threshold, strategy.strong, strategy.minimum_rr)
costs = Costs(strategy.capital, strategy.risk_pct / 100, strategy.fee_pct / 100, strategy.slip_pct / 100)
quote_panel(symbol)
market_workspace(symbol, interval, sort, params, rules, costs, strategy)
with st.expander("策略回测与交易记录"):
    backtest_panel(symbol, interval, params, rules, costs, strategy)
with st.expander("服务健康与模拟账户"):
    paper_panel()
