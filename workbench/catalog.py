"""Product labels and catalog search.

Tokenized Bitget perps stay in the directory. They are never treated as cash
equity, spot gold, spot FX, or CFD.
"""
from __future__ import annotations

METAL = frozenset({"XAU", "XAG", "XAUT", "PAXG", "XPD", "XPT"})
COMMODITY = frozenset({"COPPER", "NATGAS"})
FX = frozenset({
    "EURUSD", "GBPUSD", "USDJPY", "USDBRL", "DXY", "AUDUSD", "NZDUSD",
    "USDCAD", "USDCHF", "USDTRY", "USDMXN",
})
INDEX = frozenset({
    "QQQ", "SPY", "SP500", "NDX100", "IWM", "DIA", "DIASTOCK", "QQQE",
    "TQQQ", "SQQQ", "SMH", "SOXX", "SOXL", "SOXS", "IBB", "XLE", "XLK",
    "XLU", "XLV", "XBI", "KWEB", "EWJ", "EWY", "EWT", "EWZ", "EWH", "INDA",
    "HSI", "JP225", "KR200",
})
ALIASES = {
    "GOLD": frozenset({"XAU", "XAUT", "PAXG", "XAUUSD"}),
    "黄金": frozenset({"XAU", "XAUT", "PAXG", "XAUUSD"}),
    "NVIDIA": frozenset({"NVDA"}),
    "英伟达": frozenset({"NVDA"}),
}


def norm_symbol(value):
    return (value or "").upper().replace("/", "").replace("-", "").replace(" ", "")


def classify_contract(base, is_rwa):
    """Return a stable class and a label that names the actual product."""
    base = (base or "").upper()
    rwa = str(is_rwa or "").upper() == "YES"
    if not rwa:
        return {
            "product_class": "crypto_perp",
            "product_label": "加密货币 USDT 永续",
            "cash_equivalent": False,
            "kline_status": "永续K线可请求",
        }
    if base in METAL:
        return {
            "product_class": "tokenized_metal",
            "product_label": "代币化贵金属永续 · 不是现货",
            "cash_equivalent": False,
            "kline_status": "永续K线可请求",
        }
    if base in COMMODITY:
        return {
            "product_class": "tokenized_commodity",
            "product_label": "代币化商品永续 · 不是现货",
            "cash_equivalent": False,
            "kline_status": "永续K线可请求",
        }
    if base in FX:
        return {
            "product_class": "tokenized_fx",
            "product_label": "代币化外汇永续 · 不是即期外汇",
            "cash_equivalent": False,
            "kline_status": "永续K线可请求",
        }
    if base in INDEX:
        return {
            "product_class": "tokenized_index",
            "product_label": "代币化指数永续 · 不是指数现货",
            "cash_equivalent": False,
            "kline_status": "永续K线可请求",
        }
    return {
        "product_class": "tokenized_equity",
        "product_label": "代币化股票永续 · 不是正股",
        "cash_equivalent": False,
        "kline_status": "永续K线可请求",
    }


def gap_rows():
    """Core products that do not have an authorized feed. Prices stay empty."""
    return [
        {
            "symbol": "NVDA",
            "base": "NVDA",
            "price": None,
            "turnover": None,
            "change": None,
            "range_pct": None,
            "spread_pct": None,
            "funding": None,
            "timestamp": None,
            "product_class": "cash_equity",
            "product_label": "美股正股",
            "cash_equivalent": True,
            "kline_status": "未接通",
            "freshness": "数据源未接通",
            "quote_state": "未接通",
            "note": "未授权 Alpaca 或其他正股行情。NVDAUSDT 是 Bitget 代币化股票永续，不能代替这只正股。",
        },
        {
            "symbol": "XAU/USD",
            "base": "XAUUSD",
            "price": None,
            "turnover": None,
            "change": None,
            "range_pct": None,
            "spread_pct": None,
            "funding": None,
            "timestamp": None,
            "product_class": "spot_gold",
            "product_label": "现货黄金",
            "cash_equivalent": True,
            "kline_status": "未接通",
            "freshness": "数据源未接通",
            "quote_state": "未接通",
            "note": "没有已授权的专业黄金现货行情。XAUUSDT、XAUTUSDT、PAXGUSDT 是不同产品，不在这里报价。",
        },
        {
            "symbol": "CFD",
            "base": "CFD",
            "price": None,
            "turnover": None,
            "change": None,
            "range_pct": None,
            "spread_pct": None,
            "funding": None,
            "timestamp": None,
            "product_class": "cfd",
            "product_label": "CFD",
            "cash_equivalent": False,
            "kline_status": "未接通",
            "freshness": "数据源未接通",
            "quote_state": "未接通",
            "note": "未接入 CFD 行情，也不会用永续合约冒充。",
        },
    ]


def _bases(symbol, base):
    """Base tokens only. Do not let the quote currency complete a match.

    FOXAUSDT contains the letters XAU only because FOXA is glued to USDT.
    That is not a gold contract, so the quote suffix is removed before matching.
    """
    tokens = []
    base_n = norm_symbol(base)
    sym = norm_symbol(symbol)
    if base_n:
        tokens.append(base_n)
    if sym.endswith("USDT") and len(sym) > 4:
        tokens.append(sym[:-4])
    elif sym.endswith("USD") and len(sym) > 3:
        tokens.append(sym)
        tokens.append(sym[:-3])
    elif sym:
        tokens.append(sym)
    if sym:
        tokens.append(sym)
    return [token for token in dict.fromkeys(tokens) if token]


def query_hits(query, symbol, base):
    """Match a base coin or a full code. XAU must not hit FOXAUSDT."""
    raw = (query or "").strip()
    q = norm_symbol(raw)
    if not q and not raw:
        return True
    tokens = _bases(symbol, base)
    if q and any(token == q or token.startswith(q) for token in tokens):
        return True
    alias = ALIASES.get(raw) or ALIASES.get(q)
    if alias and any(token in alias for token in tokens):
        return True
    return False


def search_rank(query, row):
    q = norm_symbol(query)
    sym = norm_symbol(row.get("symbol"))
    base = norm_symbol(row.get("base"))
    if row.get("product_class") in {"cash_equity", "spot_gold", "cfd", "spot_fx"}:
        group = 0
    else:
        group = 1
    if q and (base == q or sym == q):
        level = 0
    elif q and (base.startswith(q) or sym.startswith(q)):
        level = 1
    else:
        level = 2
    return (group, level, sym)


def ticker_freshness(quote_state):
    return {"正常": "快照", "过期": "过期", "缺失": "数据异常", "时钟异常": "数据异常"}.get(quote_state, "数据异常")


def overlay_live(rows, snaps):
    """Replace BTC/ETH list prices with the same snapshot the cards use."""
    snaps = snaps or {}
    output = []
    for row in rows:
        item = dict(row)
        snap = snaps.get(item.get("symbol"))
        if not snap:
            output.append(item)
            continue
        item["origin"] = snap.get("origin")
        item["age_ms"] = snap.get("age_ms")
        item["transport_ms"] = snap.get("transport_ms")
        item["received"] = snap.get("received")
        item["exchange_ts"] = snap.get("timestamp")
        item["official_candidates_ok"] = bool(snap.get("official_candidates_ok"))
        item["freshness"] = snap.get("freshness") or item.get("freshness")
        if snap.get("price") is not None:
            item["price"] = snap["price"]
            item["bid"] = snap.get("bid")
            item["ask"] = snap.get("ask")
            item["price_source"] = snap.get("origin") or "live"
        else:
            item["price"] = None
            item["price_source"] = "unavailable"
        output.append(item)
    return output


def filter_catalog(rows, gaps, query="", product="", exchange="bitget"):
    if exchange not in (None, "", "bitget"):
        return [], "未接通该交易所，不会用 Bitget 行情冒充。"
    pool = list(rows) + list(gaps or [])
    if product and product not in {"", "all"}:
        pool = [row for row in pool if row.get("product_class") == product]
    if query and query.strip():
        pool = [row for row in pool if query_hits(query, row.get("symbol"), row.get("base"))]
        pool.sort(key=lambda row: search_rank(query, row))
    return pool, None


def summarize(rows):
    def count(name):
        return sum(1 for row in rows if row.get("freshness") == name)

    return {
        "rows": len(rows),
        "anomaly": count("数据异常"),
        "stale": count("过期"),
        "live": count("实时"),
        "delayed": count("延迟"),
        "snapshot": count("快照"),
        "unconnected": count("数据源未接通"),
        "down": sum(1 for row in rows if row.get("freshness") in {"断线", "不可用", "权限不足"}),
    }


def badge_for(btc, eth):
    snaps = [btc or {}, eth or {}]
    if any(item.get("price") is None or item.get("freshness") in {"断线", "不可用", "过期", "权限不足", "数据异常"} for item in snaps):
        return {"level": "down", "text": "Bitget价格流 · 已暂停"}
    if all(item.get("freshness") == "实时" and item.get("origin") == "WebSocket" for item in snaps):
        return {"level": "live", "text": "Bitget价格流 · 实时"}
    return {"level": "delay", "text": "Bitget价格流 · 延迟"}


def freshness_from_status(status):
    if status == "实时":
        return "实时"
    if status == "延迟":
        return "延迟"
    if status == "过期":
        return "过期"
    if status == "权限不足":
        return "权限不足"
    if status == "不可用":
        return "不可用"
    if status == "断线":
        return "断线"
    return status or "断线"
