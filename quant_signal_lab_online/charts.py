"""Interactive market workbench charts."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLORS = ("#f5bd64", "#69c9ff", "#c491ff", "#f18daa", "#49788f", "#7cacbd", "#49788f", "#f27888", "#4bc6a6")


def market_figure(d, params, signals=None):
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=.035,
                        row_heights=[.58, .14, .14, .14])
    fig.add_trace(go.Candlestick(x=d.time, open=d.open, high=d.high, low=d.low, close=d.close,
                                name="OHLC", increasing_line_color="#4bc6a6",
                                decreasing_line_color="#f27888"), row=1, col=1)
    labels = [*(f"EMA {n}" for n in params.ema), f"BOLL {params.boll} 上轨",
              f"BOLL {params.boll} 中轨", f"BOLL {params.boll} 下轨", "压力", "支撑"]
    for column, name, color in zip(
        ("ema7", "ema20", "ema50", "ema200", "boll_up", "boll_mid", "boll_low", "resistance", "support"),
        labels, COLORS,
    ):
        fig.add_trace(go.Scatter(x=d.time, y=d[column], name=name, line={"color": color, "width": 1.2}),
                      row=1, col=1)
    fig.add_trace(go.Scatter(
        x=d.time, y=d.close, mode="markers", name="行情详情", showlegend=False,
        marker={"opacity": 0}, customdata=d[["open", "high", "low", "atr"]],
        hovertemplate="开 %{customdata[0]:.5g} · 高 %{customdata[1]:.5g} · 低 %{customdata[2]:.5g}"
                      "<br>收 %{y:.5g} · ATR %{customdata[3]:.5g}<extra></extra>",
    ), row=1, col=1)
    if signals is not None:
        for side, name, marker, color in [(1, "做多候选", "triangle-up", "#4bc6a6"),
                                           (-1, "做空候选", "triangle-down", "#f27888")]:
            points = signals[(signals.side == side) & (signals.time >= d.time.iloc[0])
                             & (signals.time <= d.time.iloc[-1])]
            fig.add_trace(go.Scatter(x=points.time, y=points.close, mode="markers", name=name,
                                     marker={"symbol": marker, "color": color, "size": 10},
                                     text=points.decision_time.dt.strftime("%Y-%m-%d %H:%M UTC"),
                                     hovertemplate="%{text}<extra>%{fullData.name}</extra>"), row=1, col=1)
    fig.add_hline(y=d.close.iloc[-1], line_width=1, line_dash="dot",
                  annotation_text=f"{d.close.iloc[-1]:,.4f}", row=1, col=1)
    fig.add_trace(go.Bar(x=d.time, y=d.volume, name="成交量", marker_color="#446b80"), row=2, col=1)
    for col, name, row in [("vol_avg", "均量", 2), ("macd", "MACD", 3),
                           ("macd_signal", "信号线", 3), ("rsi", "RSI", 4)]:
        fig.add_trace(go.Scatter(x=d.time, y=d[col], name=name), row=row, col=1)
    for level in (30, 70):
        fig.add_hline(y=level, line_width=1, line_dash="dot", row=4, col=1)
    fig.update_layout(template="plotly_dark", height=740, hovermode="x unified", hoversubplots="axis",
                      xaxis_rangeslider_visible=False, margin={"l": 8, "r": 8, "t": 12, "b": 8},
                      legend={"orientation": "h", "y": -0.08}, uirevision="market")
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor")
    fig.update_yaxes(side="right", showspikes=True)
    return fig
