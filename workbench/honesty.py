"""What the workbench actually scores and draws.

Saved five-factor weights change the score. The research eight-factor list is
only partly computable: the last three factors are disclosed and not added in.
"""

SCORING_WEIGHTS = (30, 20, 20, 15, 15)
WEIGHTS_NOTE = (
    "已保存的五项权重会改变当前评分。原预设默认仍是 30/20/20/15/15。"
    "研究用八因子 20/20/15/15/10/10/5/5 里，只有前五项能对应现有检查；"
    "合约资金、更细的多周期权重和其余因子没有单独计算，不会计入分数。"
    "分数不是胜率，也不会自动下单。"
)
GATE_NOTE = (
    "4 小时和 1 小时方向现在只作为背景显示，不再单独把低周期形态打成观望。"
    "已收盘确认和未收盘预警分开写。未收盘 K 线不能当成正式方向。"
)
LATENCY_NOTE = (
    "传输、中位和 P95 只来自当前这条连接的实测样本。"
    "某一时刻的中位数不是已验收延迟，也不能代替页面上的样本数。"
)
EIGHT_FACTORS = (
    {"name": "趋势结构", "weight": 20, "scored": True},
    {"name": "动量", "weight": 20, "scored": True},
    {"name": "成交量", "weight": 15, "scored": True},
    {"name": "形态", "weight": 15, "scored": True},
    {"name": "波动", "weight": 10, "scored": True},
    {"name": "合约资金", "weight": 10, "scored": False, "reason": "没有独立因子接入计分"},
    {"name": "多周期细权", "weight": 5, "scored": False, "reason": "4小时和1小时只作背景，不另计这 5 分"},
    {"name": "其余研究因子", "weight": 5, "scored": False, "reason": "未计算，不计入分数"},
)
CHART_DEFAULT = ("ema7", "ema10", "ema120", "boll18")


def disclosures():
    return {
        "weights_applied": True,
        "scoring_weights": list(SCORING_WEIGHTS),
        "weights_note": WEIGHTS_NOTE,
        "gate_active": False,
        "gate_note": GATE_NOTE,
        "latency_note": LATENCY_NOTE,
        "eight_factor_research": list(EIGHT_FACTORS),
        "chart_default": list(CHART_DEFAULT),
    }
