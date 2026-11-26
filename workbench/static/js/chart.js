export function drawChart(canvas, bars, mode, colors) {
  const context = canvas.getContext("2d");
  const width = canvas.clientWidth || 640;
  const height = canvas.clientHeight || 420;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#09141f";
  context.fillRect(0, 0, width, height);
  if (!bars?.length) {
    context.fillStyle = "#89a0b4";
    context.font = "14px sans-serif";
    context.fillText("没有可绘制的 K 线", 24, 40);
    return;
  }
  const panels = mode === "four"
    ? [["price", 0.52], ["macd", 0.16], ["rsi", 0.16], ["volume", 0.16]]
    : mode === "candle"
      ? [["price", 1]]
      : [["price", 0.78], ["volume", 0.22]];
  const pad = {l: 58, r: 12, t: 10, b: 8};
  let y = 8;
  panels.forEach(([name, share], index) => {
    const box = {x: pad.l, y, w: width - pad.l - pad.r, h: Math.max(36, height * share - (index === panels.length - 1 ? 16 : 8))};
    y += box.h + 6;
    drawPanel(context, name, box, bars, mode, colors);
  });
}

function drawPanel(context, name, box, bars, mode, colors) {
  context.strokeStyle = "#173044";
  context.strokeRect(box.x, box.y, box.w, box.h);
  const values = collect(name, bars, mode);
  if (!values.length) return;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const slot = box.w / bars.length;
  const xAt = (index) => box.x + slot * index + slot / 2;
  const yAt = (value) => box.y + box.h - ((value - min) / span) * (box.h - 16) - 8;
  if (name === "price") {
    if (mode !== "candle") {
      line(context, bars, "boll_up", xAt, yAt, "#35586d");
      line(context, bars, "boll_low", xAt, yAt, "#35586d");
      line(context, bars, "boll_mid", xAt, yAt, "#6f8ea3");
      line(context, bars, "ema_bg", xAt, yAt, "#8aa4b8");
      line(context, bars, "ema_mid", xAt, yAt, "#c9a0ff");
      line(context, bars, "ema_short", xAt, yAt, "#5ec8ff");
      line(context, bars, "ema_fast", xAt, yAt, "#f5c16c");
    }
    bars.forEach((bar, index) => {
      const up = bar.c >= bar.o;
      context.strokeStyle = up ? colors.up : colors.down;
      context.fillStyle = bar.forming ? "transparent" : context.strokeStyle;
      const x = xAt(index);
      context.beginPath();
      context.moveTo(x, yAt(bar.h));
      context.lineTo(x, yAt(bar.l));
      context.stroke();
      const top = yAt(Math.max(bar.o, bar.c));
      const body = Math.max(1, Math.abs(yAt(bar.o) - yAt(bar.c)));
      context.strokeRect(x - Math.max(1, slotWidth(slot) / 2), top, Math.max(1.5, slotWidth(slot)), body);
      if (!bar.forming) context.fillRect(x - Math.max(1, slotWidth(slot) / 2), top, Math.max(1.5, slotWidth(slot)), body);
    });
  } else if (name === "volume") {
    bars.forEach((bar, index) => {
      context.fillStyle = bar.c >= bar.o ? colors.up : colors.down;
      const h = ((bar.v - min) / span) * (box.h - 16);
      context.globalAlpha = 0.75;
      context.fillRect(xAt(index) - 1.5, box.y + box.h - 8 - h, 3, h);
      context.globalAlpha = 1;
    });
  } else if (name === "macd") {
    bars.forEach((bar, index) => {
      if (bar.macd == null || bar.macd_signal == null) return;
      const hist = bar.macd - bar.macd_signal;
      context.fillStyle = hist >= 0 ? colors.up : colors.down;
      context.fillRect(xAt(index) - 1.2, yAt(Math.max(hist, 0)), 2.4, Math.max(1, Math.abs(yAt(hist) - yAt(0))));
    });
    line(context, bars, "macd", xAt, yAt, "#5ec8ff");
    line(context, bars, "macd_signal", xAt, yAt, "#f5c16c");
  } else if (name === "rsi") {
    [30, 50, 70].forEach((level) => {
      context.strokeStyle = "#1c3346";
      context.beginPath();
      context.moveTo(box.x, yAt(level));
      context.lineTo(box.x + box.w, yAt(level));
      context.stroke();
    });
    line(context, bars, "rsi", xAt, yAt, "#d7c4ff");
  }
  context.fillStyle = "#89a0b4";
  context.font = "11px sans-serif";
  context.fillText(max.toPrecision(6).replace(/\.?0+$/, ""), 8, box.y + 12);
  context.fillText(name.toUpperCase(), box.x + 6, box.y + 12);
}

function collect(name, bars, mode) {
  if (name === "price") {
    return bars.flatMap((bar) => [bar.h, bar.l, bar.ema_fast, bar.ema_short, bar.ema_mid, bar.boll_up, bar.boll_low].filter(number));
  }
  if (name === "volume") return bars.map((bar) => bar.v).filter(number);
  if (name === "macd") return bars.flatMap((bar) => [bar.macd, bar.macd_signal, 0]).filter(number);
  if (name === "rsi") return [0, 100];
  return [];
}

function line(context, bars, key, xAt, yAt, color) {
  context.beginPath();
  let started = false;
  bars.forEach((bar, index) => {
    const value = bar[key];
    if (!number(value)) {
      started = false;
      return;
    }
    if (!started) {
      context.moveTo(xAt(index), yAt(value));
      started = true;
    } else context.lineTo(xAt(index), yAt(value));
  });
  context.strokeStyle = color;
  context.lineWidth = 1.2;
  context.stroke();
}

function number(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function slotWidth(slot) {
  return Math.max(1.5, Math.min(7, slot * 0.62));
}
