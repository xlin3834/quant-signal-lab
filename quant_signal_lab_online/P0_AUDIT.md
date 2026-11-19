# Quant Signal Lab 5.0 Ultimate 开发记录

## 基线审计

审计基线为远程 `main` 的 `25c3e63`。虽然此前以为 4.0 尚未执行，Git 历史和文件内容显示 4.0 已合并。生产页面版本需独立验证。

| 检查项 | 仓库实际情况 |
| --- | --- |
| Streamlit 与配置 | 原入口 `quant_signal_lab_online/app.py`，`ui.py` 负责工作台，仓库根目录 `.streamlit/config.toml` 设置深色主题 |
| 行情、缓存与历史 | `market_data.py` 连接 Bitget 现货三个品种，限速重试、时间戳与缺口检查；近期图表缓存 40 秒；历史端点每页 200 根 |
| 指标与信号 | `indicators.py` 有 EMA、BOLL、RSI、MACD、ATR、均量和支撑压力；`signals.py` 按已收盘时间对齐四周期，按规则评分并生成结构位计划 |
| 回测 | `backtest.py` 支持成本、跳空止损、分段止盈、保守同根处理、70/30 时间切分；`legacy.py` 保留旧策略 |
| 模拟服务与持久化 | `paper.py` 有 SQLite 去重、仓位、日志、日亏损与连亏熔断、CLI 暂停；部署需独立常驻进程 |
| 依赖、测试、部署 | Streamlit、pandas、NumPy、requests 与 Plotly；15 项既有单元测试；Dockerfile 和中文部署说明 |

### 实际延迟原因与缺陷

- 基线界面刷新周期为 45 秒，排行榜和历史图表各自缓存约 40 秒。旧页面没有 WebSocket，公开成交价是 REST 快照，网络请求和浏览器重绘另有开销。
- 15 分钟确认信号等到 15 分钟 K 线收盘，这是策略约束。4 小时趋势也等待高周期收盘，与传输延迟是两个口径。
- 基线没有独立行情接收时间、延迟分位数、官方两端点 OHLC 比对或实时未收盘 K 线。
- 4.0 已有主要功能，但尚未证明旧线上版本的实际运行、真机交互或持续后台可用性。回测仅有训练／样本外二段，模拟服务仅定义单交易对和已收盘撮合。GitHub 部署面板与真实网页应分别核对。
- 5.0 未具备正式验收的能力包括动态合约与分层订阅、实时未收盘 K 线、形态库、更多验证样本、美股和指数授权、跨市场时钟对齐，以及正式 24 小时部署。各项按以下阶段验收。

## 分阶段交付

| 阶段 | 范围及依赖 |
| --- | --- |
| P0 | 原现货行情增加独立 WebSocket 成交快照、陈旧保护、时差诊断与 REST 双端点比对，保持原入口 |
| P1 | 依赖 P0 的接入与监控，接入动态 USDT 永续合约规格、排行榜、搜索、自选和流动性筛选 |
| P2 | 依赖统一元数据和已确认周期边界，加入实时未收盘 K 线与重连补齐，并完善指标交互 |
| P3 | 依赖 P2 收盘确认和合约规格，新增数学形态、信号去重及最小下单单位约束 |
| P4 | 依赖 P3 策略接口，加入资金费、滚动／验证窗口、多标的与策略比较；当前成分回测明确存活偏差 |
| P5 | 依赖 P4 成交规则，独立模拟运行、复原、停机保护和审计，禁止真实订单 |
| P6 | 完成美股、ETF、现金指数授权和交易日历验收后，启用美股与跨市场分析 |
| P7 | 全链路重启、断线、多用户、浏览器布局、监控和上线验收 |

## 供应商及部署预算待确认

- Bitget 官方公共 REST/WS 用于现货及永续。公共行情读取不需要交易密钥。
- Massive 个人 Stocks Basic 免费且日终；Starter 约 $29/月、15 分钟延迟；Advanced 约 $199/月、实时报价。指数套餐免费档覆盖有限，Starter 约 $49/月延迟，Advanced 约 $99/月实时，需另确认 S&P 500、Nasdaq Composite、DJIA 的套餐、交易所与展示权限。免费档 REST 频率有限；付费档的历史年限、流式和盘前盘后覆盖按套餐确认。个人套餐不许可向他人再分发，公开展示需商业许可。
- Alpaca Basic 免费，IEX 单交易所、标准 REST 约 200 请求/分钟；Algo Trader Plus 约 $99/月，完整 SIP 和更高请求率。历史条、延迟 SIP、扩展时段及 WebSocket 权限按订阅确认，IEX 与 SIP 的成交量口径不同。标准 Alpaca API 数据禁止再分发，公开使用需独立书面许可。
- 免费自托管可用现有设备加持久磁盘，但网络和电源由运营者维护。付费常驻方案可选 VPS，一台主机运行行情服务、模拟服务和 SQLite 持久卷，经 TLS 反向代理提供只读行情；参考 Hetzner CX23 部分地区约 €4–€7/月，未含税、备份和授权行情费用。Streamlit 免费托管维持原网址，页面可连接已部署的只读行情网关，无法保证自己长期运行后台线程。
- 未购买、开通或配置任何付费服务，也未提交供应商凭据。

来源：[Bitget](https://www.bitget.com/zh-CN/docs/catalog/classic-spot-market/classic-spot-market) · [Massive 价格](https://massive.com/pricing?product=indices) · [Massive 授权](https://massive.com/knowledge-base/article/how-can-i-redistribute-massives-market-data) · [Alpaca](https://docs.alpaca.markets/us/docs/about-market-data-api) · [Alpaca 再分发](https://alpaca.markets/support/redistribute-alpaca-api) · [VPS 价格](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)

## P0 验证记录（2026-11-20，中国标准时间）

实现范围：`live_market.py` 提供独立现货 ticker WebSocket，含按需订阅、120 秒释放、心跳、失序／重复和过期过滤、断线指数退避及 HTTP 健康检查。`quote_ui.py` 显示运输来源、成交价、交易所时间、系统接收时间、页面取样时差与网关分位数。保留现货 K 线、回测、策略、CSV 和模拟流程。`comparison.py`、`verify_market.py` 实现参考 CSV 差异报告及官方 REST 双端点检查。

- 单元测试 22 项通过，含原有 15 项、7 项 P0 测试。模拟断网测试验证重新连接并重新订阅；没有为了测试中断生产服务。
- 官方 `candles` 与 `history-candles` 比较 BTC、ETH、SOL 的 5m、15m、1H、4H、1D。15 组各匹配 100 根已收盘 K 线，共 1,500 根。开盘时间、收盘时间、OHLC 和基础币成交量在本次窗口内无差异。此测试没有比较交易所网页内部的聚合器，也没有覆盖未收盘 K 线。
- 官方公开 WebSocket 本次短样本（交易所 ticker 时间到本地接收；毫秒）：BTC 19 条，中位 85、P95 120、最大 129；ETH 20 条，中位 86、P95 119.45、最大 128；SOL 21 条，中位 85、P95 119、最大 128。指标含主机和交易所时钟误差。ETH 顺序采样的 REST 和 WS 成交价分别为 1786.04 和 1785.99，事件时间不同，相差 604 ms，不能当作同一时刻的价格偏差。
- 用真实网关验证了 `/health`、品种校验、Streamlit WebSocket 标签、币种与周期切换及图表。无网关时采用明确标记的 REST 快照。
- 原网址匿名访问返回 HTTP 303 并进入 Streamlit 登录。公开探测无法确认登录后的页面内容。没有配置线上网关、改变页面私密性、购买主机或开通付费数据。
- 独立服务的实时更新范围只包括 ticker。未收盘 K 线增量、K 线断线补齐、全合约工作台和股票均待后续阶段。线上实际延迟和长时间运行能力仍需带权限的线上验收。

续接：P1–P6 的部分探索代码保存在本地分支 `codex/qsl5-exploration`（`fd06552`），没有推送，也不是已验收版本。接手时首先基于 P0 后的远程 `main` 开新工作分支，逐个复用经验证的模块。探索期间 Bitget 合约接口返回 890 个状态正常的 USDT 永续（数量随市场变动）；验证了单一 BTC 永续 WS ticker。日线近期端点的实际可返回深度小于请求值，需在 P1/P2 继续核对分页和新品历史保底。股票和指数模块未使用有权限的真实数据跑通，暂不启用。
