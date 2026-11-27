import { drawChart } from "./chart.js";

const PAGES = [
  ["overview", "市场总览"],
  ["charts", "行情图表"],
  ["scan", "全市场扫描"],
  ["analysis", "专业分析"],
  ["cross", "跨市场"],
  ["calendar", "经济日历"],
  ["risk", "风险与仓位"],
  ["backtest", "回测与记录"],
  ["catalog", "多市场目录"],
  ["alerts", "提醒"],
  ["monitor", "实盘监控"],
  ["settings", "指标设置"],
];

const EXCHANGES = [
  ["binance", "币安永续"],
  ["okx", "欧易 OKX"],
  ["bitget", "Bitget 永续"],
  ["weex", "WEEX"],
  ["coinw", "币王"],
];

const state = {
  page: "overview",
  quotes: null,
  core: null,
  radar: {},
  settings: null,
  chart: {symbol: "BTCUSDT", interval: "15m", layers: {ema10: true, ema120: true, boll: false, extra: false, macd: false, rsi: false, volume: false}},
  market: {exchange: "bitget", product: "crypto_perp", sort: "turnover", query: "", page: 1},
  catalog: {exchange: "bitget", product: "", query: "", page: 1},
  scanQuery: "",
  alerts: loadAlerts(),
  alertLog: [],
  lastRegime: {},
  favorites: JSON.parse(localStorage.getItem("signal-lab-favorites") || "null") || ["BTCUSDT", "ETHUSDT", "NVDA", "XAU/USD"],
};

const view = document.querySelector("#view");
const tabs = document.querySelector("#tabs");
const badge = document.querySelector("#badge");
const context = document.querySelector("#context");

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    const token = sessionStorage.getItem("signal-lab-admin-token") || "";
    if (token && !headers["X-Admin-Token"]) headers["X-Admin-Token"] = token;
  }
  const response = await fetch(path, {...options, headers});
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok === false) {
    throw new Error(body.error || body.reason || `请求失败 ${response.status}`);
  }
  return body;
}

function fmt(value, digits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString("zh-CN", {maximumFractionDigits: digits});
}

function turnover(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  if (number >= 1e8) return `${(number / 1e8).toFixed(2)}亿`;
  if (number >= 1e4) return `${(number / 1e4).toFixed(1)}万`;
  return fmt(number, 0);
}

function change(value) {
  if (value == null || !Number.isFinite(Number(value))) return `<span class="meta">—</span>`;
  const number = Number(value);
  const klass = number >= 0 ? "up" : "down";
  return `<span class="${klass}">${number >= 0 ? "+" : ""}${number.toFixed(2)}%</span>`;
}

function pill(freshness) {
  const klass = freshness === "实时" ? "ok" : freshness === "数据异常" || freshness === "断线" ? "bad" : freshness === "数据源未接通" || freshness === "过期" || freshness === "延迟" ? "warn" : "";
  return `<span class="pill ${klass}">${esc(freshness || "—")}</span>`;
}

function quoteOf(symbol) {
  return state.quotes?.symbols?.[symbol] || null;
}

function priceText(symbol, row) {
  const live = quoteOf(symbol);
  if (live) return fmt(live.price, live.price > 1000 ? 1 : 2);
  return fmt(row?.price, row?.price > 1000 ? 1 : 4);
}

function loadAlerts() {
  const saved = JSON.parse(localStorage.getItem("signal-lab-alerts") || "null");
  return saved || {
    symbols: ["BTCUSDT", "ETHUSDT"],
    frames: ["5m", "15m"],
    kinds: ["candidate", "regime", "level", "invalid"],
    sound: false,
  };
}

function saveAlerts() {
  localStorage.setItem("signal-lab-alerts", JSON.stringify(state.alerts));
}

function paintChrome() {
  tabs.innerHTML = PAGES.map(([id, label]) => `<button class="tab ${state.page === id ? "active" : ""}" type="button" data-go="${id}">${label}</button>`).join("");
  const item = state.quotes?.badge;
  badge.className = `badge ${item?.level || "delay"}`;
  badge.textContent = item?.text || "Bitget价格流 · 连接中";
  const latency = document.querySelector("#latency");
  if (latency) latency.textContent = latencyBanner();
  const titles = {
    overview: "Bitget · 核心资产 · 滚动24h",
    charts: `Bitget · ${state.chart.symbol} · ${state.chart.interval}`,
    scan: "Bitget · 成交额快照 · 不编造方向",
    analysis: "已收盘评分 · 未收盘只作预警",
    cross: "已收盘收益相关 · 不是因果",
    calendar: "公开周历 · 北京时间",
    risk: "参考计算器 · 不下单",
    backtest: "手动回测 · 禁止前瞻",
    catalog: "产品分开标注 · 搜索按连续字符",
    alerts: "仅页面打开时检查",
    monitor: "Hyperliquid 公开数据",
    settings: "五种预设 · 五个参数模块",
  };
  context.textContent = titles[state.page] || "";
  paintWatch();
}

function paintWatch() {
  const box = document.querySelector("#watch");
  const assets = state.core?.assets || [
    {id: "BTC", name: "比特币", symbol: "BTCUSDT"},
    {id: "ETH", name: "以太坊", symbol: "ETHUSDT"},
    {id: "NVDA", name: "英伟达", symbol: "NVDA"},
    {id: "XAU", name: "现货黄金", symbol: "XAU/USD"},
  ];
  box.innerHTML = assets.map((asset) => {
    const quote = asset.quote || quoteOf(asset.symbol);
    const status = asset.connected ? (quote?.freshness || "连接中") : "未接通";
    return `<button type="button" data-go="charts" data-symbol="${esc(asset.symbol)}"><strong>${esc(asset.name)}</strong><span class="tiny">${esc(asset.symbol)} · ${esc(status)}</span></button>`;
  }).join("");
}

function updateLivePrices() {
  document.querySelectorAll("[data-live]").forEach((node) => {
    const quote = quoteOf(node.dataset.live);
    if (!quote) return;
    if (node.dataset.field === "price") node.textContent = fmt(quote.price, quote.price > 1000 ? 1 : 2);
    if (node.dataset.field === "fresh") node.innerHTML = pill(quote.freshness);
    if (node.dataset.field === "latency") node.textContent = latencyLine(quote);
    if (node.dataset.field === "age") node.textContent = quote.age_ms == null ? "—" : `${Math.round(quote.age_ms)} ms`;
  });
}

async function poll() {
  try {
    state.quotes = await api("/api/quotes");
    paintChrome();
    updateLivePrices();
    if (state.page === "alerts" && !document.hidden) evaluateAlerts();
  } catch (error) {
    badge.className = "badge down";
    badge.textContent = "Bitget价格流 · 已暂停";
  }
  setTimeout(poll, 400);
}

async function show(page) {
  state.page = PAGES.some(([id]) => id === page) ? page : "overview";
  history.replaceState(null, "", `#${state.page}`);
  paintChrome();
  view.innerHTML = `<div class="card hint">正在读取这一页。</div>`;
  try {
    view.innerHTML = await renderers[state.page]();
    await afterRender(state.page);
  } catch (error) {
    view.innerHTML = `<div class="card notice bad">${esc(error.message)}</div>`;
  }
}

const renderers = {
  async overview() {
    state.core = await api("/api/core");
    await loadCoreRadar();
    const product = state.market.product;
    let listing = "";
    if (state.market.exchange !== "bitget") {
      listing = `<div class="notice">未接通该交易所，不会用 Bitget 行情冒充。</div>`;
    } else if (product === "spot") {
      listing = `<div class="notice">现货簿没有并进这张表。主源是 Bitget USDT 永续，避免和现货价格混在一起。</div>`;
    } else {
      const data = await api(`/api/directory?exchange=bitget&product=${encodeURIComponent(product)}&sort=${state.market.sort}&q=${encodeURIComponent(state.market.query)}&page=${state.market.page}&page_size=20`);
      listing = `${rowsTable(data.rows)}${pager(data)}<p class="hint">本页 ${data.summary.rows} 条 · 数据异常 ${data.summary.anomaly} 个 · 过期 ${data.summary.stale} 个 · 未接通 ${data.summary.unconnected} 个。异常数只统计你现在看见的这些行。${esc(data.turnover_change)}</p>`;
    }
    return `${stanceStrip()}${coreCards()}${filters()}${listing}${radarCard()}`;
  },
  async charts() {
    const symbol = state.chart.symbol;
    if (symbol === "NVDA" || symbol === "XAU/USD" || symbol === "XAUUSD") {
      const core = state.core || await api("/api/core");
      state.core = core;
      const asset = core.assets.find((item) => item.symbol === symbol || (symbol.startsWith("XAU") && item.id === "XAU") || symbol === "NVDA" && item.id === "NVDA");
      return `${chartToolbar()}<div class="card notice">${esc(asset?.reason || "这个核心位没有已授权行情，图上不会画替代价格。")}</div>`;
    }
    const [chart, radar] = await Promise.all([
      api(`/api/chart?symbol=${encodeURIComponent(symbol)}&interval=${state.chart.interval}`),
      api(`/api/radar?symbol=${encodeURIComponent(symbol)}`).catch((error) => ({ok: false, error: error.message})),
    ]);
    state.chart.payload = chart;
    state.radar[symbol] = radar;
    if (!chart.ok) return `${chartToolbar()}<div class="card notice">${esc(chart.reason || chart.error)}</div>`;
    return `${chartToolbar()}<div class="layout"><div class="card"><div id="chart-host"><canvas id="chart"></canvas></div><p class="hint">${esc(chart.product_label)} · 最近已收盘 ${esc(chart.last_closed || "—")} · ${chart.forming ? "含一根未收盘 K 线，只作预警" : "当前绘制到已收盘"}</p>${chart.banner ? `<p class="notice">${esc(chart.banner)}</p>` : ""}</div>${chartSide(chart, radar)}</div>`;
  },
  async scan() {
    await loadCoreRadar();
    const data = await api(`/api/directory?sort=turnover&page_size=100&q=${encodeURIComponent(state.scanQuery)}`);
    const visible = data.rows;
    return `<div class="card"><h2>全市场扫描</h2>${stanceStrip()}<p class="hint">BTC、ETH、SOL、BNB 给出已收盘的做多、做空或观望。其他代码没有接通评分时写无法判断，不编造方向。</p><div class="row"><input class="search" id="scan-q" placeholder="搜索币种、股票、商品或代码" value="${esc(state.scanQuery)}"><button class="primary" data-act="scan" type="button">扫描成交额前100名</button></div><p class="hint">${esc(data.turnover_change)} 涨幅、跌幅和波动用的是同一份快照，不会把过期行标成实时。</p></div><div class="card">${rowsTable(visible, true)}${summaryLine(data.summary)}</div>`;
  },
  async analysis() {
    const symbol = CORE_PERPS.includes(state.chart.symbol) ? state.chart.symbol : "BTCUSDT";
    return `<div class="card"><h2>专业分析</h2><p class="hint">只分析已收盘 K 线。点按钮后才计算，不会自动跑长回测。分数不是胜率。</p><div class="row"><select id="an-symbol">${CORE_PERPS.map((item) => `<option ${item === symbol ? "selected" : ""}>${item}</option>`).join("")}</select><button class="primary" data-act="analyze" type="button">分析已收盘行情</button><button class="ghost" data-act="macro" type="button">读取宏观与斐波那契</button></div><div id="analysis-out" class="stack"></div></div>`;
  },
  async cross() {
    return `<div class="card"><h2>跨市场</h2><p class="hint">这一页比较两条已经接通的行情，不是产品目录。相关不是因果，页面不估计资金流。</p><div class="row"><select id="cross-left">${crossOptions("BTCUSDT")}</select><select id="cross-right">${crossOptions("ETHUSDT")}</select><select id="cross-interval"><option>1H</option><option>15m</option><option>4H</option><option>1D</option></select><button class="primary" data-act="cross" type="button">计算已收盘相关</button></div><div id="cross-out"></div></div>`;
  },
  async calendar() {
    const data = await api("/api/calendar");
    if (!data.ok) return `<div class="card notice bad">${esc(data.error)}<p>${esc(data.source || "")}</p></div>`;
    const impact = state.market.impact || "all";
    const events = data.events.filter((item) => impact === "all" || item.impact === impact);
    return `<div class="card"><h2>经济日历</h2><p class="hint">${esc(data.source)} · ${esc(data.timezone)}</p><div class="row">${["all", "High", "Medium", "Low"].map((item) => `<button class="chip ${impact === item ? "active" : ""}" data-impact="${item}" type="button">${item === "all" ? "全部" : item}</button>`).join("")}</div><table><thead><tr><th>北京时间</th><th>地区</th><th>事件</th><th>重要性</th><th>预测</th><th>前值</th><th></th></tr></thead><tbody>${events.map((item) => `<tr><td>${esc(item.time)}</td><td>${esc(item.country)}</td><td>${esc(item.title)}</td><td>${esc(item.impact)}</td><td>${esc(item.forecast)}</td><td>${esc(item.previous)}</td><td>${item.past ? "已过" : "未到"}</td></tr>`).join("") || `<tr><td colspan="7">这周的公开日历没有可显示的事件。</td></tr>`}</tbody></table></div>`;
  },
  async risk() {
    return `<div class="card"><h2>风险与仓位</h2><p class="hint">参考计算器。费用和滑点是假设值，不含资金费和借币息，不会读取真实账户，也不会下单。</p><div class="form-grid">${riskFields()}</div><div class="row"><button class="primary" data-act="risk" type="button">计算参考仓位</button></div><div id="risk-out"></div></div>`;
  },
  async backtest() {
    return `<div class="card"><h2>回测与记录</h2><div class="notice">样本内 70% 与样本外 30% 按时间切开。正式候选用已收盘 K 线，下一根开盘才模拟成交，同一根先处理止损。没有搜索最优参数，也不会把演示收益写进来。</div><div class="row"><select id="bt-symbol"><option>BTCUSDT</option><option>ETHUSDT</option></select><button class="primary" data-act="backtest" type="button">运行短样本回测</button><button class="ghost" data-act="archive" type="button">查看 2026-11-26 存档</button></div><div id="bt-out" class="stack"></div></div>`;
  },
  async catalog() {
    const data = await api(`/api/directory?q=${encodeURIComponent(state.catalog.query)}&product=${encodeURIComponent(state.catalog.product)}&exchange=${state.catalog.exchange}&sort=name&page=${state.catalog.page}&page_size=40`);
    const products = [["", "全部"], ["crypto_perp", "加密永续"], ["tokenized_equity", "代币化股票"], ["tokenized_index", "代币化指数"], ["tokenized_metal", "代币化贵金属"], ["tokenized_fx", "代币化外汇"], ["tokenized_commodity", "代币化商品"], ["cash_equity", "正股未接通"], ["spot_gold", "现货黄金未接通"], ["cfd", "CFD 未接通"]];
    return `<div class="card"><h2>多市场目录</h2><p class="hint">股票、代币化股票、CFD、加密合约和现货黄金分开标注。K 线未接通就写未接通，不叫数据异常。</p><div class="row"><select id="cat-exchange">${EXCHANGES.map(([id, label]) => `<option value="${id}" ${state.catalog.exchange === id ? "selected" : ""}>${label}</option>`).join("")}</select><select id="cat-product">${products.map(([id, label]) => `<option value="${id}" ${state.catalog.product === id ? "selected" : ""}>${label}</option>`).join("")}</select><input class="search" id="cat-q" value="${esc(state.catalog.query)}" placeholder="代码。XAU 不会匹配 FOXA"></div>${data.note ? `<div class="notice">${esc(data.note)}</div>` : ""}${rowsTable(data.rows)}${pager(data)}${summaryLine(data.summary)}</div>`;
  },
  async alerts() {
    const kinds = [["candidate", "多空候选"], ["regime", "趋势切换"], ["level", "支撑压力触达"], ["pullback", "突破/回踩确认"], ["rsi", "RSI 规则近似"], ["invalid", "候选失效"], ["sound", "声音"]];
    return `<div class="card"><h2>提醒</h2><div class="notice bad">云端推送、锁屏通知和后台监控都没有部署。关闭这个页面后不会继续检查，也不会发到手机。</div><p class="hint">可选 BTC、ETH 的页内提醒。英伟达和现货黄金没有行情，不能加入。</p><div class="row">${kinds.map(([id, label]) => `<label><span><input type="checkbox" data-alert="${id}" ${state.alerts.kinds.includes(id) || (id === "sound" && state.alerts.sound) ? "checked" : ""}> ${label}</span></label>`).join("")}</div><button class="primary" data-act="alerts" type="button">保存页内提醒</button><div id="alert-log" class="log">${state.alertLog.map((line) => `<div>${esc(line)}</div>`).join("") || "还没有页内记录。"}</div></div>`;
  },
  async monitor() {
    const watched = JSON.parse(localStorage.getItem("signal-lab-wallets") || "[]");
    const data = await api(`/api/monitor?sort=${state.market.board || "account"}&limit=12&watched=${encodeURIComponent(watched.join(","))}`);
    const positions = [...(data.watched_positions || []), ...(data.positions || [])];
    return `<div class="card"><h2>实盘监控</h2><div class="notice">${esc(data.notice || "")}</div><p class="hint">${esc(data.source || "")}${data.stale ? " · 榜单暂时刷新失败，下面使用上一次成功的公开快照" : ""} ${data.updated_at ? `· 更新 ${esc(data.updated_at)}` : ""} ${data.error ? `· ${esc(data.error)}` : ""}</p><h3>公开持仓</h3>${positionTable(positions)}<div class="row"><input id="wallet" class="search" placeholder="0x 公开地址，不要填私钥" maxlength="42"><button class="primary" data-act="wallet" type="button">加入自选并读取持仓</button></div><p class="tiny">自选地址存在这个浏览器里。刷新公开榜不会清掉它们。${esc((watched || []).join("、"))}</p><div id="wallet-out"></div><details><summary>公开榜账户价值（不是持仓）</summary><div class="row"><select id="board-sort"><option value="account">账户价值</option><option value="day">日收益率</option><option value="week">周收益率</option><option value="month">月收益率</option></select><button class="ghost" data-act="board" type="button">刷新公开榜</button></div><table><thead><tr><th>公开名称或地址</th><th>账户价值</th><th>日收益</th><th>周收益</th><th>月收益</th></tr></thead><tbody>${(data.rows || []).map((row) => `<tr><td>${esc(row.name || shortAddr(row.address))}</td><td>${fmt(row.account_value, 0)}</td><td>${pct(row.day_roi)}</td><td>${pct(row.week_roi)}</td><td>${pct(row.month_roi)}</td></tr>`).join("") || "<tr><td colspan='5'>榜单暂时没有数据。</td></tr>"}</tbody></table></details></div>`;
  },
  async settings() {
    const data = await api("/api/settings");
    state.settings = data;
    return settingsPage(data);
  },
};

function coreCards() {
  return `<div class="card"><h2>核心资产</h2><div class="split">${state.core.assets.map((asset) => {
    const quote = asset.quote;
    const price = asset.connected ? fmt(quote?.price, quote?.price > 1000 ? 1 : 2) : "—";
    const liveAttr = asset.connected ? ` data-live="${esc(asset.symbol)}" data-field="latency"` : "";
    return `<article class="card grow"><div class="row"><strong>${esc(asset.name)}</strong>${pill(asset.connected ? quote?.freshness : "数据源未接通")}</div><div class="name" data-live="${esc(asset.symbol)}" data-field="price">${price}</div><p class="meta">${esc(asset.symbol)} · ${esc(asset.product_label)}</p><ul class="facts"><li>来源：${esc(asset.source || "未接通")}</li><li>更新时间：${esc(asset.updated_at || "无")}</li><li>连接状态：${esc(asset.connection || "未连接")}</li><li>新鲜度：${esc(asset.freshness_label || "无行情")}</li><li${liveAttr}>${esc(asset.connected ? latencyLine(quote) : "无实测传输、中位或 P95。不引用替代产品的价格。")}</li>${asset.not_substitute ? `<li>${esc(asset.not_substitute)}</li>` : ""}</ul>${asset.reason ? `<p class="tiny">${esc(asset.reason)}</p>` : ""}<button class="ghost" type="button" data-go="charts" data-symbol="${esc(asset.symbol)}">查看图表</button></article>`;
  }).join("")}</div></div>`;
}

function msText(value) {
  return value == null || !Number.isFinite(Number(value)) ? "—" : String(Math.round(Number(value)));
}

function latencyLine(quote) {
  return `本次连接实测 传输 ${msText(quote?.transport_ms)} ms · 中位 ${msText(quote?.median_ms)} ms · P95 ${msText(quote?.p95_ms)} ms · 样本 ${quote?.samples ?? 0}。不是历史验收值。`;
}

function latencyBanner() {
  return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"].map((symbol) => `${symbol} ${latencyLine(quoteOf(symbol))}`).join("\n");
}

function filters() {
  const products = [["crypto_perp", "合约"], ["spot", "现货"], ["cash_equity", "股票"], ["tokenized_commodity", "大宗商品"], ["tokenized_index", "指数"], ["tokenized_fx", "外汇"], ["tokenized_metal", "贵金属"]];
  const sorts = [["turnover", "成交额最高"], ["change_desc", "涨幅最高"], ["change_asc", "跌幅最高"], ["range", "波动最高"], ["name", "名称排序"]];
  return `<div class="card stack"><div class="row">${EXCHANGES.map(([id, label]) => `<button class="chip ${state.market.exchange === id ? "active" : ""}" data-ex="${id}" type="button">${label}</button>`).join("")}</div><div class="row">${products.map(([id, label]) => `<button class="chip ${state.market.product === id ? "active" : ""}" data-product="${id}" type="button">${label}</button>`).join("")}</div><div class="row">${sorts.map(([id, label]) => `<button class="chip ${state.market.sort === id ? "active" : ""}" data-sort="${id}" type="button">${label}</button>`).join("")}<button class="chip" type="button" disabled title="快照没有这个字段">成交额增幅最大</button></div><div class="row"><input id="ov-q" class="search" placeholder="搜索币种、股票、商品或代码" value="${esc(state.market.query)}"></div></div>`;
}

function rowsTable(rows, scan = false) {
  if (!rows?.length) return `<p class="hint">没有符合条件的行。</p>`;
  return `<div class="list"><div class="head-row"><span></span><span>产品</span><span>24h 成交额</span><span>涨跌</span><span>新鲜度 / 价格</span></div>${rows.map((row) => `<div class="item"><button class="star ${state.favorites.includes(row.symbol) ? "on" : ""}" data-fav="${esc(row.symbol)}" type="button">★</button><div><div class="name">${esc(row.base || row.symbol)}</div><div class="meta">${esc(row.symbol)} · ${esc(row.product_label)}</div><div class="tiny">来源 ${esc(row.source || row.product_label || "未标注")} · 更新 ${esc(row.updated_label || "无")} · 连接 ${esc(row.connection || row.freshness || "未知")}</div>${row.note ? `<div class="tiny">${esc(row.note)}</div>` : ""}</div><div>${turnover(row.turnover)}</div><div>${change(row.change)}</div><div><div data-live="${esc(row.symbol)}" data-field="price">${priceText(row.symbol, row)}</div><div class="tiny"><span data-live="${esc(row.symbol)}" data-field="fresh">${pill(row.freshness)}</span> · ${esc(row.kline_status || "")}${scan ? ` · ${esc(state.radar[row.symbol]?.conclusion?.action || "无法判断")}` : ""}</div></div></div>`).join("")}</div>`;
}

function summaryLine(summary) {
  if (!summary) return "";
  return `<p class="hint">本页 ${summary.rows} 条 · 数据异常 ${summary.anomaly} 个 · 过期 ${summary.stale} 个 · 快照 ${summary.snapshot} 个 · 实时 ${summary.live} 个 · 未接通 ${summary.unconnected} 个。</p>`;
}

function pager(data) {
  return `<div class="pager"><button class="ghost" data-page="${data.page - 1}" type="button" ${data.page <= 1 ? "disabled" : ""}>上一页</button><span>${data.page} / ${data.pages}</span><button class="ghost" data-page="${data.page + 1}" type="button" ${data.page >= data.pages ? "disabled" : ""}>下一页</button></div>`;
}

const CORE_PERPS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"];

async function loadCoreRadar() {
  const rows = await Promise.all(CORE_PERPS.map(async (symbol) => {
    try {
      return await api(`/api/radar?symbol=${symbol}`);
    } catch (error) {
      return {ok: false, symbol, conclusion: {action: "无法判断", status: "数据不足", reason: error.message, score_long: null, score_short: null, higher_tf: "", provisional: ""}};
    }
  }));
  rows.forEach((item) => {
    if (item?.symbol) state.radar[item.symbol] = item;
  });
}

function stanceStrip() {
  return `<div class="split">${CORE_PERPS.map((symbol) => {
    const conclusion = state.radar[symbol]?.conclusion || {action: "无法判断", status: "数据不足", reason: "还没有已收盘评分", score_long: null, score_short: null, higher_tf: ""};
    const tone = conclusion.action === "做多" ? "up" : conclusion.action === "做空" ? "down" : "";
    return `<article class="card grow stance"><div class="name ${tone}">${esc(conclusion.action)}</div><div class="meta">${esc(symbol)} · ${esc(conclusion.status || "")}</div><p>多 ${fmt(conclusion.score_long, 0)} / 空 ${fmt(conclusion.score_short, 0)}</p><p class="tiny">${esc(conclusion.reason || "")}</p><p class="tiny">${esc(conclusion.higher_tf || "")} ${esc(conclusion.provisional || "")}</p></article>`;
  }).join("")}</div>`;
}

function radarCard() {
  const sample = CORE_PERPS.map((symbol) => state.radar[symbol]).find(Boolean);
  return `<div class="card" id="radar-card"><h2>方向说明</h2><p class="notice">${esc(sample?.gate_note || "4小时和1小时只作背景，不再单独否决低周期方向。")}</p><p class="notice">${esc(sample?.weights_note || "已保存的五项权重会进入当前评分。分数不是胜率，也不会自动下单。")}</p><p class="tiny">未收盘 K 线只作预警。环境识别只建议，不自动换预设。</p></div>`;
}

function chartToolbar() {
  const intervals = ["1m", "5m", "15m", "30m", "1H", "4H", "1D"];
  const layers = [["ema10", "EMA10"], ["ema120", "EMA120"], ["boll", "布林"], ["extra", "其他均线"], ["macd", "MACD"], ["rsi", "RSI"], ["volume", "成交量"]];
  return `<div class="card stack"><div class="row"><input id="chart-symbol" class="search" value="${esc(state.chart.symbol)}" placeholder="BTCUSDT、ETHUSDT、SOLUSDT、BNBUSDT"><button class="ghost" data-act="chart-symbol" type="button">打开</button></div><div class="row">${intervals.map((item) => `<button class="chip ${state.chart.interval === item ? "active" : ""}" data-interval="${item}" type="button">${item}</button>`).join("")}</div><div class="row">${layers.map(([id, label]) => `<button class="chip ${state.chart.layers[id] ? "active" : ""}" data-layer="${id}" type="button">${label}</button>`).join("")}<button class="ghost" data-act="fullscreen" type="button">全屏</button><button class="ghost" data-act="reset-chart" type="button">恢复 EMA10/120</button></div><p class="tiny">主图默认只有 EMA10 和 EMA120。布林、其他均线、MACD、RSI 和成交量按需打开，画在各自的区域。</p></div>`;
}

function chartSide(chart, radar) {
  const env = radar?.environment;
  const conclusion = radar?.conclusion;
  return `<aside class="card stack">${conclusion ? `<div class="name ${conclusion.action === "做多" ? "up" : conclusion.action === "做空" ? "down" : ""}">${esc(conclusion.action)}</div><p>多 ${fmt(conclusion.score_long, 0)} / 空 ${fmt(conclusion.score_short, 0)} · ${esc(conclusion.status || "")}</p><p class="tiny">${esc(conclusion.reason || "")}</p>` : ""}<h3>信号实验室 7.1 趋势工作台</h3><p>${esc(env ? `${env.regime} · ${env.reason}` : radar?.error || "环境建议尚未算出")}</p><p class="tiny">建议研究 ${esc(env?.suggestion || "—")}。不会自动切换。</p><p>正式状态：${esc(radar?.grade || "—")}</p><p class="tiny">${esc(radar?.pause_note || radar?.forming_note || "")}</p><h3>布林动态止盈止损</h3><p class="tiny">未收盘 K 线不能确认交易信号。下面的价位来自已收盘计划，只收研究参考，不会发单。</p><p>${esc(radar?.plan ? `方案 ${radar.plan.scheme} · 止损 ${fmt(radar.plan.stop, 4)} · 目标 ${fmt(radar.plan.target1, 4)}` : "当前没有可展示的已收盘计划。")}</p><p class="tiny">${esc(chart.warning || "")}</p></aside>`;
}

function crossOptions(selected) {
  const options = [["BTCUSDT", "BTCUSDT 加密永续"], ["ETHUSDT", "ETHUSDT 加密永续"], ["NVDA", "NVDA 正股（未接通）"], ["XAU/USD", "XAU/USD 现货（未接通）"], ["NVDAUSDT", "NVDAUSDT 代币化股票永续"], ["XAUUSDT", "XAUUSDT 代币化黄金永续"]];
  return options.map(([id, label]) => `<option value="${id}" ${id === selected ? "selected" : ""}>${label}</option>`).join("");
}

function riskFields() {
  const fields = [["equity", "账户权益", 10000], ["risk_pct", "风险比例 %", 1], ["entry", "进场价格", ""], ["stop", "止损价格", ""], ["leverage", "杠杆", 1], ["fee_pct", "单边手续费 %", 0.05], ["slip_pct", "单边滑点 %", 0.03], ["atr", "ATR", ""], ["atr_mult", "ATR 倍数", 1.5], ["min_rr", "最小净盈亏比", 1.5], ["min_qty", "最小数量步长", 0.0001]];
  const inputs = fields.map(([id, label, value]) => `<label>${label}<input id="risk-${id}" type="number" step="any" value="${value}"></label>`).join("");
  return `${inputs}<label>止损模式<select id="risk-mode"><option>ATR与结构取较宽</option><option>固定止损价</option></select></label>`;
}

function pct(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function shortAddr(value) {
  if (!value) return "—";
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

function positionTable(rows) {
  if (!rows?.length) return `<p class="hint">公开快照里没有持仓。不会编造仓位。</p>`;
  return `<table><thead><tr><th>名称或地址</th><th>币</th><th>方向</th><th>数量</th><th>开仓价</th><th>标记或折算价</th><th>杠杆</th><th>未实现盈亏</th><th>更新</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(row.label || row.name || shortAddr(row.address))}</td><td>${esc(row.coin)}</td><td class="${row.side === "多" ? "up" : "down"}">${esc(row.side)}</td><td>${fmt(row.size, 4)}</td><td>${fmt(row.entry, 4)}</td><td>${fmt(row.mark, 4)}</td><td>${esc(row.leverage ?? "—")}</td><td>${fmt(row.unrealized_pnl, 2)}</td><td>${esc(row.updated_at || "无")}</td></tr>`).join("")}</tbody></table>`;
}

function settingsPage(data) {
  const current = data.current;
  const presetColumns = Object.keys((data.presets.table || [{}])[0] || {}).filter((key) => key !== "参数");
  const table = (data.presets.table || []).map((row) => `<tr><th>${esc(row["参数"])}</th>${presetColumns.map((name) => `<td>${esc(row[name])}</td>`).join("")}</tr>`).join("");
  return `<div class="card stack"><h2>五种预设</h2><div class="notice">${esc(data.weights_note)}</div><div class="notice">${esc(data.gate_note)}</div><div class="notice">${esc(data.write_policy?.note || "")}</div><div class="row"><label>管理令牌<input id="admin-token" type="password" autocomplete="off" placeholder="只留在这个浏览器会话"></label><button class="ghost" data-act="save-token" type="button">记住令牌</button></div><p class="hint">这些是初始测试值，回测之前不能称为最优。应用预设只改工作副本，不覆盖已经保存的自定义配置，也不改杠杆和单笔风险。</p>${data.drifted ? `<div class="notice">${esc(data.drift_note)}</div>` : ""}<div class="row">${data.presets.names.map((name) => `<button class="chip ${current.applied_preset === name ? "active" : ""}" data-act="apply" data-name="${esc(name)}" type="button">${esc(name)}</button>`).join("")}</div><p class="tiny">当前：${esc(current.applied_preset)}。${esc(data.presets.notes[current.applied_preset] || data.presets.custom_note || "")}</p><table><thead><tr><th>参数</th>${presetColumns.map((name) => `<th>${esc(name)}</th>`).join("")}</tr></thead><tbody>${table}</tbody></table><div class="row"><input id="custom-name" placeholder="自定义配置名称" value="我的配置"><button class="ghost" data-act="save-custom" type="button">保存为自定义</button></div><p class="tiny">已保存：${esc((data.presets.custom_names || []).join("、") || "无")}</p></div>
  <div class="card">${module("1. 旧版 MA 与 RSI 快捷参数", shortcutFields(current), `<button class="ghost" data-act="sync-shortcut" type="button">保存快捷参数</button><p class="tiny">同步只写入节奏 EMA 和 RSI。长期 MA 留在这一组，避免覆盖短线 EMA。</p>`)}
  ${module("2. 标准日内评分与异动参数", intradayFields(current), `<p class="tiny">正式候选最低分、RVOL、异动和权重都在这里。权重合计 ${current.modules.intraday.weights.reduce((sum, item) => sum + item, 0)}。这五项已保存的权重会改变当前评分。</p>`)}
  ${module("3. 独立 EMA×布林策略参数", emaFields(current), `<p class="tiny">切换总预设不会改这一组的杠杆和单笔风险。</p>`)}
  ${module("4. 多周期回踩策略参数", pullFields(current))}
  ${module("5. 布林动态止盈止损参数", stopFields(current))}
  ${module("宏观周期与斐波那契", macroFields(current), `<p class="tiny">这组不替换上面五块。结论只建议，启动时不写已保存配置。</p>`)}
  ${module("独立交叉提醒", `<label>参数来源<select data-path="modules.cross.inherit"><option value="true" ${current.modules.cross.inherit ? "selected" : ""}>继承预设</option><option value="false" ${current.modules.cross.inherit ? "" : "selected"}>使用独立参数</option></select></label>`, `<p class="tiny">交叉提醒不替换五大模块。云端通知未部署。</p>`)}
  <div class="row"><button class="primary" data-act="save-settings" type="button">保存工作副本</button><button class="ghost" data-act="environment" type="button">只查看环境建议</button></div><div id="settings-note" class="hint"></div></div>`;
}

function module(title, body, extra = "") {
  return `<details open><summary>${title}</summary><div class="form-grid">${body}</div>${extra}</details>`;
}

function field(path, label, value, step = "any") {
  return `<label>${label}<input data-path="${path}" type="number" step="${step}" value="${value}"></label>`;
}

function check(path, label, value) {
  return `<label><span><input data-path="${path}" data-type="bool" type="checkbox" ${value ? "checked" : ""}> ${label}</span></label>`;
}

function choice(path, label, value, options) {
  return `<label>${label}<select data-path="${path}">${options.map((item) => `<option ${String(item) === String(value) ? "selected" : ""}>${esc(item)}</option>`).join("")}</select></label>`;
}

function shortcutFields(current) {
  const item = current.modules.shortcut;
  return field("modules.shortcut.short", "短期 MA", item.short, 1) + field("modules.shortcut.long", "长期 MA", item.long, 1) + field("modules.shortcut.rsi", "RSI 周期", item.rsi, 1);
}

function intradayFields(current) {
  const item = current.modules.intraday;
  const extra = current.extra;
  const ui = current.modules.ui;
  return [
    field("modules.intraday.ema.0", "节奏 EMA", item.ema[0], 1),
    field("modules.intraday.ema.1", "短线 EMA", item.ema[1], 1),
    field("modules.intraday.ema.2", "中线 EMA", item.ema[2], 1),
    field("modules.intraday.ema.3", "背景 EMA", item.ema[3], 1),
    field("modules.intraday.macd.0", "MACD 快线", item.macd[0], 1),
    field("modules.intraday.macd.1", "MACD 慢线", item.macd[1], 1),
    field("modules.intraday.macd.2", "MACD 信号", item.macd[2], 1),
    field("modules.intraday.rsi", "RSI 周期", item.rsi, 1),
    field("modules.intraday.atr", "ATR 周期", item.atr, 1),
    field("modules.intraday.boll", "BOLL 周期", item.boll, 1),
    field("modules.intraday.boll_mult", "BOLL 倍数", item.boll_mult),
    field("modules.intraday.volume", "成交量均值周期", item.volume, 1),
    field("modules.intraday.threshold", "正式候选最低分", item.threshold, 1),
    field("extra.early", "提前预警分数", extra.early, 1),
    field("extra.strong", "强候选分数", extra.strong, 1),
    field("modules.intraday.rvol", "交易确认 RVOL", item.rvol),
    field("modules.intraday.extreme_atr_pct", "极端 ATR% 暂停阈值", item.extreme_atr_pct),
    field("modules.intraday.shock_5m_pct", "5 分钟异动 %", item.shock_5m_pct),
    field("modules.intraday.shock_15m_pct", "15 分钟异动 %", item.shock_15m_pct),
    field("modules.intraday.shock_1h_pct", "1 小时异动 %", item.shock_1h_pct),
    field("modules.intraday.volume_shock", "量能异动 RVOL", item.volume_shock),
    field("modules.intraday.volume_high", "高等级量能 RVOL", item.volume_high),
    field("modules.intraday.weights.0", "权重 · 趋势结构", item.weights[0], 1),
    field("modules.intraday.weights.1", "权重 · 动量", item.weights[1], 1),
    field("modules.intraday.weights.2", "权重 · 成交量", item.weights[2], 1),
    field("modules.intraday.weights.3", "权重 · 形态", item.weights[3], 1),
    field("modules.intraday.weights.4", "权重 · 波动", item.weights[4], 1),
    field("extra.atr_stop", "ATR 止损倍数", extra.atr_stop),
    field("extra.minimum_rr", "最低净盈亏比", extra.minimum_rr),
    field("extra.structure", "结构窗口", extra.structure, 1),
    field("modules.ui.label_size", "图表标签字号", ui.label_size, 1),
    `<label>上涨颜色<input data-path="modules.ui.up" type="text" value="${esc(ui.up)}"></label>`,
    `<label>下跌颜色<input data-path="modules.ui.down" type="text" value="${esc(ui.down)}"></label>`,
    check("modules.ui.atr_adaptive", "ATR 自适应异动阈值（取固定阈值与 1.5×ATR% 中较高者）", ui.atr_adaptive),
  ].join("");
}

function emaFields(current) {
  const item = current.modules.ema_boll;
  return [
    check("modules.ema_boll.enabled", "独立策略启用", item.enabled),
    check("modules.ema_boll.only_two_lines", "仅显示两条策略线", item.only_two_lines),
    check("modules.ema_boll.show_levels", "显示本策略参考位", item.show_levels),
    check("modules.ema_boll.inherit", "继承预设的布林与评分周期", item.inherit),
    field("modules.ema_boll.ema", "策略 EMA", item.ema, 1),
    choice("modules.ema_boll.interval", "默认触发周期", item.interval, ["5m", "15m", "1H", "4H"]),
    field("modules.ema_boll.capital", "模拟资金 USDT", item.capital),
    field("modules.ema_boll.risk_pct", "单笔风险 %", item.risk_pct),
    field("modules.ema_boll.leverage", "杠杆", item.leverage),
    field("modules.ema_boll.atr_stop", "ATR 止损倍数", item.atr_stop),
    field("modules.ema_boll.fee_pct", "单边手续费 %", item.fee_pct),
    field("modules.ema_boll.slip_pct", "单边滑点 %", item.slip_pct),
  ].join("");
}

function pullFields(current) {
  const item = current.modules.pullback;
  return [
    check("modules.pullback.enabled", "启用回踩", item.enabled),
    check("modules.pullback.both_sides", "允许双向", item.both_sides),
    check("modules.pullback.show_levels", "显示参考线", item.show_levels),
    check("modules.pullback.inherit", "继承预设的指标周期", item.inherit),
    field("modules.pullback.capital", "模拟资金 USDT", item.capital),
    field("modules.pullback.risk_pct", "单笔风险 %", item.risk_pct),
    field("modules.pullback.leverage", "杠杆", item.leverage),
    field("modules.pullback.rsi_long", "做多 RSI 低位上限", item.rsi_long),
    field("modules.pullback.rsi_short", "做空 RSI 高位下限", item.rsi_short),
    field("modules.pullback.fee_pct", "单边手续费 %", item.fee_pct),
    field("modules.pullback.slip_pct", "单边滑点 %", item.slip_pct),
  ].join("");
}

function stopFields(current) {
  const item = current.modules.dynamic_stop;
  return [
    choice("modules.dynamic_stop.scheme", "计划方案", item.scheme, ["ATR/结构", "布林动态"]),
    field("modules.dynamic_stop.boll", "布林周期", item.boll, 1),
    field("modules.dynamic_stop.boll_mult", "标准差倍数", item.boll_mult),
    field("modules.dynamic_stop.atr", "ATR 周期", item.atr, 1),
    field("modules.dynamic_stop.minimum_rr", "最低净盈亏比", item.minimum_rr),
    field("modules.dynamic_stop.structure_buffer", "结构缓冲 ATR 倍数", item.structure_buffer),
    field("modules.dynamic_stop.atr_guard", "ATR 保护倍数", item.atr_guard),
    field("modules.dynamic_stop.second_atr", "第二目标 ATR 空间", item.second_atr),
    field("modules.dynamic_stop.max_stop_pct", "最大计划止损距离 %", item.max_stop_pct),
    field("modules.dynamic_stop.trail_start_r", "移动止损启动（R）", item.trail_start_r),
    choice("modules.dynamic_stop.trail_method", "追踪方法", item.trail_method, ["布林中轨", "ATR 跟踪"]),
    field("modules.dynamic_stop.trail_atr", "ATR 跟踪倍数", item.trail_atr),
    check("modules.dynamic_stop.breakeven", "成本保本位", item.breakeven),
    check("modules.dynamic_stop.trailing", "移动止损", item.trailing),
  ].join("");
}

function macroFields(current) {
  const macro = current.modules.macro;
  const fib = current.modules.fib;
  return [
    field("modules.macro.daily_fast", "日线快线", macro.daily_fast, 1),
    field("modules.macro.daily_slow", "日线慢线", macro.daily_slow, 1),
    field("modules.macro.weekly_fast", "周线快线", macro.weekly_fast, 1),
    field("modules.macro.weekly_slow", "周线慢线", macro.weekly_slow, 1),
    field("modules.macro.swing", "宏观摆动确认根数", macro.swing, 1),
    choice("modules.fib.interval", "斐波那契基准周期", fib.interval, ["1D", "1W"]),
    field("modules.fib.swing", "斐波那契摆动确认根数", fib.swing, 1),
    field("modules.fib.lookback", "斐波那契回看根数", fib.lookback, 1),
  ].join("");
}

function readSettingsForm() {
  const body = structuredClone(state.settings.current);
  view.querySelectorAll("[data-path]").forEach((input) => {
    const parts = input.dataset.path.split(".");
    let cursor = body;
    parts.slice(0, -1).forEach((part) => {
      const key = /^\d+$/.test(part) ? Number(part) : part;
      cursor = cursor[key];
    });
    const last = parts[parts.length - 1];
    const key = /^\d+$/.test(last) ? Number(last) : last;
    if (input.dataset.type === "bool") cursor[key] = input.checked;
    else if (input.type === "number") cursor[key] = input.value === "" ? null : Number(input.value);
    else if (input.tagName === "SELECT" && (input.value === "true" || input.value === "false")) cursor[key] = input.value === "true";
    else cursor[key] = input.value;
  });
  return body;
}

async function afterRender(page) {
  if (page === "charts") {
    const canvas = document.querySelector("#chart");
    const payload = state.chart.payload;
    if (canvas && payload?.bars) {
      const colors = state.settings?.current?.modules?.ui || {up: "#2fdb93", down: "#ff5c7a"};
      drawChart(canvas, payload.bars, state.chart.layers, colors);
    }
  }
}

function metricTable(title, presets) {
  const names = Object.keys(presets || {});
  if (!names.length) return "";
  const keys = Object.keys(presets[names[0]] || {});
  return `<h3>${esc(title)}</h3><table><thead><tr><th>预设</th>${keys.map((key) => `<th>${esc(key)}</th>`).join("")}</tr></thead><tbody>${names.map((name) => `<tr><th>${esc(name)}</th>${keys.map((key) => `<td>${esc(fmtMaybe(presets[name][key]))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function fmtMaybe(value) {
  if (typeof value === "number") return fmt(value, 2);
  return value ?? "—";
}

function resultTables(payload, archived = false) {
  const blocks = (payload.jobs || [{symbol: payload.symbol, presets: payload.presets, window: payload.window}]).map((job) => {
    const full = {};
    const inside = {};
    const outside = {};
    Object.entries(job.presets || {}).forEach(([name, item]) => {
      full[name] = item.full;
      inside[name] = item.is;
      outside[name] = item.oos;
    });
    return `<section class="card"><h3>${esc(job.symbol)} ${archived ? "存档" : "短样本"}</h3><p class="tiny">${esc(job.window?.start || "")} → ${esc(job.window?.end || "")} · 样本内 ${esc(job.window?.is_bars ?? "")} / 样本外 ${esc(job.window?.oos_bars ?? "")}</p>${metricTable("全样本", full)}${metricTable("样本内", inside)}${metricTable("样本外", outside)}</section>`;
  }).join("");
  return blocks;
}

async function evaluateAlerts() {
  if (document.hidden) return;
  const now = Date.now();
  if (now - (state.alertAt || 0) < 5000) return;
  state.alertAt = now;
  for (const symbol of state.alerts.symbols) {
    const radar = await api(`/api/radar?symbol=${symbol}`).catch(() => null);
    if (!radar) continue;
    const previous = state.radar[symbol];
    state.radar[symbol] = radar;
    const hits = [];
    if (state.alerts.kinds.includes("candidate") && radar.market_ok && String(radar.grade).includes("候选")) hits.push(`${symbol} 页内候选 ${radar.grade}`);
    if (state.alerts.kinds.includes("invalid") && previous?.market_ok && String(previous.grade).includes("候选") && !String(radar.grade).includes("候选")) hits.push(`${symbol} 候选已不再成立`);
    if (state.alerts.kinds.includes("regime") && previous?.environment && radar.environment && previous.environment.regime !== radar.environment.regime) hits.push(`${symbol} 环境描述从 ${previous.environment.regime} 变为 ${radar.environment.regime}`);
    const price = quoteOf(symbol)?.price;
    if (state.alerts.kinds.includes("level") && price && radar.support && (price <= radar.support || price >= radar.resistance)) hits.push(`${symbol} 最新价碰到已收盘结构位附近`);
    if (!hits.length) continue;
    const stamp = new Date().toLocaleString("zh-CN", {hour12: false});
    hits.forEach((line) => state.alertLog.unshift(`${stamp} ${line}`));
    state.alertLog = state.alertLog.slice(0, 40);
    if (state.page === "alerts") {
      const log = document.querySelector("#alert-log");
      if (log) log.innerHTML = state.alertLog.map((line) => `<div>${esc(line)}</div>`).join("");
    }
    if (state.alerts.sound && state.alerts.kinds.includes("sound")) beep();
  }
}

function beep() {
  const audio = new AudioContext();
  const osc = audio.createOscillator();
  osc.frequency.value = 660;
  osc.connect(audio.destination);
  osc.start();
  osc.stop(audio.currentTime + 0.08);
}

document.body.addEventListener("click", async (event) => {
  const go = event.target.closest("[data-go]");
  if (go) {
    if (go.dataset.symbol) state.chart.symbol = go.dataset.symbol;
    show(go.dataset.go);
    return;
  }
  const pageButton = event.target.closest("[data-page]");
  if (pageButton && !pageButton.disabled) {
    const target = state.page === "catalog" ? state.catalog : state.market;
    target.page = Number(pageButton.dataset.page);
    show(state.page);
    return;
  }
  const exchange = event.target.closest("[data-ex]");
  if (exchange) {
    state.market.exchange = exchange.dataset.ex;
    state.market.page = 1;
    show(state.page);
    return;
  }
  const product = event.target.closest("[data-product]");
  if (product) {
    state.market.product = product.dataset.product;
    state.market.page = 1;
    show(state.page);
    return;
  }
  const sort = event.target.closest("[data-sort]");
  if (sort) {
    state.market.sort = sort.dataset.sort;
    show(state.page);
    return;
  }
  const impact = event.target.closest("[data-impact]");
  if (impact) {
    state.market.impact = impact.dataset.impact;
    show("calendar");
    return;
  }
  const interval = event.target.closest("[data-interval]");
  if (interval) {
    state.chart.interval = interval.dataset.interval;
    show("charts");
    return;
  }
  const layer = event.target.closest("[data-layer]");
  if (layer) {
    const key = layer.dataset.layer;
    state.chart.layers[key] = !state.chart.layers[key];
    show("charts");
    return;
  }
  const favorite = event.target.closest("[data-fav]");
  if (favorite) {
    const symbol = favorite.dataset.fav;
    state.favorites = state.favorites.includes(symbol) ? state.favorites.filter((item) => item !== symbol) : [...state.favorites, symbol];
    localStorage.setItem("signal-lab-favorites", JSON.stringify(state.favorites));
    favorite.classList.toggle("on");
    return;
  }
  const action = event.target.closest("[data-act]");
  if (!action) return;
  try {
    await runAction(action.dataset.act, action);
  } catch (error) {
    view.insertAdjacentHTML("afterbegin", `<div class="notice bad">${esc(error.message)}</div>`);
  }
});

async function runAction(name, source) {
  if (name === "scan") {
    state.scanQuery = document.querySelector("#scan-q").value;
    show("scan");
  } else if (name === "chart-symbol") {
    state.chart.symbol = document.querySelector("#chart-symbol").value.trim().toUpperCase();
    show("charts");
  } else if (name === "fullscreen") {
    document.querySelector("#chart-host")?.requestFullscreen?.();
  } else if (name === "reset-chart") {
    state.chart.layers = {ema10: true, ema120: true, boll: false, extra: false, macd: false, rsi: false, volume: false};
    state.chart.interval = "15m";
    if (document.fullscreenElement) document.exitFullscreen();
    show("charts");
  } else if (name === "radar") {
    state.radar.BTCUSDT = await api("/api/radar?symbol=BTCUSDT");
    state.radar.ETHUSDT = await api("/api/radar?symbol=ETHUSDT");
    show("overview");
  } else if (name === "analyze") {
    const symbol = document.querySelector("#an-symbol").value;
    const radar = await api(`/api/radar?symbol=${symbol}`);
    document.querySelector("#analysis-out").innerHTML = analysisHtml(radar);
  } else if (name === "macro") {
    const symbol = document.querySelector("#an-symbol").value;
    const macro = await api(`/api/macro?symbol=${symbol}`);
    document.querySelector("#analysis-out").insertAdjacentHTML("beforeend", `<div class="card"><h3>宏观与斐波那契</h3><p>${esc(macro.summary || macro.error)}</p><p class="tiny">建议 ${esc(macro.suggestion || "—")}。自动应用：否。</p><p class="tiny">${esc(macro.note || "")}</p><pre class="tiny">${esc(JSON.stringify(macro.stages || macro.fibonacci || {}, null, 2))}</pre></div>`);
  } else if (name === "cross") {
    const left = document.querySelector("#cross-left").value;
    const right = document.querySelector("#cross-right").value;
    const interval = document.querySelector("#cross-interval").value;
    const data = await api(`/api/cross?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}&interval=${interval}`);
    document.querySelector("#cross-out").innerHTML = `<div class="card"><p>${esc(data.left.label)} 与 ${esc(data.right.label)}</p><p>样本 ${data.samples} 根已收盘 ${esc(data.interval)} · 同期相关 ${fmt(data.pearson, 3)} · 领先一根 ${fmt(data.lead_lag, 3)}</p><p class="notice">${esc(data.statement)}</p><p class="tiny">${esc(data.start)} → ${esc(data.end)}。资金流：不计算。</p></div>`;
  } else if (name === "risk") {
    const body = {};
    ["equity", "risk_pct", "entry", "stop", "leverage", "fee_pct", "slip_pct", "atr", "atr_mult", "min_rr", "min_qty"].forEach((key) => {
      const value = document.querySelector(`#risk-${key}`).value;
      body[key] = value === "" ? null : Number(value);
    });
    body.mode = document.querySelector("#risk-mode").value;
    const result = await api("/api/risk", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    document.querySelector("#risk-out").innerHTML = `<div class="card"><p>参考数量 ${fmt(result.qty, 6)} ${esc(result.side)} · 名义 ${fmt(result.notional, 2)} · 保证金 ${fmt(result.margin, 2)}</p><p>计划止损 ${fmt(result.stop, 4)} · 距离 ${fmt(result.distance, 4)} · 风险预算 ${fmt(result.risk_budget, 2)}</p><p class="tiny">${esc(result.leverage_note)} ${esc(result.cost_note)} ${esc(result.order_note)} ${esc(result.rr_note)} ${esc(result.min_qty_note || "")}</p></div>`;
  } else if (name === "backtest") {
    const symbol = document.querySelector("#bt-symbol").value;
    const out = document.querySelector("#bt-out");
    out.innerHTML = `<div class="hint">正在读取短样本。这一步不会自动重复。</div>`;
    const data = await api("/api/backtest", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({symbol})});
    out.innerHTML = `<div class="notice">${esc(data.sample)} ${esc(data.rules)} ${esc(data.lookahead)}</div>${resultTables(data)}`;
  } else if (name === "archive") {
    const data = await api("/api/backtest/archive");
    document.querySelector("#bt-out").innerHTML = `<div class="notice">${esc(data.note)}</div>${resultTables(data, true)}`;
  } else if (name === "board") {
    state.market.board = document.querySelector("#board-sort").value;
    show("monitor");
  } else if (name === "wallet") {
    const address = document.querySelector("#wallet").value.trim();
    const data = await api(`/api/monitor/wallet?address=${encodeURIComponent(address)}`);
    const saved = JSON.parse(localStorage.getItem("signal-lab-wallets") || "[]");
    if (address && !saved.includes(address)) localStorage.setItem("signal-lab-wallets", JSON.stringify([...saved, address].slice(0, 12)));
    show("monitor");
  } else if (name === "alerts") {
    const kinds = [...document.querySelectorAll("[data-alert]")].filter((input) => input.checked && input.dataset.alert !== "sound").map((input) => input.dataset.alert);
    state.alerts.kinds = kinds;
    state.alerts.sound = document.querySelector("[data-alert='sound']").checked;
    saveAlerts();
    state.alertLog.unshift(`${new Date().toLocaleString("zh-CN", {hour12: false})} 已保存。关闭页面后停止。`);
    show("alerts");
  } else if (name === "apply") {
    await api("/api/presets/apply", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: source.dataset.name})});
    show("settings");
  } else if (name === "save-settings") {
    const body = readSettingsForm();
    state.settings = await api("/api/settings", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    state.settings.current = state.settings.current || body;
    show("settings");
  } else if (name === "sync-shortcut") {
    await api("/api/settings", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(readSettingsForm())});
    await api("/api/settings/sync-shortcut", {method: "POST"});
    show("settings");
  } else if (name === "save-custom") {
    await api("/api/settings", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(readSettingsForm())});
    await api("/api/presets/save", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: document.querySelector("#custom-name").value})});
    show("settings");
  } else if (name === "save-token") {
    const value = document.querySelector("#admin-token").value.trim();
    if (value) sessionStorage.setItem("signal-lab-admin-token", value);
    else sessionStorage.removeItem("signal-lab-admin-token");
    document.querySelector("#settings-note").textContent = value ? "令牌只保存在这个浏览器会话，没有写入服务器。" : "已清除本机会话里的令牌。";
  } else if (name === "environment") {
    const radar = await api("/api/radar?symbol=BTCUSDT");
    document.querySelector("#settings-note").textContent = `${radar.environment.regime}：${radar.environment.reason}。可以研究 ${radar.environment.suggestion}。自动切换：否。`;
  }
}

document.querySelector("#refresh").addEventListener("click", () => show(state.page));
document.body.addEventListener("change", (event) => {
  if (event.target.id === "ov-q") {
    state.market.query = event.target.value;
    state.market.page = 1;
    show("overview");
  }
  if (event.target.id === "cat-q") {
    state.catalog.query = event.target.value;
    state.catalog.page = 1;
    show("catalog");
  }
  if (event.target.id === "cat-product") {
    state.catalog.product = event.target.value;
    state.catalog.page = 1;
    show("catalog");
  }
  if (event.target.id === "cat-exchange") {
    state.catalog.exchange = event.target.value;
    state.catalog.page = 1;
    show("catalog");
  }
});

window.addEventListener("hashchange", () => show(location.hash.slice(1)));
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
paintChrome();
poll();
show(location.hash.slice(1) || "overview");

function analysisHtml(radar) {
  const rows = (radar.history || []).map((item) => `<tr><td>${esc(item.time)}</td><td>${esc(radar.symbol)}</td><td>${esc(item.grade)}</td><td>${esc(item.side)}</td><td>${fmt(item.score_long, 0)} / ${fmt(item.score_short, 0)}</td><td>${esc(item.alert || "")}</td></tr>`).join("");
  const conclusion = radar.conclusion || {};
  return `<div class="notice"><strong>${esc(conclusion.action || "无法判断")}</strong> ${esc(conclusion.status || "")} · 多 ${fmt(conclusion.score_long, 0)} / 空 ${fmt(conclusion.score_short, 0)}</div><p>${esc(conclusion.reason || "")}</p><div class="notice">${esc(radar.weights_note || "")}</div><div class="notice">${esc(radar.gate_note || "")}</div><div class="notice">${esc(radar.pause_note || radar.forming_note || "分数来自最近一根已收盘 K 线。")}</div><p>${esc(radar.product_label)} · 预设 ${esc(radar.preset)} · ${esc(radar.grade)}</p><table><thead><tr><th>时间</th><th>合约</th><th>分级</th><th>方向</th><th>多/空分</th><th>预警</th></tr></thead><tbody>${rows}</tbody></table>`;
}
