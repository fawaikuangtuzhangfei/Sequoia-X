"""FastAPI 应用工厂。

单进程同时提供 `/api/*` 接口和前端页面：`frontend/dist` 存在就挂成静态站点，
不存在则降级为纯 API 服务（前端开发时由 Vite dev server 提供页面）。

**没有 CORS 中间件**，这是有意的：开发态浏览器只访问 Vite 的源，
`/api` 由 vite.config.ts 的 proxy 转发；生产态页面与接口同源。
两种情况下都不存在跨源请求，加 CORS 只会凭空放开一个来源。
"""

import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from sequoia_x.api.routers import selections
from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)

# sequoia_x/api/app.py -> parents[2] 即仓库根目录
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _handle_sqlite_error(request: Request, exc: Exception) -> JSONResponse:
    """数据库异常统一转 500，并记录出错的路径。

    不把 sqlite 的原始报错回给客户端：那会泄露表结构，且对前端没有意义。
    """
    logger.error(f"数据库查询失败 {request.url.path}：{exc}")
    return JSONResponse(status_code=500, content={"detail": "数据库查询失败"})


def create_app(engine: DataEngine | None = None) -> FastAPI:
    """
    构造 FastAPI 应用。

    Args:
        engine: 注入的数据引擎，仅测试使用。缺省时按全局 Settings 自行构造，
            并在整个进程生命周期内复用同一个实例。

    Returns:
        配置好路由、异常处理与静态资源的 FastAPI 实例。
    """
    app = FastAPI(
        title="Sequoia-X 选股结果查询",
        description="只读接口：查询历史选股结果。写入由 main.py 负责。",
        version="2.0.0",
    )

    app.state.engine = engine if engine is not None else DataEngine(get_settings())

    app.add_exception_handler(sqlite3.Error, _handle_sqlite_error)
    app.include_router(selections.router, prefix="/api", tags=["selections"])

    # 静态资源必须最后挂载：mount("/") 会兜住所有未匹配路径，
    # 放在 include_router 之前会把 /api/* 一起吃掉。
    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="ui")
        logger.info(f"已挂载前端页面：{_FRONTEND_DIST}")
    else:
        logger.info("未找到 frontend/dist，仅提供 API（前端请用 npm run dev）")

    return app
