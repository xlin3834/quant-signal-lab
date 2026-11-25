"""Interactive market workbench charts."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLORS = ("#f5bd64", "#69c9ff", "#c491ff", "#f18daa", "#49788f", "#7cacbd", "#49788f", "#f27888", "#4bc6a6")
LAYER_LABELS = (
    ("均线", "ema"), ("布林带", "boll"), ("支撑压力", "levels"),
    ("成交量", "volume"), ("MACD", "macd"), ("RSI", "rsi"),
)
OVERLAYS = (
    ("ema7", "ema"), ("ema20", "ema"), ("ema50", "ema"), ("ema200", "ema"),
    ("boll_up", "boll"), ("boll_mid", "boll"), ("boll_low", "boll"),
    ("resistance", "levels"), ("support", "levels"),
)


def layers_from(chosen):
    selected = set(chosen or ())
    return frozenset(key for _label, key in LAYER_LABELS if _label in selected)


def _visible(layer, show):
    if show is None:
        return None
    return True if layer in show else "legendonly"


def market_figure(d, params, signals=None, forming=None, show=None):
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=.035,
                        row_heights=[.58, .14, .14, .14])
    fig.add_trace(go.Candlestick(x=d.time, open=d.open, high=d.high, low=d.low, close=d.close,
                                name="OHLC", increasing_line_color="#4bc6a6",
                                decreasing_line_color="#f27888"), row=1, col=1)
    labels = [*(f"EMA {n}" for n in params.ema), f"BOLL {params.boll} 上轨",
              f"BOLL {params.boll} 中轨", f"BOLL {params.boll} 下轨", "压力", "支撑"]
    for (column, layer), name, color in zip(OVERLAYS, labels, COLORS):
        visible = _visible(layer, show)
        fig.add_trace(go.Scatter(x=d.time, y=d[column], name=name, line={"color": color, "width": 1.2},
                                 **({} if visible is None else {"visible": visible})), row=1, col=1)
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
    if forming is not None:
        fig.add_trace(go.Candlestick(
            x=[forming["time"]], open=[forming["open"]], high=[forming["high"]],
            low=[forming["low"]], close=[forming["close"]], name="未收盘",
            increasing_line_color="#f5bd64", decreasing_line_color="#f5bd64",
            increasing_fillcolor="rgba(245,189,100,0.35)", decreasing_fillcolor="rgba(245,189,100,0.35)",
        ), row=1, col=1)
        price = forming["close"]
        label = f"未收盘 {price:,.4f}"
    else:
        price = d.close.iloc[-1]
        label = f"{price:,.4f}"
    fig.add_hline(y=price, line_width=1, line_dash="dot", annotation_text=label, row=1, col=1)
    volume_visible = _visible("volume", show)
    fig.add_trace(go.Bar(x=d.time, y=d.volume, name="成交量", marker_color="#446b80",
                         **({} if volume_visible is None else {"visible": volume_visible})), row=2, col=1)
    for col, name, row, layer in [("vol_avg", "均量", 2, "volume"), ("macd", "MACD", 3, "macd"),
                                  ("macd_signal", "信号线", 3, "macd"), ("rsi", "RSI", 4, "rsi")]:
        visible = _visible(layer, show)
        fig.add_trace(go.Scatter(x=d.time, y=d[col], name=name,
                                 **({} if visible is None else {"visible": visible})), row=row, col=1)
    for level in (30, 70):
        fig.add_hline(y=level, line_width=1, line_dash="dot", row=4, col=1)
    fig.update_layout(template="plotly_dark", height=740, hovermode="x unified", hoversubplots="axis",
                      xaxis_rangeslider_visible=False, margin={"l": 8, "r": 8, "t": 12, "b": 8},
                      legend={"orientation": "h", "y": -0.08}, uirevision="market")
    fig.update_xaxes(showspikes=True, spikemode="across", spikesnap="cursor")
    fig.update_yaxes(side="right", showspikes=True)
    return fig
