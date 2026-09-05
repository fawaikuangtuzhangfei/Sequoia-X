"""策略说明路由。

**本模块是 api 层唯一 import strategy 的地方，而且只读类属性、绝不调用 run()。**
分层规则里 "api 不得 import strategy" 的本意是禁止在请求线程上触发选股——
那会把一个不设上限、依赖网络、还要开多进程的任务挂到 HTTP 请求上，
并且让局域网里任何人都能刷屏飞书群。读几个字符串常量不在此列。

代价是导入策略模块会执行它们的模块级代码。目前七个策略在模块级只 import
pandas（data 层本来就已经导入），akshare 和 baostock 都是在方法内部懒加载的。
**新增策略若在模块级做重活，这个端点会跟着变慢——那属于策略本身的问题。**
"""

from fastapi import APIRouter

from sequoia_x.api.schemas import StrategyDoc
from sequoia_x.strategy.registry import STRATEGIES

router = APIRouter()


@router.get("/strategies/docs", summary="全部策略的说明")
def list_strategy_docs() -> list[StrategyDoc]:
    """
    返回注册表里全部策略的说明，顺序即 main.py 的执行顺序。

    与 /api/strategies 不同：那个返回的是"当前库里有选股数据的策略名"，
    这个返回的是"系统里注册了哪些策略"。没跑出结果的策略也在这里，
    而那恰恰是最需要解释的——它在结果页里根本不出现。

    纯静态元数据，不查库。
    """
    return [
        StrategyDoc(
            class_name=cls.__name__,
            title=cls.title,
            summary=cls.summary,
            criteria=list(cls.criteria),
            data_source=cls.data_source,
            ordering=cls.ordering,
            min_bars=cls.min_bars,
            caveat=cls.caveat,
        )
        for cls in STRATEGIES
    ]
