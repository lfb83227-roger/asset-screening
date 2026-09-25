"""FastAPI 应用装配。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import ENGINE_VERSION, __version__
from app.config import STATIC_DIR
from app.database import init_db
from app.routers import admin, api, assets, auth, crawl, dashboard


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI资产智能筛选与评分系统",
        description="法拍/AMC 资产全网采集 + 一票否决风控 + 五维量化评分 + 报告导出",
        version=__version__,
        lifespan=lifespan,
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(auth.router)
    app.include_router(api.router)
    app.include_router(dashboard.router)
    app.include_router(assets.router)
    app.include_router(crawl.router)
    app.include_router(admin.router)

    @app.exception_handler(404)
    async def _not_found(request: Request, exc):  # pragma: no cover
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        from app.webutils import render
        return render(request, "error.html", code=404,
                      message="页面不存在", active="")

    @app.exception_handler(500)
    async def _server_error(request: Request, exc):  # pragma: no cover
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "internal error"}, status_code=500)
        from app.webutils import render
        return render(request, "error.html", code=500,
                      message=f"服务内部错误：{exc}", active="")

    return app


app = create_app()
