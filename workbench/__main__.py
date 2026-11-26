"""Run the public workbench: python -m workbench"""
from __future__ import annotations

import argparse
from threading import Thread

import uvicorn

from workbench.server import app


def main():
    parser = argparse.ArgumentParser(description="信号实验室独立网页工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    print(f"信号实验室工作台  http://{args.host}:{args.port}")
    print("研究模拟页面。不下单，不收集交易密钥。不要把这个进程当成现有 Streamlit 生产站点。")
    Thread(target=_warm_feed, daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _warm_feed():
    from workbench.research import get_feed

    try:
        get_feed()
    except Exception:
        return


if __name__ == "__main__":
    main()
