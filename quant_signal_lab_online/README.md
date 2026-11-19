# Quant Signal Lab 4.0

Streamlit 中文市场工作台。Bitget 公开现货行情覆盖 BTCUSDT、ETHUSDT、SOLUSDT。指标、历史回测和后台账户都用于研究与模拟，无 API 密钥或下单接口。

## Streamlit Community Cloud

现有应用保持原仓库、原 `main` 分支与入口 `quant_signal_lab_online/app.py`，更新同一应用即可继续使用原网址。依赖位于入口旁的 `requirements.txt`，主题配置位于仓库根目录。若出现 Bitget 网络或数据异常，页面会提示并停止生成信号。

本地从仓库根目录启动：

```bash
pip install -r quant_signal_lab_online/requirements.txt
streamlit run quant_signal_lab_online/app.py
```

## 行情、评分与回测

- 图表支持 5 分钟、15 分钟、1 小时、4 小时和日线。信号的触发周期固定为 15 分钟。4 小时判定主趋势，1 小时确认方向，5 分钟辅助形态。
- 只有已收盘 K 线用于指标和信号。高周期通过实际收盘时间向后匹配。指数平滑指标使用固定长度的历史预热，避免滚动下载窗口改变已预热的数值。缺少行情、时间戳过期或序列不连续时停止生成新信号。
- 评分为五项规则权重之和。默认 65 分及以上为候选，80 分及以上为强候选，还须满足方向和结构位盈亏比约束。评分不代表交易方向概率。止损用先前的短窗结构位，两段目标用较长窗口的高低点固定，按手续费与滑点重新计算盈亏比，开盘跳空使盈亏比不足时取消。
- 高级回测支持 3,000 至 5,000 根 15 分钟 K 线，另取预热期和多周期历史。Bitget 现货历史端点每页最多 200 根，逐页检查时间序列。
- 样本按时间分为 70% 样本内和 30% 样本外。阈值优化只使用样本内，独立本金从各段起点开始，样本外沿用先前已知行情计算指标。阈值排名使用计成本的期末盈亏，仍可能过拟合。
- 下一根开盘模拟成交。同根触及止损与止盈时，先按止损成交，跳空止损按不利开盘价计价。止盈一平一半，止盈二平余量。末尾未平仓按扣除预计退出成本的市价计入权益，交易次数与胜率按已平仓头寸统计。做空是研究中对称的 1 倍模拟，不包含借币、资金费率或实盘撮合。
- 保留原版 EMA 交叉模拟回测，以及 K 线和交易记录 CSV 导出。

[Bitget 经典现货行情接口](https://www.bitget.com/zh-CN/docs/catalog/classic-spot-market/classic-spot-market)

## 独立模拟服务

Streamlit Community Cloud 页面不承载持续交易循环或持久磁盘。下面的服务需部署到常驻主机或容器，并由进程管理器配置自动重启。为该服务挂载持久目录。一个数据库对应一个交易对和一个模拟账户。公开页面的账户区域只读。需要在界面查看状态时，将 Streamlit 部署在能够读取同一持久文件的主机，并设置相同的 `QSL_PAPER_DB`。

```bash
cd quant_signal_lab_online
mkdir -p data
export QSL_PAPER_DB="$PWD/data/paper.sqlite3"
python paper.py --symbol BTCUSDT run
```

在服务主机的另一个终端使用同一数据库路径：

```bash
python paper.py --db data/paper.sqlite3 status
python paper.py --db data/paper.sqlite3 pause
python paper.py --db data/paper.sqlite3 stop
python paper.py --db data/paper.sqlite3 reset-streak
python paper.py --db data/paper.sqlite3 resume
```

也可在 `quant_signal_lab_online/` 构建镜像并独立启动后台：

```bash
docker build -t quant-signal-lab .
docker volume create qsl-paper
docker run -d --name qsl-paper --restart unless-stopped --no-healthcheck \
  -v qsl-paper:/data -e QSL_PAPER_DB=/data/paper.sqlite3 \
  quant-signal-lab python paper.py --symbol BTCUSDT run
docker exec qsl-paper python paper.py status
```

默认本金 1,000 USDT，单笔风险预算 0.5%，UTC 每日权益回撤达到当日初始权益的 2% 时暂停新开仓，连续三笔亏损时锁定新开仓，单账户最多一笔 1 倍名义仓位。止损跳空可能超过风险预算。暂停或紧急停止开仓会清除待执行计划，现有仓位仍按止损止盈管理。连续亏损锁定需先暂停再手动重置。

后台每 30 秒轮询，观察到候选后只安排严格未来的开盘，在该根 K 线收盘后使用真实 OHLC 结算模拟订单和止损。SQLite 原子保存进度、交易、权益和心跳，重放仅处理已有仓位风险。若缺失连续历史或停机超过 5,000 根 15 分钟 K 线则保留状态并提示人工处理。这里没有逐笔实时撮合。

## 测试

```bash
cd quant_signal_lab_online
python -m unittest -v
```

## 5.0 Ultimate P0 行情监控

原入口和现货功能继续使用。最新成交区默认显示 REST 快照，15 秒缓存、约 20 秒页面取样；闭合 K 线和信号的更新独立运行。配置独立网关后，前端每 5 秒读取该网关的 WebSocket 缓存，仅在交易所时间戳与接收时间有效时标记为 WebSocket。REST 回退会单独标注。行情时间到网关接收时差含时钟误差，不是浏览器端延迟，也不能视作逐笔成交的真实传输时间。

在获授权的常驻主机上可运行：

```bash
cd quant_signal_lab_online
python live_market.py --host 127.0.0.1 --port 8765
curl 'http://127.0.0.1:8765/ticker?symbol=BTCUSDT'
curl 'http://127.0.0.1:8765/health'
```

同机 Streamlit 设置 `QSL_MARKET_URL=http://127.0.0.1:8765`。Community Cloud 如需访问该服务，应通过 HTTPS 反向代理暴露只读端点并限速。网关根据访问续订所选品种，最后一次访问后约 120 秒释放，不在 Streamlit 页面中维持后台进程。当前网关只推送现货 ticker，未收盘实时 K 线与永续市场留待 P1/P2。

官方双端点一致性检查：

```bash
python verify_market.py --output /tmp/bitget-spot-differences.csv
```

比较同一交易对、周期和已收盘时间的 OHLC、基础币成交量和收盘时间。差异报告保留缺失和不一致字段。页面另支持参考 CSV 和指标比对，报告会区分数据口径与指标公式／预热的可能影响。开发审计、付费评估和后续阶段见 [P0_AUDIT.md](P0_AUDIT.md)。如需回滚，使用普通 `git revert <P0提交>` 并推送至原分支。

## 5.0 Ultimate P1 全合约工作台

入口默认打开 Bitget USDT 永续，可随时切回原现货工作台。合约目录取自 Bitget 官方 `/api/v2/mix/market/contracts`，只显示状态正常、USDT 报价且支持 USDT 保证金的永续。目录缓存 60 秒，24 小时 REST 报价缓存 20 秒，工作台约每 30 秒更新。列表可搜索合约或基础币，按成交额、正涨幅、负跌幅或高低振幅排序，并按最低成交额和最大买卖价差筛选。报价缺失或过期的合约仍保留在目录，排行榜会排除相关快照。

自选最多 50 个，通过当前网址的 `favorites` 参数保存。保存带参数的书签即可在刷新后恢复。列表同时显示资金费率、接收时间、行情时间和价差；价差以买卖中间价为分母。规格面板展示 Bitget 原始精度、步进、最小数量和最小名义额。P1 为只读，永续实时 K 线、候选信号与模拟交易留待后续阶段。完整验收记录见 [P1_REPORT.md](P1_REPORT.md)。
