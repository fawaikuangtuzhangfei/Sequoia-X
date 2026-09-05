"""历史回放的属性测试。

回放最危险的失败方式不是报错，而是**悄悄读到未来数据**——
那样跑出来的策略会有惊人的胜率，而且看不出哪里不对。
下面前两条属性就是守这个的。
"""

import sqlite3
import tempfile
from pathlib import Path

from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.backtest.replay import (
    REPLAYABLE_STRATEGIES,
    AsOfEngine,
    MarketCache,
    replay_range,
)
from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy

_DATES: list[str] = [f"2024-01-{day:02d}" for day in range(1, 29)]

# 只靠 engine.get_ohlcv + get_local_symbols 取数的策略。
# 这四个必须在 as-of 包装下**源文件零改动**即可回放。
_PURE_LOCAL_STRATEGIES = (
    MaVolumeStrategy,
    HighTightFlagStrategy,
    LimitUpShakeoutStrategy,
    UptrendLimitDownStrategy,
)


def make_engine_in(tmp_dir: str) -> tuple[DataEngine, Settings]:
    """创建使用临时数据库的 DataEngine 与 Settings。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    return DataEngine(settings), settings


def seed_market(engine: DataEngine, n_symbols: int = 5) -> None:
    """塞一个小型市场：稳步上行、每天创新高的行情。

    刻意让 high 等于 close（没有上影线）且价格单调上行，这样今日收盘价
    必然高于前 20 日最高价——海龟策略会选中每一只票。若留出上影线，
    海龟一只都选不出来，依赖它有输出的属性就会变成永远为真的空断言。
    """
    rows = []
    for s in range(n_symbols):
        symbol = f"60000{s}"
        for i, date in enumerate(_DATES):
            open_price = 10.0 + s + i * 0.3
            close_price = open_price * 1.02
            rows.append(
                (
                    symbol,
                    date,
                    open_price,
                    close_price,
                    open_price * 0.98,
                    close_price,
                    2_000_000.0,
                    300_000_000.0,
                )
            )
    with sqlite3.connect(engine.db_path) as conn:
        conn.executemany(
            "INSERT INTO stock_daily "
            "(symbol, date, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    conn.close()


# Feature: sequoia-x-v2, Property 52: as-of 包装永远看不到未来行情
@given(cut=st.integers(min_value=1, max_value=27))
@h_settings(max_examples=30, deadline=None)
def test_as_of_engine_never_returns_future_bars(cut: int) -> None:
    """属性 52：AsOfEngine 返回的任何行情，日期都不得晚于 as_of。

    这是整个回放的地基。一旦漏了未来数据进来，策略会跑出漂亮得离谱的结果，
    而且不会有任何报错提示哪里不对。
    """
    as_of = _DATES[cut]
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        seed_market(engine)
        cache = MarketCache(engine)
        as_of_engine = AsOfEngine(cache, as_of, engine.db_path)

        for symbol in as_of_engine.get_local_symbols():
            df = as_of_engine.get_ohlcv(symbol)
            assert df.empty or df["date"].max() <= as_of

        market = as_of_engine.get_market_ohlcv()
        assert market.empty or market["date"].max() <= as_of


# Feature: sequoia-x-v2, Property 53: as-of 包装下 iloc[-1] 就是 as-of 当日
@given(cut=st.integers(min_value=1, max_value=27))
@h_settings(max_examples=30, deadline=None)
def test_as_of_last_bar_is_the_as_of_day(cut: int) -> None:
    """属性 53：截断后最后一根 bar 正是 as-of 当日。

    策略里的 df.iloc[-1] 表示"今天"。这条属性就是"零改动即可回放"
    这个说法成立的前提——若最后一根不是 as-of 当日，
    所有策略的当日判断都会错位一天。
    """
    as_of = _DATES[cut]
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        seed_market(engine)
        as_of_engine = AsOfEngine(MarketCache(engine), as_of, engine.db_path)

        df = as_of_engine.get_ohlcv("600000")

    assert df.iloc[-1]["date"] == as_of


# Feature: sequoia-x-v2, Property 54: 四个纯本地策略零改动即可回放
def test_pure_local_strategies_run_unmodified_under_as_of() -> None:
    """属性 54：四个纯本地策略在 AsOfEngine 下直接可跑，返回合法的代码列表。

    它们的源文件不该为了回放改动一行。这条属性一旦变红，
    说明有人给某个策略引入了 engine 之外的取数路径。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, settings = make_engine_in(tmp_dir)
        seed_market(engine)
        as_of_engine = AsOfEngine(MarketCache(engine), _DATES[-1], engine.db_path)

        for cls in _PURE_LOCAL_STRATEGIES:
            result = cls(engine=as_of_engine, settings=settings).run()
            assert isinstance(result, list)
            assert all(isinstance(s, str) and len(s) == 6 for s in result)


# Feature: sequoia-x-v2, Property 55: 回放不触碰实盘选股结果
@given(n_symbols=st.integers(min_value=1, max_value=5))
@h_settings(max_examples=10, deadline=None)
def test_replay_never_touches_selection_result(n_symbols: int) -> None:
    """属性 55：回放只写 selection_replay，selection_result 一行不变。

    selection_result 是实盘推荐的事实记录。掺进几万条模拟数据不可逆，
    还会把 Web 页面的日期列表淹没。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, settings = make_engine_in(tmp_dir)
        seed_market(engine)

        live_symbols = [f"60000{i}" for i in range(n_symbols)]
        engine.save_selection("2024-01-05", "LiveStrategy", live_symbols)
        before, before_total = engine.get_selections(limit=1000)

        replay_range(engine, settings, start=_DATES[20], end=_DATES[-1])

        after, after_total = engine.get_selections(limit=1000)

    assert before_total == after_total == n_symbols
    assert [r["symbol"] for r in before] == [r["symbol"] for r in after]


# Feature: sequoia-x-v2, Property 56: 定增策略被排除在回放之外
def test_private_placement_is_excluded_from_replay() -> None:
    """属性 56：PrivatePlacementStrategy 不在可回放清单里。

    它读的是定向增发公告接口，没有历史快照。"回放"出来的只会是
    今天的公告被按到每一个历史日期上，是纯粹的未来函数。
    """
    assert PrivatePlacementStrategy not in REPLAYABLE_STRATEGIES
    assert all(
        cls is not PrivatePlacementStrategy for cls in REPLAYABLE_STRATEGIES
    )


# Feature: sequoia-x-v2, Property 57: 回放里的海龟结果记在实盘类名下
def test_replay_records_turtle_under_its_real_name() -> None:
    """属性 57：回放写库时策略名用 TurtleTradeStrategy，不是回放子类名。

    否则收益报表里会冒出一个叫 _ReplayTurtleTradeStrategy 的陌生策略，
    和实盘的同名策略对不上，两边的数字再也没法比较。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, settings = make_engine_in(tmp_dir)
        seed_market(engine)
        replay_range(engine, settings, start=_DATES[20], end=_DATES[-1])

        with sqlite3.connect(engine.db_path) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT strategy FROM selection_replay"
                )
            }
        conn.close()

    assert not any(name.startswith("_Replay") for name in names)
    assert TurtleTradeStrategy.__name__ in names


# Feature: sequoia-x-v2, Property 58: as-of 包装不暴露任何写入或联网入口
def test_as_of_engine_exposes_no_write_or_network_path() -> None:
    """属性 58：AsOfEngine 上不存在写库与联网方法。

    回放绝不该改动数据库或调用 baostock。把这些方法挡在包装之外，
    越界访问会立刻 AttributeError，而不是悄悄写脏数据或打出网络请求。
    """
    forbidden = (
        "save_selection",
        "save_replay",
        "save_returns",
        "save_benchmark",
        "sync_today_bulk",
        "backfill",
        "get_all_symbols",
        "upsert_stock_basic",
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        seed_market(engine)
        as_of_engine = AsOfEngine(MarketCache(engine), _DATES[-1], engine.db_path)

        for name in forbidden:
            assert not hasattr(as_of_engine, name), f"AsOfEngine 不该暴露 {name}"
