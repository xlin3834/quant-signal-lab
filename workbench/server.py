"""Public research site. It does not replace the Streamlit production app."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from workbench.engine_path import PACKAGE  # noqa: F401
from market_data import MarketDataError
from workbench.research import (
    archive_report,
    calendar,
    catalog_view,
    chart,
    core_assets,
    cross_market,
    health,
    live_quotes,
    macro_view,
    monitor,
    public_wallet,
    radar,
    reference_position,
    run_backtest,
)
from workbench.store import (
    WorkbenchError,
    apply_named_preset,
    copy_named,
    default_settings,
    delete_named,
    drift_note,
    export_named,
    import_named,
    load_settings,
    presets_public,
    rename_named,
    save_named_custom,
    save_settings,
    sync_shortcut,
)

STATIC = Path(__file__).resolve().parent / "static"


def create_app():
    app = FastAPI(title="信号实验室", docs_url=None, redoc_url=None)

    @app.exception_handler(WorkbenchError)
    async def user_error(_request, exc: WorkbenchError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.exception_handler(MarketDataError)
    async def market_error(_request, exc: MarketDataError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)

    @app.exception_handler(KeyError)
    async def missing(_request, exc: KeyError):
        return JSONResponse({"ok": False, "error": "没有这项配置"}, status_code=404)

    @app.get("/api/health")
    def api_health():
        return health()

    @app.get("/api/quotes")
    def api_quotes():
        return live_quotes()

    @app.get("/api/core")
    def api_core():
        return core_assets()

    @app.get("/api/directory")
    def api_directory(q: str = "", product: str = "", exchange: str = "bitget",
                      sort: str = "turnover", page: int = 1, page_size: int = 40):
        return catalog_view(q, product, exchange, sort, page, page_size)

    @app.get("/api/chart")
    def api_chart(symbol: str = "BTCUSDT", interval: str = "15m"):
        return chart(symbol, interval)

    @app.get("/api/radar")
    def api_radar(symbol: str = "BTCUSDT"):
        return radar(symbol)

    @app.get("/api/macro")
    def api_macro(symbol: str = "BTCUSDT"):
        return macro_view(symbol)

    @app.get("/api/settings")
    def api_settings():
        current = load_settings()
        drifted, note = drift_note(current)
        return {"ok": True, "current": current, "defaults": default_settings(),
                "presets": presets_public(), "drifted": drifted, "drift_note": note,
                "weights_note": "五项权重会保存。现行评分仍使用固定的 30/20/20/15/15，改数字不会假装分数已经变了。"}

    @app.put("/api/settings")
    async def api_save_settings(request: Request):
        body = await request.json()
        saved = save_settings(body)
        drifted, note = drift_note(saved)
        return {"ok": True, "current": saved, "drifted": drifted, "drift_note": note}

    @app.post("/api/settings/sync-shortcut")
    def api_sync():
        saved = sync_shortcut(load_settings())
        return {"ok": True, "current": saved, "note": "已把短期 MA 写入节奏 EMA，并把 RSI 同步到日内模块。长期 MA 仍只留在快捷模块。"}

    @app.post("/api/presets/apply")
    async def api_apply(request: Request):
        body = await request.json()
        saved = apply_named_preset(str(body.get("name") or ""))
        return {"ok": True, "current": saved, "note": "已切换工作副本。杠杆和单笔风险没有改，已保存的自定义配置也没有被覆盖。环境识别不会自动做这件事。"}

    @app.post("/api/presets/save")
    async def api_save_preset(request: Request):
        body = await request.json()
        name = save_named_custom(str(body.get("name") or "").strip(), load_settings())
        return {"ok": True, "name": name}

    @app.post("/api/presets/delete")
    async def api_delete_preset(request: Request):
        body = await request.json()
        delete_named(str(body.get("name") or ""))
        return {"ok": True}

    @app.post("/api/presets/rename")
    async def api_rename_preset(request: Request):
        body = await request.json()
        rename_named(str(body.get("old") or ""), str(body.get("new") or ""))
        return {"ok": True}

    @app.post("/api/presets/copy")
    async def api_copy_preset(request: Request):
        body = await request.json()
        copy_named(str(body.get("src") or ""), str(body.get("dest") or ""))
        return {"ok": True}

    @app.get("/api/presets/export")
    def api_export():
        return {"ok": True, "json": export_named()}

    @app.post("/api/presets/import")
    async def api_import(request: Request):
        body = await request.json()
        names = import_named(body.get("json") or "", replace=bool(body.get("replace")))
        return {"ok": True, "names": names, "note": "内置预设不会被导入内容覆盖。"}

    @app.post("/api/risk")
    async def api_risk(request: Request):
        return reference_position(await request.json())

    @app.post("/api/backtest")
    async def api_backtest(request: Request):
        body = await request.json()
        return run_backtest(str(body.get("symbol") or "BTCUSDT"))

    @app.get("/api/backtest/archive")
    def api_archive():
        return archive_report()

    @app.get("/api/cross")
    def api_cross(left: str = "BTCUSDT", right: str = "ETHUSDT", interval: str = "1H"):
        return cross_market(left, right, interval)

    @app.get("/api/calendar")
    def api_calendar():
        return calendar()

    @app.get("/api/monitor")
    def api_monitor(sort: str = "account", limit: int = 30, direction: str = "desc"):
        return monitor(sort, limit, direction)

    @app.post("/api/monitor/wallet")
    async def api_wallet(request: Request):
        body = await request.json()
        return public_wallet(str(body.get("address") or ""))

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
