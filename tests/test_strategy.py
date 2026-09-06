"""策略引擎属性测试。"""

import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

import sequoia_x.strategy.turtle_trade as turtle_module
from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy


# Feature: sequoia-x-v2, Property 9: 策略 run() 返回值类型正确
@given(
    symbols=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=0, max_size=3, unique=True,
    )
)
@h_settings(max_examples=30, deadline=None)
def test_strategy_run_returns_list_of_str(symbols: list[str]) -> None:
    """属性 9：run() 应返回 list[str]，每个元素为非空字符串。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)

        with patch.object(engine, "get_all_symbols", return_value=symbols):
            with patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()):
                strategy = MaVolumeStrategy(engine=engine, settings=settings)
                result = strategy.run()

    assert isinstance(result, list)
    assert all(isinstance(s, str) and len(s) > 0 for s in result)


def _breakout_df(as_of: str, n: int = 25) -> pd.DataFrame:
    """构造一段最后一根 K 线满足海龟全部条件的日线。

    前 n-1 根横盘在 10 元，最后一根放量突破到 12 元的实体阳线。
    """
    end = date.fromisoformat(as_of)
    dates = [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    rows = [
        {"date": d, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
         "volume": 1000.0, "turnover": 2e8}
        for d in dates
    ]
    rows[-1].update(open=10.0, high=12.0, low=10.0, close=12.0, turnover=2e8)
    return pd.DataFrame(rows)


def _capture_turtle_logs() -> tuple[list[logging.LogRecord], logging.Handler]:
    """turtle logger 也是 propagate=False，caplog 收不到。"""
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _ListHandler(logging.WARNING)
    logging.getLogger(turtle_module.__name__).addHandler(handler)
    return records, handler


def _run_turtle(
    symbols: list[str], as_of: str, caps: dict[str, float]
) -> tuple[list[str], list[str], list[logging.LogRecord]]:
    """跑一次海龟，返回 (选股结果, _get_market_caps 收到的 as_of 列表, 日志)。"""
    seen_as_of: list[str] = []

    def fake_caps(_self: object, syms: list[str], as_of_arg: str) -> dict[str, float]:
        seen_as_of.append(as_of_arg)
        return caps

    df = _breakout_df(as_of)
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
        )
        engine = DataEngine(settings)
        records, handler = _capture_turtle_logs()
        try:
            with patch.object(engine, "get_local_symbols", return_value=symbols), \
                 patch.object(engine, "get_ohlcv", return_value=df), \
                 patch.object(TurtleTradeStrategy, "_get_market_caps", fake_caps):
                result = TurtleTradeStrategy(engine=engine, settings=settings).run()
        finally:
            logging.getLogger(turtle_module.__name__).removeHandler(handler)
    return result, seen_as_of, records


# Feature: sequoia-x-v2, Property 72: 市值排序基于决策日，且失效时必须告警
@given(offset_days=st.integers(min_value=1, max_value=400))
@h_settings(max_examples=15, deadline=None)
def test_market_cap_uses_decision_date_not_today(offset_days: int) -> None:
    """属性 72：取市值的基准日是最后一根 K 线的日期，不是 date.today()。

    原先写死 date.today()：周末、节假日、盘后数据未发布时 baostock 返回空，
    market_caps 全空，sort 因为稳定排序原样不动——"按流通市值排序"静默失效，
    日志一个字不说。实测 2026-09-06（周日）跑出的海龟名单恰好是代码升序，
    就是这个 bug 的现场。

    用决策日还有个附带好处：不依赖机器时钟，回测重放拿到的是 as-of 日。
    """
    as_of = (date.today() - timedelta(days=offset_days)).isoformat()

    _, seen_as_of, _ = _run_turtle(["600519"], as_of, {"600519": 1.0})

    assert seen_as_of == [as_of], "取市值用的不是决策日"
    assert seen_as_of[0] != date.today().isoformat()


def test_empty_market_caps_warns_that_sorting_did_not_happen() -> None:
    """一条市值都没取到时必须 WARNING：结果看着"排过了"，其实原样未动。"""
    as_of = (date.today() - timedelta(days=30)).isoformat()

    result, _, records = _run_turtle(["600519", "000001"], as_of, {})

    assert len(result) == 2
    warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
    assert any("未生效" in m for m in warnings), f"排序失效却没有告警：{warnings}"


def test_partial_market_cap_coverage_warns() -> None:
    """覆盖率不足阈值时告警：缺失的按 0 处理会被排到末尾，排序已经失真。"""
    as_of = (date.today() - timedelta(days=30)).isoformat()
    symbols = [f"{600000 + i:06d}" for i in range(10)]

    result, _, records = _run_turtle(symbols, as_of, {symbols[0]: 5.0})

    assert len(result) == 10
    warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
    assert any("部分失真" in m for m in warnings), f"覆盖率不足却没有告警：{warnings}"


def test_full_coverage_sorts_desc_and_stays_quiet() -> None:
    """市值齐全时按市值降序排列，且不该有任何告警。"""
    as_of = (date.today() - timedelta(days=30)).isoformat()
    symbols = ["600001", "600002", "600003"]
    caps = {"600001": 1.0, "600002": 9.0, "600003": 5.0}

    result, _, records = _run_turtle(symbols, as_of, caps)

    assert result == ["600002", "600003", "600001"]
    assert not [r for r in records if r.levelno >= logging.WARNING]
