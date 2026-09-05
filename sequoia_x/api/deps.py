"""依赖注入：向路由函数提供进程内唯一的 DataEngine。

DataEngine 实例在 `create_app()` 中构造一次并挂到 `app.state`，路由通过
`Depends(get_engine)` 取用。之所以不用模块级全局单例：
  - 全局变量在多线程下首次构造存在竞态（uvicorn 用线程池跑同步路由）
  - 测试需要换成临时数据库，走 app.state 只需 `create_app(engine=...)`，
    不必依赖 `dependency_overrides`

DataEngine 本身不持有连接，每次查询在 `data.engine._connect` 里开短连接，
因此多线程共享同一个实例是安全的。
"""

from fastapi import Request

from sequoia_x.data.engine import DataEngine


def get_engine(request: Request) -> DataEngine:
    """返回当前应用绑定的 DataEngine。"""
    return request.app.state.engine
