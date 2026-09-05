"""选股结果查询路由。

四个端点全部只读，不写库、不触发策略、不发通知。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from sequoia_x.api.deps import get_engine
from sequoia_x.api.schemas import SelectionItem, SelectionPage
from sequoia_x.core.symbols import to_xueqiu_code
from sequoia_x.data.engine import DataEngine

# 本模块不建 logger：路由只做参数校验与转换，唯一的失败路径是数据库异常，
# 已由 app.py 的 sqlite3.Error 处理器统一记录，在这里再记一次只会重复刷屏。
router = APIRouter()

# 日期一律 'YYYY-MM-DD'，与 selection_result.run_date 的存储格式一致。
# 格式不合法直接 422，与"格式合法但当天没数据"（200 + 空列表）区分开。
_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

# 单页上限。选股结果一天最多几十只，200 足够一次拉完；设上限是为了防止
# page_size=999999 把整库读进内存。
_MAX_PAGE_SIZE = 200


@router.get("/health", summary="存活检查")
def health() -> dict[str, str]:
    """返回固定的 ok，用于确认服务进程存活。"""
    return {"status": "ok"}


@router.get("/dates", summary="有选股数据的日期列表")
def list_dates(engine: Annotated[DataEngine, Depends(get_engine)]) -> list[str]:
    """返回有选股结果的日期，倒序（最新在前）。"""
    return engine.get_selection_dates()


@router.get("/strategies", summary="有选股数据的策略列表")
def list_strategies(engine: Annotated[DataEngine, Depends(get_engine)]) -> list[str]:
    """返回有选股结果的策略类名，字母序。"""
    return engine.get_selection_strategies()


@router.get("/selections", summary="查询选股结果")
def list_selections(
    engine: Annotated[DataEngine, Depends(get_engine)],
    date: Annotated[
        str | None,
        Query(pattern=_DATE_PATTERN, description="选股日期，缺省为最近有数据的一天"),
    ] = None,
    strategy: Annotated[
        str | None,
        Query(max_length=100, description="策略类名，缺省为全部策略"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=_MAX_PAGE_SIZE, description="每页条数")] = 50,
) -> SelectionPage:
    """
    分页查询某天的选股结果。

    日期没有数据时返回 `total=0, items=[]` 且 HTTP 200——"那天没选出票"
    是正常业务状态，不是 404。
    """
    # 空串视同未过滤，避免 `?strategy=` 让响应里回显一个无意义的 ""
    strategy = strategy or None

    run_date = date
    if run_date is None:
        dates = engine.get_selection_dates()
        run_date = dates[0] if dates else None

    if run_date is None:
        # 库里一条选股记录都没有（比如 main.py 还没跑过）
        return SelectionPage(
            date=None, strategy=strategy, total=0,
            page=page, page_size=page_size, items=[],
        )

    rows, total = engine.get_selections(
        run_date=run_date,
        strategy=strategy,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    return SelectionPage(
        date=run_date,
        strategy=strategy,
        total=total,
        page=page,
        page_size=page_size,
        items=[
            SelectionItem(
                symbol=row["symbol"],
                name=row["name"],
                strategy=row["strategy"],
                rank=row["rank"],
                xueqiu_code=to_xueqiu_code(row["symbol"]),
            )
            for row in rows
        ],
    )
