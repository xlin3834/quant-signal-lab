# 信号实验室 · 趋势判断工作台

独立的公开研究网页。不需要登录，不下单，不收集交易密钥。界面按「信号实验室 · 趋势判断工作台」的深色市场终端来做，计算沿用仓库里的 Python 行情和指标引擎。

现有生产站点 [https://aether-quant-lab.streamlit.app/](https://aether-quant-lab.streamlit.app/) 继续跑 GitHub `main` 上的 Streamlit 入口 `quant_signal_lab_online/app.py`。不要把本分支合并进 `main`，也不要把它设成那个 Streamlit 应用的部署来源。

## 本地运行

在仓库根目录：

```bash
pip install -r workbench/requirements.txt
python -m workbench
```

浏览器打开 http://127.0.0.1:8787 。进程默认监听 `0.0.0.0`。如果环境变量 `PORT` 有值就用它，否则用 8787。显式传入 `--port` 时以命令行为准。

引擎自测（不启动网页）：

```bash
python -m unittest workbench.test_workbench
cd quant_signal_lab_online && python -m unittest
```

第二个命令需要 `quant_signal_lab_online/requirements.txt` 里的 Streamlit 和 Plotly。网页本身不依赖它们。

## 这个网站现在接到什么

- BTC、ETH：Bitget 官方 USDT 本位永续。WebSocket 优先，REST 用来补历史、重连和校验。页面上的延迟是交易所时间戳到本机接收的毫秒数。REST 快照不会标成实时。断线或过期时暂停正式候选。未收盘 K 线只作预警。
- NVDA 正股、XAU/USD 现货：没有已授权行情，核心卡片不报价。目录里的 NVDAUSDT、XAUUSDT、XAUTUSDT、PAXGUSDT 会标成代币化永续，不拿来顶替正股或现货。
- 搜索按基础币代码匹配。`XAU` 可以找到 XAU 和 XAUT，不会因为 `FOXA` 和 `USDT` 粘在一起而命中 FOXA。
- 五种预设各自有参数，只是初始测试值。环境识别只给建议。应用预设会更新图表、评分、回测和页内提醒共用的工作副本，不会改写已经保存的自定义配置，也不会自动提高杠杆或单笔风险。
- 五个原有参数模块都在指标设置里：MA/RSI 快捷、日内评分与异动、EMA×布林、多周期回踩、布林动态止盈止损。宏观、斐波那契和交叉提醒是额外的，不替换这五块。
- 回测要手动开始。短样本和 2026-11-26 的存档分开标注。规则是已收盘信号、下一根开盘成交、同一根先止损，并按时间做样本内/外分割。
- 提醒只在这个页面开着的时候检查。没有云端、锁屏或后台推送。
- 「实盘监控」读的是 Hyperliquid 公开排行榜和公开地址，不是使用者自己的交易所账户。

## 还没有、也不要假装已经有

- 付费或需授权的美股行情（例如 Alpaca）和专业黄金现货。未购买，核心位保持「数据源未接通」。
- 云端通知、手机锁屏和关页后继续监控。没有后端服务，页面写明关闭后即停止。
- 交易、下单和交易 API Key。风险页只是参考计算器，手续费和滑点是假设，不含资金费或借币息。
- 现货、CFD、以及其他交易所（币安、OKX、WEEX）的实时簿。这些入口会说明未接通，不用 Bitget 合约冒充。

评分函数目前仍使用固定权重 30/20/20/15/15。设置里的权重会保存，页面会说明它们还没有改写分数。

## 以后做成应用

网页是同一套静态前端加 Python API，并带了 manifest 和只缓存页面壳的 service worker。以后可以把它包成 PWA。这一轮没有做原生应用。Service worker 不缓存 `/api`，离线时也不会继续提醒。

## 单独部署，且不替换现有 Streamlit

这是一个新网站，不是现有 Streamlit 应用的更新。不要把 [https://aether-quant-lab.streamlit.app/](https://aether-quant-lab.streamlit.app/) 的仓库、分支或入口改到本分支。那个站点继续使用 GitHub `main` 的 `quant_signal_lab_online/app.py`。

仓库根目录的 `render.yaml` 只描述一个新的 Docker Web 服务：用本分支 `cursor/signal-lab-workbench-1d62` 构建 `workbench/Dockerfile`，构建上下文是仓库根目录。平台会注入 `PORT`，容器监听 `0.0.0.0:$PORT`。应用蓝图之后，新网址由托管商分配，名称对应 `https://signal-lab-workbench.onrender.com`。这里没有创建该服务，也没有购买套餐。

本地或任意能跑 Docker 的新主机：

```bash
pip install -r workbench/requirements.txt
python -m workbench
```

```bash
docker build -f workbench/Dockerfile -t signal-lab-workbench .
docker run --rm -e PORT=8787 -p 8787:8787 signal-lab-workbench
```

不要改 `quant_signal_lab_online/Dockerfile`，那份镜像仍是 Streamlit。
