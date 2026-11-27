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

- BTC、ETH、SOL、BNB：Bitget 官方 USDT 本位永续。WebSocket 优先，REST 用来补历史、重连和校验。同一合约不混用其他交易所的价格。页面上的延迟是当前连接的实测毫秒数。REST 快照不会标成实时。断线或数据不足时写无法判断，不编造方向。未收盘 K 线只作预警。
- NVDA 正股、XAU/USD 现货：没有已授权行情，核心卡片不报价。目录里的 NVDAUSDT、XAUUSDT、XAUTUSDT、PAXGUSDT 会标成代币化永续，不拿来顶替正股或现货。
- 搜索按基础币代码匹配。`XAU` 可以找到 XAU 和 XAUT，不会因为 `FOXA` 和 `USDT` 粘在一起而命中 FOXA。
- 五种预设各自有参数，只是初始测试值。环境识别只给建议。应用预设会更新图表、评分、回测和页内提醒共用的工作副本，不会改写已经保存的自定义配置，也不会自动提高杠杆或单笔风险。
- 五个原有参数模块都在指标设置里：MA/RSI 快捷、日内评分与异动、EMA×布林、多周期回踩、布林动态止盈止损。宏观、斐波那契和交叉提醒是额外的，不替换这五块。
- 回测要手动开始。短样本和 2026-11-26 的存档分开标注。规则是已收盘信号、下一根开盘成交、同一根先止损，并按时间做样本内/外分割。
- 主动提醒只有六种收盘确认的 EMA 与布林中轨交叉。旧的 EMA9×EMA20、80 分候选、趋势切换、RSI 背驰和支撑压力提醒已归档，默认不显示、不推送。收藏自动加入监控，可以按品种暂停。名单存在这个部署的服务器上，刷新后还在。免费 Render 网页空闲会休眠，关页后不能保证继续监控。常驻检查要另跑 `python -m workbench.monitor_worker`。邮箱和 Telegram 只读服务端环境变量，没有密钥时测试会写明未发送。
- 「实盘监控」读的是 Hyperliquid 公开排行榜和公开地址，不是使用者自己的交易所账户。没有官方 displayName 时写「未公开昵称」。排行榜账户价值和永续权益分开。最大回撤、交易次数、胜率没有官方字段时写「暂无数据」。默认按 30 日收益率排序。

## 还没有、也不要假装已经有

- 付费或需授权的美股行情（例如 Alpaca）和专业黄金现货。未购买，核心位保持「数据源未接通」。
- 把免费会休眠的网页说成已经 24 小时监控。常驻进程要单独部署，密钥未配置时不会假装已经发出邮件或 Telegram。
- 交易、下单和交易 API Key。导航里不再单列风险页。回测仍使用手续费、滑点和仓位模块，这些是假设，不含资金费或借币息。
- 现货、CFD、以及其他交易所（币安、OKX、WEEX）的实时簿。这些入口会说明未接通，不用 Bitget 合约冒充。

已保存的五项权重会进入评分。原预设默认仍是 30/20/20/15/15。研究档位「超短线 / 激进 / 平衡 / 稳健初始 / 保守」另外保留，不替换敏捷、均衡、稳健、震荡和自定义。八因子里没有单独算出来的三项不计入分数。4 小时和 1 小时方向只作背景，不再单独否决低周期结论。主图默认画 EMA7（黄）、EMA10（蓝）、EMA120（紫）和 BOLL(18,2)。中轨是收盘价的 18 周期简单均线。EMA120 穿越只报告方向，不是开仓指令。市场扫描按所选市场的 24 小时成交额取前 20，不混单位。XAUUSDT、XAGUSDT 若能画出，标成 Bitget 代币化贵金属永续，不代替现货黄金。实盘监控读的是 Hyperliquid 公开持仓，不是用户自己的 Bitget 账户。页面上的传输、中位和 P95 只来自当前连接的实测样本。

## 设置写入

`QSL_ADMIN_TOKEN` 未设置时，只有本机回环地址可以改全站设置和预设，公网请求会被拒绝。设置了这个环境变量之后，每一次写入都要带请求头 `X-Admin-Token`，本机也不例外。不要把令牌写进仓库。页面上的令牌框只留在当前浏览器会话。写入接口默认每个地址每分钟最多 30 次。这里没有登录系统，也不收集 Telegram 或邮箱密钥。

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
