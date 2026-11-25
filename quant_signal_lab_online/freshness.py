"""Read-time freshness. A stored row is never permanently live."""
from datetime import datetime, timezone


def now_ms(clock=None):
    if clock is not None:
        return int(clock)
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def classify(exchange_ms, received_ms, now_ms, ttl_ms):
    """Return a status that the UI can show without treating stale data as live.

    ``正常`` is the only status allowed to be described as an in-date official snapshot.
    Missing exchange time stays ``无行情时间`` even when the HTTP call just returned.
    """
    if received_ms is None or now_ms is None:
        return "缺失", None
    if exchange_ms is None:
        if now_ms - received_ms > ttl_ms:
            return "过期", None
        return "无行情时间", None
    latency = int(received_ms) - int(exchange_ms)
    if latency < -2000:
        return "时钟异常", None
    if int(now_ms) - int(exchange_ms) > ttl_ms:
        return "过期", latency if latency >= 0 else None
    return "正常", latency if latency >= 0 else None


def can_call_current(status):
    return status == "正常"


def status_sentence(status):
    if status == "正常":
        return "官方快照仍在有效期内。这是轮询结果，不是逐笔推送。"
    if status == "过期":
        return "已过期，不能当作实时持仓或实时行情。"
    if status == "无行情时间":
        return "接口没有返回可核对的行情时间，不能称为实时。"
    if status == "时钟异常":
        return "行情时间晚于接收时间，已停止把它当作有效快照。"
    if status == "历史记录":
        return "这是历史记录，不是当前挂单或当前持仓。"
    return "数据缺失或不可用，不能当作实时。"
