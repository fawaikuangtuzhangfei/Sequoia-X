"""API 响应模型：定义接口对外的数据契约。"""

from pydantic import BaseModel, Field


class SelectionItem(BaseModel):
    """单条选股记录。"""

    symbol: str = Field(description="纯 6 位数字代码，如 '600519'")
    name: str | None = Field(description="股票名称，stock_basic 中查不到时为 null")
    strategy: str = Field(
        description=(
            "选出该股票的策略类名。不加 strategy 过滤时，同一天多个策略的结果混在"
            "一条列表里，同一只股票可能出现多次且 rank 都是 0——没有本字段就无法"
            "区分它们各自属于哪个策略。"
        )
    )
    rank: int = Field(
        description="该策略输出顺序，从 0 开始。rank 只在同一策略内可比，跨策略无意义"
    )
    xueqiu_code: str = Field(description="雪球代码，如 'SH600519'，由服务端计算")


class StrategyDoc(BaseModel):
    """一个策略的说明。内容来自策略类自身的属性，不是这里另写的副本。"""

    class_name: str = Field(description="策略类名，与 selection_result.strategy 一致")
    title: str = Field(description="中文名，如 '海龟突破'")
    summary: str = Field(description="一句话说清它在找什么")
    criteria: list[str] = Field(description="逐条判据，顺序与 run() 的判断顺序一致")
    data_source: str = Field(description="数据来源：本地行情还是外部接口")
    ordering: str = Field(description="结果顺序的依据，也就是 rank 的含义")
    min_bars: str = Field(description="需要多少历史数据才会参与计算")
    caveat: str | None = Field(description="使用时需要留神的地方，没有则为 null")


class SelectionPage(BaseModel):
    """一页选股结果。"""

    date: str | None = Field(description="实际查询的日期；库中完全没有数据时为 null")
    strategy: str | None = Field(description="策略名过滤条件，未过滤时为 null")
    total: int = Field(description="符合过滤条件的总条数（不受分页影响）")
    page: int = Field(description="当前页码，从 1 开始")
    page_size: int = Field(description="每页条数")
    items: list[SelectionItem]
