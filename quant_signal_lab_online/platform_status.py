"""Module and venue states shown by the research platform.

``完成`` means the stated public read works in this build. It does not mean the
original full product wish is finished.
"""

MODULES = (
    {"id": "x_intel", "name": "X 情报台", "state": "阻塞",
     "reason": "本次环境打开 X 官方文档返回 HTTP 403。读取公开帖需要所有者自己的开发者凭证，凭证不能写入仓库。"},
    {"id": "researchers", "name": "研究者库", "state": "部分",
     "reason": "可以在本地数据库录入、搜索、分类和收藏。没有外部研究者名录，页面不会预填人物。"},
    {"id": "articles", "name": "文章收录", "state": "部分",
     "reason": "可以保存原文链接和手工中文摘要。自动摘要需要所有者自己的模型接口，本版本不调用模型。"},
    {"id": "github", "name": "GitHub 开源发现", "state": "完成",
     "reason": "读取公开仓库元数据并给出本地许可、维护、质量和兼容评分。不克隆、不运行第三方代码，安全分留空。"},
    {"id": "meme", "name": "Meme 早期发现", "state": "阻塞",
     "reason": "没有已核实的官方安全数据源。不会把 Bitget 小市值合约伪装成 meme 雷达。"},
    {"id": "whale", "name": "链上巨鲸 / 聪明钱", "state": "部分",
     "reason": "只支持用户粘贴的 Hyperliquid 地址，数据来自官方 info。没有聪明钱标签，也不会预置地址。"},
    {"id": "hyperliquid", "name": "Hyperliquid 交易员", "state": "部分",
     "reason": "官方 info 可按地址读取公开账户状态。昵称榜不在已读到的官方 info 文档中，因此没有做。"},
    {"id": "structure", "name": "订单流、资金费、持仓量、成交量", "state": "部分",
     "reason": "Bitget 官方行情可看资金费、持仓量、最近公开成交和最优买卖档。不是完整历史订单流。"},
    {"id": "backtest", "name": "回测与参数", "state": "部分",
     "reason": "原有回测和模拟成交保留。五套策略可单独调整并写入研究库。均值回归只进入研究回测，模拟服务仍走原来的趋势评分。"},
    {"id": "assistant", "name": "AI 研究助手", "state": "部分",
     "reason": "页面只解释本地规则和证据等级。没有模型密钥，不会生成看起来像模型结论的文字。"},
    {"id": "tasks", "name": "任务与版本", "state": "部分",
     "reason": "本地任务、方案版本和模块状态可记录。没有自动发布，也不能从这里合并到 main。"},
)

VENUES = (
    {"id": "hyperliquid", "name": "Hyperliquid", "state": "部分", "grade": "B",
     "reason": "公开地址的 clearinghouseState、userFills、portfolio、allMids 可用。不是账户主人单独授权的只读 Key，所以不是 A 类。"},
    {"id": "okx", "name": "OKX", "state": "部分", "grade": "B",
     "reason": "公开带单榜和 uniqueCode 当前/历史带单可用。榜单字段没有最大回撤。私有持仓接口未接。"},
    {"id": "bitget", "name": "Bitget 跟单", "state": "阻塞", "grade": "A",
     "reason": "公开行情可用，但查询他人跟单资料的接口需要 ACCESS_KEY。本版本不签名、不读取密钥。"},
    {"id": "binance", "name": "Binance", "state": "阻塞", "grade": "A",
     "reason": "本环境访问 fapi.binance.com 和 api.binance.com 返回 HTTP 451。已公开的跟单文档也不能查询他人持仓。"},
    {"id": "telegram", "name": "Telegram", "state": "阻塞", "grade": "C",
     "reason": "Bot API 只能收到机器人所在聊天之后的新消息，更新最多保留约 24 小时。不能拉取任意公开频道历史，本版本也不读取 Bot Token。"},
    {"id": "x", "name": "X", "state": "阻塞", "grade": "C",
     "reason": "官方文档本次未能打开，且需要所有者的开发者凭证。不抓取网页。"},
)


def blocked_ids():
    return tuple(item["id"] for item in MODULES if item["state"] == "阻塞")
