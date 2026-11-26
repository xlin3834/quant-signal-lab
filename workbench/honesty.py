"""Visible limits of the current scoring rules.

Phase 1 records these limits. It does not change the score or the 4H+1H gate,
because that would move the existing backtest baseline.
"""

SCORING_WEIGHTS = (30, 20, 20, 15, 15)
WEIGHTS_NOTE = (
    "当前保存的权重不会改变当前评分。"
    "计分仍使用固定权重 30/20/20/15/15（趋势结构、动量指标、成交量确认、市场形态、波动率与风险）。"
    "第二阶段才会把设置中的权重接入分数；本阶段不改口径。"
)
GATE_NOTE = (
    "正式候选仍有硬门槛：4 小时方向与 1 小时方向必须同时与该侧一致。"
    "低周期单独反弹目前会被否决。"
    "本阶段不改判定，以免打破已有回测口径。第二阶段将解除这一硬门槛。"
)
LATENCY_NOTE = (
    "传输、中位和 P95 只来自当前这条连接的实测样本。"
    "某一时刻的中位数不是已验收延迟，也不能代替页面上的样本数。"
)


def disclosures():
    return {
        "weights_applied": False,
        "scoring_weights": list(SCORING_WEIGHTS),
        "weights_note": WEIGHTS_NOTE,
        "gate_active": True,
        "gate_note": GATE_NOTE,
        "latency_note": LATENCY_NOTE,
    }
