"""选股收益计算的属性测试。

这些属性守的是同一件事：算错了不会报错，只会给出一张好看的表。
每条断言都对应一个"改坏了也能跑通"的地方。
"""

import sqlite3
import tempfile
from pathlib import Path

from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.backtest.benchmark import refresh_benchmark
from sequoia_x.backtest.returns import compute_returns
from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine

# 一段连续的交易日，够跑 20 日持有期还有富余。
_DATES: list[str] = [f"2024-01-{day:02d}" for day in range(1, 29)]


def make_engine_in(tmp_dir: str) -> DataEngine:
    """创建使用临时数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    return DataEngine(settings)


def seed_bars(
    engine: DataEngine,
    symbol: str,
    dates: list[str],
    opens: list[float],
    closes: list[float],
) -> None:
    """往 stock_daily 塞一只股票的日线。高低价取开收的包络，成交量固定。"""
    rows = [
        (
            symbol,
            date,
            opens[i],
            max(opens[i], closes[i]),
            min(opens[i], closes[i]),
            closes[i],
            1_000_000.0,
            200_000_000.0,
        )
        for i, date in enumerate(dates)
    ]
    with sqlite3.connect(engine.db_path) as conn:
        conn.executemany(
            "INSERT INTO stock_daily "
            "(symbol, date, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    conn.close()


def seed_flat_market(engine: DataEngine, dates: list[str], n_symbols: int = 4) -> None:
    """塞一批价格恒定的陪跑股票，让基准存在且日收益恒为 0。

    基准恒为 0 时超额收益就等于个股收益，手算断言不必再算一遍基准。
    """
    for i in range(n_symbols):
        symbol = f"9000{i:02d}"
        seed_bars(engine, symbol, dates, [100.0] * len(dates), [100.0] * len(dates))


# Feature: sequoia-x-v2, Property 41: 收益等于 T+1 开盘买入到第 N 日收盘卖出
@given(
    horizon=st.integers(min_value=1, max_value=5),
    buy_open=st.floats(min_value=5.0, max_value=200.0, allow_nan=False),
    sell_close=st.floats(min_value=5.0, max_value=200.0, allow_nan=False),
)
@h_settings(max_examples=30, deadline=None)
def test_return_matches_hand_computation(
    horizon: int, buy_open: float, sell_close: float
) -> None:
    """属性 41：收益必须等于 (第 N 日收盘 / 买入日开盘 - 1)。

    买入日是推荐日之后的下一个交易日——用 >= 而不是 > 取买入日，
    就等于拿当天收盘信息买当天，是最典型的未来函数。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        seed_flat_market(engine, _DATES)

        # 推荐日定在下标 0，买入日应当是下标 1，卖出日是 1 + horizon - 1。
        buy_idx = 1
        sell_idx = buy_idx + horizon - 1
        opens = [10.0] * len(_DATES)
        closes = [10.0] * len(_DATES)
        opens[buy_idx] = buy_open
        closes[sell_idx] = sell_close
        seed_bars(engine, "600000", _DATES, opens, closes)
        refresh_benchmark(engine)

        rows, _ = compute_returns(
            engine,
            [(_DATES[0], "S", "600000")],
            horizons=(horizon,),
        )

    assert len(rows) == 1
    row = rows[0]
    assert row.buy_date == _DATES[buy_idx]
    assert row.sell_date == _DATES[sell_idx]
    assert row.ret == (sell_close / buy_open - 1.0)


# Feature: sequoia-x-v2, Property 42: 未到期样本永不落库
@given(horizon=st.integers(min_value=2, max_value=15))
@h_settings(max_examples=30, deadline=None)
def test_immature_samples_are_never_emitted(horizon: int) -> None:
    """属性 42：卖出日的行情还不存在时，该组合不产出任何记录。

    若改成"取最后一根 bar"，所有未到期样本都会被记成一段不足 N 天的收益，
    统计量被系统性稀释向 0——而且报表上完全看不出来。
    """
    # 行情只到第 horizon 根，推荐日在倒数第二根，凑不满 horizon 天。
    dates = _DATES[:horizon]
    run_date = dates[-2]

    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        seed_flat_market(engine, dates)
        seed_bars(engine, "600000", dates, [10.0] * len(dates), [10.0] * len(dates))
        refresh_benchmark(engine)

        rows, stats = compute_returns(
            engine, [(run_date, "S", "600000")], horizons=(horizon,)
        )

    assert rows == []
    assert stats.immature >= 1
    assert stats.computed == 0


# Feature: sequoia-x-v2, Property 43: 重复计算同一批样本结果一致
@given(horizon=st.integers(min_value=1, max_value=5))
@h_settings(max_examples=20, deadline=None)
def test_compute_is_idempotent(horizon: int) -> None:
    """属性 43：同一批样本算两次并入库两次，库里逐条一致且不翻倍。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        seed_flat_market(engine, _DATES)
        seed_bars(engine, "600000", _DATES, [10.0] * len(_DATES), [11.0] * len(_DATES))
        refresh_benchmark(engine)

        samples = [(_DATES[0], "S", "600000")]
        first, _ = compute_returns(engine, samples, horizons=(horizon,))
        engine.save_returns([tuple(r) for r in first])
        after_first = engine.get_returns("live")

        second, _ = compute_returns(engine, samples, horizons=(horizon,))
        engine.save_returns([tuple(r) for r in second])
        after_second = engine.get_returns("live")

    assert len(after_first) == len(after_second) == 1
    assert after_first["ret"].tolist() == after_second["ret"].tolist()


# Feature: sequoia-x-v2, Property 44: 次日停牌的信号被丢弃而不是顺延
def test_suspended_next_day_sample_is_dropped() -> None:
    """属性 44：推荐日的下一个交易日该股没有行情时，样本必须被丢弃。

    顺延到复牌再买是另一笔交易。实测中一只停牌两个月的股票，
    "顺延"会让 7 月的信号在 9 月成交，并把这两个月的涨跌算进策略账上。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        seed_flat_market(engine, _DATES)

        # 该股在下标 1..5 停牌（缺 bar），下标 6 才复牌。
        own_dates = [_DATES[0]] + _DATES[6:]
        seed_bars(
            engine,
            "600000",
            own_dates,
            [10.0] * len(own_dates),
            [10.0] * len(own_dates),
        )
        refresh_benchmark(engine)

        rows, stats = compute_returns(
            engine, [(_DATES[0], "S", "600000")], horizons=(1,)
        )

    assert rows == []
    assert stats.unbuyable == 1


# Feature: sequoia-x-v2, Property 45: 超额收益恒等于个股收益减基准收益
@given(
    horizon=st.integers(min_value=1, max_value=5),
    drift=st.floats(min_value=-0.05, max_value=0.05, allow_nan=False),
)
@h_settings(max_examples=30, deadline=None)
def test_excess_equals_ret_minus_bench(horizon: int, drift: float) -> None:
    """属性 45：excess_ret == ret - bench_ret，对任意行情都成立。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)

        # 让市场每天按 drift 漂移，基准就不再恒为 0。
        prices = [100.0 * (1.0 + drift) ** i for i in range(len(_DATES))]
        for i in range(4):
            seed_bars(engine, f"9000{i:02d}", _DATES, prices, prices)
        seed_bars(engine, "600000", _DATES, prices, prices)
        refresh_benchmark(engine)

        rows, _ = compute_returns(
            engine, [(_DATES[0], "S", "600000")], horizons=(horizon,)
        )

    for row in rows:
        assert abs(row.excess_ret - (row.ret - row.bench_ret)) < 1e-12


# Feature: sequoia-x-v2, Property 46: 空样本集合不抛异常
def test_empty_samples_return_empty() -> None:
    """属性 46：没有样本时返回空结果，不抛异常。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        rows, stats = compute_returns(engine, [])

    assert rows == []
    assert stats.computed == 0


# Feature: sequoia-x-v2, Property 47: 买入日一字涨停的样本标记为买不进
@given(
    symbol=st.sampled_from(["600000", "300001", "688001"]),
    over=st.floats(min_value=0.001, max_value=0.05, allow_nan=False),
)
@h_settings(max_examples=30, deadline=None)
def test_limit_up_open_is_marked_untradable(symbol: str, over: float) -> None:
    """属性 47：买入日开盘涨幅达到板块涨停阈值时 tradable 必须为 0。

    这些票根本买不进，把它们的收益算进来是自欺欺人——
    涨停洗盘类策略尤其容易整片踩中。
    """
    threshold = 0.196 if symbol.startswith(("300", "301", "688")) else 0.098
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        seed_flat_market(engine, _DATES)

        opens = [10.0] * len(_DATES)
        closes = [10.0] * len(_DATES)
        # 买入日（下标 1）开盘价相对前一日收盘涨到阈值之上。
        opens[1] = closes[0] * (1.0 + threshold + over)
        seed_bars(engine, symbol, _DATES, opens, closes)
        refresh_benchmark(engine)

        rows, _ = compute_returns(engine, [(_DATES[0], "S", symbol)], horizons=(1,))

    assert len(rows) == 1
    assert rows[0].tradable == 0


# Feature: sequoia-x-v2, Property 48: 买入日未涨停的样本标记为可成交
@given(under=st.floats(min_value=0.001, max_value=0.05, allow_nan=False))
@h_settings(max_examples=30, deadline=None)
def test_normal_open_is_marked_tradable(under: float) -> None:
    """属性 48：买入日开盘涨幅低于阈值时 tradable 必须为 1。

    与属性 47 成对：只断言一侧的话，一个恒返回 0 的实现也能通过。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        seed_flat_market(engine, _DATES)

        opens = [10.0] * len(_DATES)
        closes = [10.0] * len(_DATES)
        opens[1] = closes[0] * (1.0 + 0.098 - under)
        seed_bars(engine, "600000", _DATES, opens, closes)
        refresh_benchmark(engine)

        rows, _ = compute_returns(engine, [(_DATES[0], "S", "600000")], horizons=(1,))

    assert len(rows) == 1
    assert rows[0].tradable == 1


# Feature: sequoia-x-v2, Property 49: 实盘与回放的同键样本互不覆盖
@given(horizon=st.integers(min_value=1, max_value=5))
@h_settings(max_examples=20, deadline=None)
def test_live_and_replay_rows_coexist(horizon: int) -> None:
    """属性 49：source 不同的同键收益明细可以并存。

    source 若不在唯一键里，回放会把实盘的收益记录悄悄覆盖掉，
    而两者混在一起平均出来的数字没有任何意义。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        seed_flat_market(engine, _DATES)
        seed_bars(engine, "600000", _DATES, [10.0] * len(_DATES), [12.0] * len(_DATES))
        refresh_benchmark(engine)

        samples = [(_DATES[0], "S", "600000")]
        for source in ("live", "replay"):
            rows, _ = compute_returns(
                engine, samples, horizons=(horizon,), source=source
            )
            engine.save_returns([tuple(r) for r in rows])

        live = engine.get_returns("live")
        replay = engine.get_returns("replay")

    assert len(live) == 1
    assert len(replay) == 1


# Feature: sequoia-x-v2, Property 50: 基准只采纳前一根 K 线正好是上个交易日的样本
def test_benchmark_ignores_returns_spanning_gaps() -> None:
    """属性 50：跨越停牌/回填空洞的涨跌幅不得计入基准日收益。

    少了这道过滤，LAG 会把跨越两周的涨跌当成一天的日收益。
    实测中这让某一天的全市场等权基准从 -1% 变成 -8.6%，
    进而把当天所有样本的超额收益整体抬高 7 个百分点。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)

        # 三只股票全程在场，价格恒定，日收益为 0。
        for i in range(3):
            seed_bars(engine, f"9000{i:02d}", _DATES, [100.0] * 28, [100.0] * 28)

        # 一只股票中间缺了一大段，复牌当天价格跌两成。
        # 跌幅刻意选在异常值阈值（±50%）以内：若用腰斩，
        # 挡住它的会是异常值过滤而不是 gap=1，这条属性就成了摆设。
        gap_dates = [_DATES[0], _DATES[20]]
        seed_bars(engine, "600000", gap_dates, [100.0, 80.0], [100.0, 80.0])

        refresh_benchmark(engine)
        series = dict(engine.get_benchmark_series())

    # 三只陪跑股当天日收益为 0；跨空洞的 -20% 必须被排除在外。
    assert abs(series[_DATES[20]]) < 1e-12


# Feature: sequoia-x-v2, Property 51: 持仓期停牌过久的组合被丢弃
def test_long_suspension_during_holding_is_dropped() -> None:
    """属性 51：持有期内停牌太久时，该组合被丢弃而不是记成一段超长持有。

    "持有 20 个交易日"里如果混进两个月的停牌，收益反映的是停牌期间的
    市场变化，不是这个策略的持有结果。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine = make_engine_in(tmp_dir)
        seed_flat_market(engine, _DATES)

        # 买入日（下标 1）正常，之后直接跳到下标 27，中间全部停牌。
        own_dates = [_DATES[0], _DATES[1], _DATES[27]]
        seed_bars(engine, "600000", own_dates, [10.0] * 3, [10.0, 10.0, 30.0])
        refresh_benchmark(engine)

        rows, stats = compute_returns(
            engine, [(_DATES[0], "S", "600000")], horizons=(2,)
        )

    assert rows == []
    assert stats.hold_slipped == 1
