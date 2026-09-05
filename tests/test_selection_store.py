"""选股结果持久化属性测试。"""

import contextlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine

_SYMBOL = st.text(min_size=6, max_size=6, alphabet="0123456789")


def make_engine_in(tmp_dir: str) -> DataEngine:
    """创建使用临时数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    return DataEngine(settings)


# Feature: sequoia-x-v2, Property 14: 选股结果顺序保真
@given(symbols=st.lists(_SYMBOL, min_size=0, max_size=20, unique=True))
@h_settings(max_examples=30, deadline=None)
def test_selection_preserves_order(symbols: list[str]) -> None:
    """属性 14：save_selection 写入的顺序，查询时应原样还原。

    顺序是策略输出的一部分（海龟按流通市值降序、定增按公告日期降序），
    丢失顺序等于丢失结果的一部分。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        engine.save_selection("2026-09-05", "AnyStrategy", symbols)
        items, total = engine.get_selections(run_date="2026-09-05", limit=1000)

    assert total == len(symbols)
    assert [item["symbol"] for item in items] == symbols
    assert [item["rank"] for item in items] == list(range(len(symbols)))


# Feature: sequoia-x-v2, Property 15: 同日同策略重复写入保持幂等
@given(
    first=st.lists(_SYMBOL, min_size=0, max_size=10, unique=True),
    second=st.lists(_SYMBOL, min_size=0, max_size=10, unique=True),
)
@h_settings(max_examples=30, deadline=None)
def test_selection_rewrite_is_idempotent(first: list[str], second: list[str]) -> None:
    """属性 15：同一 (日期, 策略) 写两次，最终只保留第二次的结果。

    重跑 main.py 不应产生重复数据；且第二次结果变短时，
    第一次遗留的多余记录必须消失。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        engine.save_selection("2026-09-05", "AnyStrategy", first)
        engine.save_selection("2026-09-05", "AnyStrategy", second)
        items, total = engine.get_selections(run_date="2026-09-05", limit=1000)

    assert total == len(second)
    assert [item["symbol"] for item in items] == second


# Feature: sequoia-x-v2, Property 16: 查询不存在的组合返回空而非报错
@given(
    run_date=st.dates().map(str),
    strategy=st.text(min_size=1, max_size=30, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
    )),
)
@h_settings(max_examples=30, deadline=None)
def test_query_missing_combination_returns_empty(run_date: str, strategy: str) -> None:
    """属性 16：查询没有数据的日期/策略，应返回空列表和 0，而不是抛异常。

    "当天没选出票"是正常业务状态，不是错误。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        items, total = engine.get_selections(run_date=run_date, strategy=strategy)

    assert items == []
    assert total == 0


# Feature: sequoia-x-v2, Property 20: 重复代码被去重而非丢弃整批结果
@given(
    symbols=st.lists(_SYMBOL, min_size=1, max_size=8, unique=True),
    dup_count=st.integers(min_value=1, max_value=3),
)
@h_settings(max_examples=20, deadline=None)
def test_duplicate_symbols_are_deduplicated(symbols: list[str], dup_count: int) -> None:
    """属性 20：策略返回重复代码时应去重保序，而不是触发 UNIQUE 约束报错。

    UNIQUE(run_date, strategy, symbol) 会让整批写入失败，main.py 捕获后
    该策略当天的结果就全丢了。去重的代价远小于丢数据。
    """
    with_dups = symbols + symbols[:dup_count]

    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        written = engine.save_selection("2026-09-05", "AnyStrategy", with_dups)
        items, total = engine.get_selections(run_date="2026-09-05", limit=1000)

    assert written == len(symbols)
    assert total == len(symbols)
    # 保留首次出现的顺序
    assert [item["symbol"] for item in items] == symbols


# Feature: sequoia-x-v2, Property 17: 空结果与未运行在数据上可区分
@given(symbols=st.lists(_SYMBOL, min_size=1, max_size=5, unique=True))
@h_settings(max_examples=20, deadline=None)
def test_empty_result_clears_previous_records(symbols: list[str]) -> None:
    """属性 17：先写入非空结果，再写入空结果，旧记录应被清空。

    这样"今天该策略确实没选出票"与"今天没跑过"才能区分开。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        engine.save_selection("2026-09-05", "AnyStrategy", symbols)
        engine.save_selection("2026-09-05", "AnyStrategy", [])
        items, total = engine.get_selections(run_date="2026-09-05")
        dates = engine.get_selection_dates()

    assert items == []
    assert total == 0
    assert dates == []


# Feature: sequoia-x-v2, Property 18: 股票名称通过 LEFT JOIN 补全且可缺失
@given(
    known=st.lists(_SYMBOL, min_size=1, max_size=5, unique=True),
    unknown=st.lists(_SYMBOL, min_size=1, max_size=5, unique=True),
)
@h_settings(max_examples=20, deadline=None)
def test_name_is_joined_and_nullable(known: list[str], unknown: list[str]) -> None:
    """属性 18：stock_basic 里有的代码带出名称，没有的名称为 None，查询不报错。

    名称只是展示信息，查不到必须降级而不是让查询失败。
    """
    unknown = [s for s in unknown if s not in known]

    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        engine.upsert_stock_basic([(s, f"名称{s}") for s in known])
        engine.save_selection("2026-09-05", "AnyStrategy", known + unknown)
        items, _ = engine.get_selections(run_date="2026-09-05", limit=1000)

    by_symbol = {item["symbol"]: item["name"] for item in items}
    assert all(by_symbol[s] == f"名称{s}" for s in known)
    assert all(by_symbol[s] is None for s in unknown)


# ── main 流程的容错性 ──

_STRATEGY_ATTRS = [
    "MaVolumeStrategy",
    "TurtleTradeStrategy",
    "HighTightFlagStrategy",
    "LimitUpShakeoutStrategy",
    "UptrendLimitDownStrategy",
    "RpsBreakoutStrategy",
    "PrivatePlacementStrategy",
]


class _StubStrategy:
    """恒定返回一只股票的桩策略，避免测试触网或读真实数据库。"""

    webhook_key = "stub"

    def __init__(self, engine: object, settings: object) -> None:
        self.engine = engine
        self.settings = settings

    def run(self) -> list[str]:
        return ["600519"]


# Feature: sequoia-x-v2, Property 19: 写库失败不影响飞书推送
@given(error_msg=st.text(min_size=1, max_size=50))
@h_settings(max_examples=10, deadline=None)
def test_save_failure_does_not_break_notification(error_msg: str) -> None:
    """属性 19：save_selection 抛任何异常，main() 都应继续推送且不退出。

    main() 不单独包裹每个策略，逃逸异常会终止整轮运行。持久化是新增的
    次要功能，绝不能拖累飞书推送这条既有主链路。
    """
    import main as main_module

    settings = Settings(
        db_path="data/unused.db",
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = MagicMock()
    engine.sync_today_bulk.return_value = 0
    engine.save_selection.side_effect = RuntimeError(error_msg)
    notifier = MagicMock()

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(main_module, "get_settings", return_value=settings))
        stack.enter_context(patch.object(main_module, "DataEngine", return_value=engine))
        stack.enter_context(patch.object(main_module, "FeishuNotifier", return_value=notifier))
        for attr in _STRATEGY_ATTRS:
            stack.enter_context(patch.object(main_module, attr, _StubStrategy))
        stack.enter_context(patch("sys.argv", ["main.py"]))

        main_module.main()  # 不应抛出，也不应 SystemExit

    assert engine.save_selection.call_count == len(_STRATEGY_ATTRS)
    assert notifier.send.call_count == len(_STRATEGY_ATTRS)
