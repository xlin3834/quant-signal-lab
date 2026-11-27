export function drawChart(canvas, bars, layers, colors) {
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
  const show = layers || {ema7: true, ema10: true, ema120: true, boll: true};
  const count = Math.max(30, Math.min(bars.length, show.count || bars.length));
  bars = bars.slice(bars.length - count);
  const panels = [["price", show.macd || show.rsi || show.volume ? 0.62 : 1]];
  if (show.macd) panels.push(["macd", 0.16]);
  if (show.rsi) panels.push(["rsi", 0.16]);
  if (show.volume) panels.push(["volume", 0.16]);
  const total = panels.reduce((sum, item) => sum + item[1], 0);
  const pad = {l: 58, r: 12, t: 10, b: 8};
  let y = 8;
  panels.forEach(([name, share], index) => {
    const box = {x: pad.l, y, w: width - pad.l - pad.r, h: Math.max(36, height * (share / total) - (index === panels.length - 1 ? 16 : 8))};
    y += box.h + 6;
    drawPanel(context, name, box, bars, show, colors);
  });
}

function drawPanel(context, name, box, bars, show, colors) {
  context.strokeStyle = "#173044";
  context.strokeRect(box.x, box.y, box.w, box.h);
  const values = collect(name, bars, show);
  if (!values.length) return;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const slot = box.w / bars.length;
  const xAt = (index) => box.x + slot * index + slot / 2;
  const yAt = (value) => box.y + box.h - ((value - min) / span) * (box.h - 16) - 8;
  if (name === "price") {
    if (show.boll !== false) {
      line(context, bars, "boll18_up", xAt, yAt, show.bollColor || "#8ea0b0", show.bollWidth || 1);
      line(context, bars, "boll18_low", xAt, yAt, show.bollColor || "#8ea0b0", show.bollWidth || 1);
      line(context, bars, "boll18_mid", xAt, yAt, show.bollColor || "#d5dde6", show.bollWidth || 1.4);
    }
    if (show.ema120 !== false) line(context, bars, "ema120", xAt, yAt, show.ema120Color || "#b07cff", show.ema120Width || 1.6);
    if (show.ema10 !== false) line(context, bars, "ema10", xAt, yAt, show.ema10Color || "#3aa0ff", show.ema10Width || 1.6);
    if (show.ema7 !== false) line(context, bars, "ema7", xAt, yAt, show.ema7Color || "#f5c542", show.ema7Width || 1.6);
    (show.sr || []).forEach((price) => {
      context.strokeStyle = "#e0b15a";
      context.setLineDash([4, 4]);
      context.beginPath();
      context.moveTo(box.x, yAt(price));
      context.lineTo(box.x + box.w, yAt(price));
      context.stroke();
      context.setLineDash([]);
    });
    if (Number.isInteger(show.crossIndex) && show.crossIndex >= 0 && show.crossIndex < bars.length) {
      const x = xAt(show.crossIndex);
      context.strokeStyle = "#d5dde6";
      context.setLineDash([2, 3]);
      context.beginPath();
      context.moveTo(x, box.y);
      context.lineTo(x, box.y + box.h);
      context.stroke();
      context.setLineDash([]);
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

function collect(name, bars, show) {
  if (name === "price") {
    return bars.flatMap((bar) => [bar.h, bar.l,
      show.ema7 === false ? null : bar.ema7,
      show.ema10 === false ? null : bar.ema10,
      show.ema120 === false ? null : bar.ema120,
      show.boll === false ? null : bar.boll18_up,
      show.boll === false ? null : bar.boll18_low].filter(number));
  }
  if (name === "volume") return bars.map((bar) => bar.v).filter(number);
  if (name === "macd") return bars.flatMap((bar) => [bar.macd, bar.macd_signal, 0]).filter(number);
  if (name === "rsi") return [0, 100];
  return [];
}

function line(context, bars, key, xAt, yAt, color, width=1.4) {
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
  context.lineWidth = width;
  context.stroke();
}

function number(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function slotWidth(slot) {
  return Math.max(1.5, Math.min(7, slot * 0.62));
}

export function bindChart(canvas, getBars, getLayers, colors) {
  if (canvas.dataset.bound) return;
  canvas.dataset.bound = "1";
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const layers = getLayers();
    const size = layers.count || getBars().length || 160;
    layers.count = Math.max(40, Math.min(500, size + (event.deltaY > 0 ? 20 : -20)));
    drawChart(canvas, getBars(), layers, colors());
  }, {passive: false});
  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const layers = getLayers();
    const count = Math.max(30, Math.min(getBars().length, layers.count || getBars().length));
    const shown = getBars().slice(getBars().length - count);
    const slot = (rect.width - 70) / Math.max(shown.length, 1);
    const index = Math.min(shown.length - 1, Math.max(0, Math.floor((event.clientX - rect.left - 58) / slot)));
    const bar = shown[index];
    layers.crossIndex = index;
    drawChart(canvas, getBars(), layers, colors());
    const hover = document.querySelector("#hover");
    if (hover && bar) {
      hover.textContent = `收盘 ${bar.c} · EMA7 ${bar.ema7 ?? "—"} · EMA10 ${bar.ema10 ?? "—"} · EMA120 ${bar.ema120 ?? "—"} · 中轨 ${bar.boll18_mid ?? "—"}`;
    }
  });
  canvas.addEventListener("click", (event) => {
    if (!event.shiftKey) return;
    const layers = getLayers();
    const count = Math.max(30, Math.min(getBars().length, layers.count || getBars().length));
    const shown = getBars().slice(getBars().length - count);
    const rect = canvas.getBoundingClientRect();
    const slot = (rect.width - 70) / Math.max(shown.length, 1);
    const index = Math.min(shown.length - 1, Math.max(0, Math.floor((event.clientX - rect.left - 58) / slot)));
    const price = shown[index]?.c;
    if (!price) return;
    layers.sr = [...(layers.sr || []), price].slice(-8);
    drawChart(canvas, getBars(), layers, colors());
  });
}
