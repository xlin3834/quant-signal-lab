# Quant Signal Lab — 在线网页版部署包

这是可部署的 Streamlit 网页应用（不是 Next.js/FastAPI 成品），支持 Mac/iPhone 浏览器访问。**不包含实盘交易功能，不需要 API 密钥。**

## 最快的公开网页部署（Streamlit Community Cloud）
1. 把 `app.py`、`requirements.txt`、`.streamlit/config.toml` 上传到你自己的 GitHub 仓库。
2. 登录 https://share.streamlit.io/ ，选择 Create app / Deploy app。
3. 选择你的 GitHub 仓库、分支，以及主文件 `app.py`，点击部署。
4. 部署成功后，平台会生成 `*.streamlit.app` 网址；在 Mac 和 iPhone 浏览器打开。

**注意：** 免费服务可能休眠，公开链接可能允许其他人访问。该应用仅展示公开行情和模拟回测，不要上传账户密钥。Bitget 接口在某些云平台可能无法访问。

## Docker 部署（自有云服务器）
```bash
docker build -t quant-signal-lab .
docker run --rm -p 8501:8501 quant-signal-lab
```
在服务器部署时，建议使用反向代理配置 HTTPS 和访问认证，不要直接将 8501 端口长期裸露公网。

## 功能与限制
- Bitget 公开现货 BTC/ETH/SOL K 线、指标图表、演示级历史回测。
- 只有约 200 根 K 线，回测不具备统计代表性。
- **没有**自动模拟盘持续运行、自动实盘下单、持仓同步、用户登录或生产级风控。
- 不保证数据实时性、服务可用性或盈利能力。
