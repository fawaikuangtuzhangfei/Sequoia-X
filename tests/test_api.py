"""选股结果查询接口属性测试。

全部走 TestClient + 临时 SQLite，不启真实服务、不触网。
"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.api.app import create_app
from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine

_SYMBOL = st.text(min_size=6, max_size=6, alphabet="0123456789")


def make_client(tmp_dir: str) -> tuple[TestClient, DataEngine]:
    """基于临时数据库构造测试客户端。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    return TestClient(create_app(engine=engine)), engine


# Feature: sequoia-x-v2, Property 21: 接口返回的雪球代码符合前缀规则
@given(symbols=st.lists(_SYMBOL, min_size=1, max_size=10, unique=True))
@h_settings(max_examples=30, deadline=None)
def test_xueqiu_code_prefix_is_correct(symbols: list[str]) -> None:
    """属性 21：任意 6 位代码，接口返回的 xueqiu_code 长度为 8 且前缀符合规则。

    前缀由服务端计算，前端直接拼链接。规则错了会把用户带到不存在的页面。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, engine = make_client(tmp_dir)
        engine.save_selection("2026-09-05", "AnyStrategy", symbols)
        resp = client.get("/api/selections", params={"date": "2026-09-05", "page_size": 200})

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == len(symbols)
    for item in items:
        code = item["xueqiu_code"]
        symbol = item["symbol"]
        assert len(code) == 8
        assert code.endswith(symbol)
        if symbol.startswith("6"):
            assert code.startswith("SH")
        elif symbol.startswith(("4", "8")):
            assert code.startswith("BJ")
        else:
            assert code.startswith("SZ")


# Feature: sequoia-x-v2, Property 22: 查询无数据的日期返回 200 而非 404
@given(run_date=st.dates().map(lambda d: d.strftime("%Y-%m-%d")))
@h_settings(max_examples=20, deadline=None)
def test_missing_date_returns_empty_page(run_date: str) -> None:
    """属性 22：合法但无数据的日期，应返回 200 + total=0，而不是 404。

    "那天没选出票"是正常业务状态。返回 404 会让前端把它当成错误弹窗。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, _ = make_client(tmp_dir)
        resp = client.get("/api/selections", params={"date": run_date})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["date"] == run_date


# Feature: sequoia-x-v2, Property 23: 分页不重不漏且保持 rank 顺序
@given(
    symbols=st.lists(_SYMBOL, min_size=1, max_size=25, unique=True),
    page_size=st.integers(min_value=1, max_value=10),
)
@h_settings(max_examples=25, deadline=None)
def test_pagination_is_complete_and_ordered(symbols: list[str], page_size: int) -> None:
    """属性 23：逐页拉取并拼接，结果应等于写入顺序，既不重复也不遗漏。

    rank 承载着策略的排序含义（海龟按流通市值降序），分页错位等于结果错乱。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, engine = make_client(tmp_dir)
        engine.save_selection("2026-09-05", "AnyStrategy", symbols)

        collected: list[str] = []
        total_pages = (len(symbols) + page_size - 1) // page_size
        for page in range(1, total_pages + 1):
            resp = client.get(
                "/api/selections",
                params={"date": "2026-09-05", "page": page, "page_size": page_size},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == len(symbols)
            collected.extend(item["symbol"] for item in body["items"])

    assert collected == symbols


# Feature: sequoia-x-v2, Property 24: 不传日期时默认返回最新有数据的一天
@given(
    older=st.lists(_SYMBOL, min_size=1, max_size=5, unique=True),
    newer=st.lists(_SYMBOL, min_size=1, max_size=5, unique=True),
)
@h_settings(max_examples=20, deadline=None)
def test_default_date_is_latest(older: list[str], newer: list[str]) -> None:
    """属性 24：缺省 date 参数时，应返回最近有数据的那天的结果。

    这是前端首屏的默认行为——用户打开页面就该看到最新一次选股。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, engine = make_client(tmp_dir)
        engine.save_selection("2026-01-01", "AnyStrategy", older)
        engine.save_selection("2026-09-05", "AnyStrategy", newer)
        resp = client.get("/api/selections", params={"page_size": 200})

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-05"
    assert [item["symbol"] for item in body["items"]] == newer


# Feature: sequoia-x-v2, Property 25: 越界的分页参数被拒绝而非放行
@given(page_size=st.integers(min_value=201, max_value=10**6))
@h_settings(max_examples=20, deadline=None)
def test_oversized_page_size_is_rejected(page_size: int) -> None:
    """属性 25：page_size 超过上限返回 422，不允许一次拉走整库。

    没有上限时 page_size=10**6 会把整张表读进内存。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, _ = make_client(tmp_dir)
        resp = client.get("/api/selections", params={"page_size": page_size})

    assert resp.status_code == 422


# Feature: sequoia-x-v2, Property 26: 非法日期格式被拒绝而非静默返回空
@given(
    bad_date=st.text(min_size=1, max_size=20).filter(
        lambda s: not (len(s) == 10 and s[:4].isdigit() and s[4] == "-")
    )
)
@h_settings(max_examples=30, deadline=None)
def test_malformed_date_is_rejected(bad_date: str) -> None:
    """属性 26：不符合 YYYY-MM-DD 的日期返回 422。

    静默返回空列表会让前端把"参数写错了"误读成"那天没选出票"。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, _ = make_client(tmp_dir)
        resp = client.get("/api/selections", params={"date": bad_date})

    assert resp.status_code == 422


# Feature: sequoia-x-v2, Property 27: 不过滤策略时每条记录仍标明来源策略
@given(
    shared=_SYMBOL,
    extra_a=st.lists(_SYMBOL, min_size=0, max_size=4, unique=True),
    extra_b=st.lists(_SYMBOL, min_size=0, max_size=4, unique=True),
)
@h_settings(max_examples=25, deadline=None)
def test_items_carry_strategy_when_unfiltered(
    shared: str, extra_a: list[str], extra_b: list[str]
) -> None:
    """属性 27：同一只股票被多个策略选中时，每条记录都能区分来源策略。

    不加 strategy 过滤时，多个策略的结果混在一条列表里，同一 symbol 会出现
    多次且 rank 都可能是 0。缺了 strategy 字段前端就无法分组，只会显示成
    莫名其妙的重复行。
    """
    a = [shared] + [s for s in extra_a if s != shared]
    b = [shared] + [s for s in extra_b if s != shared]

    with tempfile.TemporaryDirectory() as tmp_dir:
        client, engine = make_client(tmp_dir)
        engine.save_selection("2026-09-05", "StrategyA", a)
        engine.save_selection("2026-09-05", "StrategyB", b)
        resp = client.get("/api/selections", params={"date": "2026-09-05", "page_size": 200})

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == len(a) + len(b)

    # (策略, 代码) 才是唯一键；仅凭 symbol 无法定位一条记录
    pairs = [(item["strategy"], item["symbol"]) for item in items]
    assert len(set(pairs)) == len(pairs)
    assert ("StrategyA", shared) in pairs
    assert ("StrategyB", shared) in pairs

    by_strategy: dict[str, list[str]] = {}
    for item in items:
        by_strategy.setdefault(item["strategy"], []).append(item["symbol"])
    assert by_strategy["StrategyA"] == a
    assert by_strategy["StrategyB"] == b


def test_health_and_metadata_endpoints() -> None:
    """健康检查与元数据端点在空库上也应正常返回。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, engine = make_client(tmp_dir)

        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/dates").json() == []
        assert client.get("/api/strategies").json() == []

        engine.save_selection("2026-09-05", "TurtleTradeStrategy", ["600519"])
        engine.save_selection("2026-09-04", "MaVolumeStrategy", ["000001"])

        assert client.get("/api/dates").json() == ["2026-09-05", "2026-09-04"]
        assert client.get("/api/strategies").json() == [
            "MaVolumeStrategy", "TurtleTradeStrategy",
        ]


# Feature: sequoia-x-v2, Property 30: 说明端点如实反映注册表
def test_strategy_docs_mirror_the_registry() -> None:
    """属性 30：/api/strategies/docs 返回注册表里的全部策略，顺序一致。

    它必须包含当天没跑出结果的策略——那些在结果页里根本不出现，
    恰恰最需要解释。所以这个端点不能只返回"有数据的策略"。
    """
    from sequoia_x.strategy.registry import STRATEGIES

    with tempfile.TemporaryDirectory() as tmp_dir:
        client, _ = make_client(tmp_dir)  # 空库：证明它不依赖任何选股数据
        resp = client.get("/api/strategies/docs")

    assert resp.status_code == 200
    docs = resp.json()
    assert [d["class_name"] for d in docs] == [c.__name__ for c in STRATEGIES]

    for doc, cls in zip(docs, STRATEGIES, strict=True):
        assert doc["title"] == cls.title
        assert doc["summary"] == cls.summary
        assert doc["criteria"] == list(cls.criteria)
        assert doc["ordering"] == cls.ordering
        assert doc["caveat"] == cls.caveat


def test_database_error_returns_500_without_leaking_details() -> None:
    """数据库异常应转成 500，且不把 sqlite 原始报错回给客户端。

    这是唯一的服务端错误路径，不测就等于没写：注册处理器时写错异常类型，
    表现是 TestClient 直接抛出而非返回 500。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, engine = make_client(tmp_dir)
        broken = sqlite3.OperationalError("no such table: selection_result")
        with patch.object(engine, "get_selection_dates", side_effect=broken):
            resp = client.get("/api/dates")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "数据库查询失败"}
    assert "selection_result" not in resp.text


def test_empty_database_returns_null_date() -> None:
    """库中一条选股记录都没有时，date 为 null 且不报错。

    对应 main.py 从未跑过的全新部署，页面必须能打开而不是 500。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        client, _ = make_client(tmp_dir)
        resp = client.get("/api/selections")

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] is None
    assert body["total"] == 0
    assert body["items"] == []
