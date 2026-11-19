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
