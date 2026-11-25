"""Evidence grades. Price agreement never upgrades a grade."""

CROSSCHECK_NOTE = "只对照 Bitget 公开最新价，不能证明该持仓属于该交易员，也不能证明收益。"

GRADES = {
    "A": "账户主人自己的官方只读接口。本版本不保存密钥，因此不会产生 A 类记录。",
    "B": "交易所公开接口，或公开地址上的官方账户状态。",
    "C": "社交平台上的公开说法。说法本身不是持仓证明。",
    "D": "截图或 OCR。不能当作已验证持仓或已验证收益。",
}


class SourceError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def screenshot_evidence():
    return {
        "grade": "D",
        "available": False,
        "label": "未接入 OCR。截图不能当作已验证持仓或收益。",
    }


def social_evidence():
    return {
        "grade": "C",
        "available": False,
        "label": "未接入社交帖子。公开说法不能当作已验证持仓。",
    }


def crosscheck(source_price, bitget_price):
    diff = None
    if source_price and bitget_price and source_price > 0 and bitget_price > 0:
        diff = (source_price - bitget_price) / bitget_price * 100
    return {"diff_pct": diff, "note": CROSSCHECK_NOTE, "upgrades_grade": False}


def bitget_symbol_for(symbol):
    if not symbol or ":" in symbol or symbol.startswith("@") or any(char.islower() for char in symbol):
        return None
    if "-" in symbol:
        quote = symbol.split("-")
        if len(quote) < 2 or quote[1] != "USDT" or not quote[0].isalnum():
            return None
        candidate = quote[0] + "USDT"
    elif symbol.endswith("USDT"):
        candidate = symbol
    elif symbol.isalnum():
        candidate = symbol + "USDT"
    else:
        return None
    if len(candidate) < 6 or len(candidate) > 24:
        return None
    return candidate
