"""Run the public workbench: python -m workbench"""
from __future__ import annotations

import argparse
import os
from threading import Thread

import uvicorn

from workbench.server import app

DEFAULT_PORT = 8787


def bind_address(host_arg=None, port_arg=None, environ=None):
    """Listen on every interface. Use PORT when the platform sets it."""
    env = os.environ if environ is None else environ
    host = host_arg or "0.0.0.0"
    if port_arg is not None:
        port = int(port_arg)
    else:
        raw = env.get("PORT")
        port = int(raw) if raw not in (None, "") else DEFAULT_PORT
    if not 1 <= port <= 65535:
        raise ValueError(f"PORT {port} 不在 1–65535")
    return host, port


def main():
    parser = argparse.ArgumentParser(description="信号实验室独立网页工作台")
    parser.add_argument("--host", default=None, help="默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="未设置时读取 PORT，否则 8787")
    args = parser.parse_args()
    host, port = bind_address(args.host, args.port)
    browse = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"信号实验室工作台  http://{browse}:{port}")
    print(f"监听 {host}:{port}。PORT 环境变量优先于 8787。")
    print("研究模拟页面。不下单，不收集交易密钥。不要把这个进程当成现有 Streamlit 生产站点。")
    Thread(target=_warm_feed, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


def _warm_feed():
    from workbench.research import get_feed

    try:
        get_feed()
    except Exception:
        return


if __name__ == "__main__":
    main()
