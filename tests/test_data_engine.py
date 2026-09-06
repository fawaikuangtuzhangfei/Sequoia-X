"""数据引擎属性测试。"""

import logging
import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

import sequoia_x.data.engine as engine_module
from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine


def make_engine_in(tmp_dir: str) -> tuple[DataEngine, Settings]:
    """创建使用临时数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    return engine, settings


# Property 4: (symbol, date) 唯一约束防止重复写入
@given(
    symbol=st.text(min_size=6, max_size=6, alphabet="0123456789"),
    trade_date=st.dates(min_value=date(2024, 1, 1), max_value=date(2025, 12, 31)),
)
@h_settings(max_examples=50, deadline=None)
def test_unique_symbol_date_constraint(symbol: str, trade_date: date) -> None:
    """相同 (symbol, date) 插入两次，数据库中该组合记录数应保持为 1。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        row = {
            "symbol": symbol, "date": str(trade_date),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "volume": 1000.0, "turnover": 10500.0,
        }
        df = pd.DataFrame([row])
        # closing() 必不可少：`with sqlite3.connect()` 只提交事务，不关闭连接，
        # 未关闭的句柄会让 Windows 上的 TemporaryDirectory 清理失败。
        with closing(sqlite3.connect(engine.db_path)) as conn, conn:
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            try:
                df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            except sqlite3.IntegrityError:
                pass
            count = conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE symbol=? AND date=?",
                (symbol, str(trade_date)),
            ).fetchone()[0]
        assert count == 1


def _capture_engine_logs() -> tuple[list[logging.LogRecord], logging.Handler]:
    """engine logger 设置了 propagate=False，caplog 收不到，得直接挂 handler。"""
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _ListHandler(logging.WARNING)
    logging.getLogger(engine_module.__name__).addHandler(handler)
    return records, handler


def _run_sync_with(engine: DataEngine, batch_results: list) -> tuple[int, list[logging.LogRecord]]:
    """在给定的 worker 返回值下跑一次 sync_today_bulk。

    Pool 用 spawn 启动，子进程会重新 import 本模块，所以 patch
    _bs_fetch_batch 对子进程无效——必须把整个 Pool 换掉。
    """
    pool = MagicMock()
    pool.__enter__.return_value.map.return_value = batch_results
    records, handler = _capture_engine_logs()
    try:
        with patch("multiprocessing.Pool", return_value=pool):
            written = engine.sync_today_bulk()
    finally:
        logging.getLogger(engine_module.__name__).removeHandler(handler)
    return written, records


def _seed_one_stale_symbol(engine: DataEngine) -> None:
    """写一条很旧的记录，让 sync_today_bulk 认为有东西要更新。"""
    with closing(sqlite3.connect(engine.db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO stock_daily (symbol, date, open, high, low, close, volume, turnover) "
            "VALUES ('600519', '2024-01-02', 1, 1, 1, 1, 1, 1)"
        )


# Feature: sequoia-x-v2, Property 33: 数据源全挂与非交易日必须可区分
def test_total_fetch_failure_logs_error_not_no_data() -> None:
    """属性 33：所有拉取都失败时记 ERROR，而不是"无新数据（可能非交易日）"。

    这两种情况在数据上完全一样（一行都没拿到），含义却天差地别：
    后者是正常的休市，前者意味着策略即将基于过期数据选股。
    实测撞见过——8 个 worker 同时连 baostock 全部被拒，2000 次查询逐个超时，
    而日志只有一条 INFO。cron 每天这样跑，没人会发现。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        _seed_one_stale_symbol(engine)

        written, records = _run_sync_with(engine, [([], {"ok": 0, "failed": 250})])

    assert written == 0
    errors = [r for r in records if r.levelno == logging.ERROR]
    assert errors, "全部失败却没有记 ERROR"
    assert "数据源不可用" in errors[0].getMessage()


def test_genuine_non_trading_day_stays_quiet() -> None:
    """查询都成功但没有新 K 线，是正常的非交易日，不该报错。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        _seed_one_stale_symbol(engine)

        written, records = _run_sync_with(engine, [([], {"ok": 250, "failed": 0})])

    assert written == 0
    assert not [r for r in records if r.levelno >= logging.WARNING]


def test_partial_fetch_failure_warns() -> None:
    """部分失败要留下 WARNING：数据不完整，但不至于让整轮停下。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        _seed_one_stale_symbol(engine)

        written, records = _run_sync_with(engine, [([], {"ok": 200, "failed": 50})])

    assert written == 0
    warnings = [r for r in records if r.levelno == logging.WARNING]
    assert warnings and "部分失败" in warnings[0].getMessage()


def _seed_bars(engine: DataEngine, symbol: str, dates: list[str]) -> None:
    """给某只股票写入指定日期的 K 线。"""
    with closing(sqlite3.connect(engine.db_path)) as conn, conn:
        conn.executemany(
            "INSERT INTO stock_daily (symbol, date, open, high, low, close, volume, turnover) "
            "VALUES (?, ?, 1, 1, 1, 1, 100, 100)",
            [(symbol, d) for d in dates],
        )


def _dates_of(engine: DataEngine, symbol: str) -> list[str]:
    with closing(sqlite3.connect(engine.db_path)) as conn:
        return [r[0] for r in conn.execute(
            "SELECT date FROM stock_daily WHERE symbol = ? ORDER BY date", (symbol,))]


# Feature: sequoia-x-v2, Property 73: 同步只覆盖自己抓到的 (symbol, date)，不碰别的股票
def test_sync_does_not_delete_other_symbols_history() -> None:
    """属性 73：一只落后股补数据时，不得删掉其它股票同期的历史。

    写入路径曾经是「DELETE 掉 df 里出现过的每个日期，再整体 append」。
    日常同步时所有股票都只抓当天，看不出问题；可一旦各股票的 last_date
    参差不齐——任何一次部分失败之后必然如此——落后股会带回横跨数月的日期，
    于是那几个月被对**所有**股票删除，却只写回落后股那一份。

    这不是假想：生产库里全市场 2000 只的 2026-05-12..09-02 就是这样被挖空的，
    持续四个月，日志上始终显示"运行完成"。它是个失败放大器——
    一次失败造成参差，下一次同步就把好数据删掉。
    """
    fresh_dates = [f"2026-01-{d:02d}" for d in range(1, 11)]   # 健康股：10 根
    stale_dates = [f"2026-01-{d:02d}" for d in range(1, 4)]    # 落后股：只到 01-03

    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        _seed_bars(engine, "AAA", fresh_dates)
        _seed_bars(engine, "BBB", stale_dates)

        # 本轮只有落后股 BBB 抓回了 01-04..01-10
        fetched = [["BBB", f"2026-01-{d:02d}", "1", "1", "1", "1", "100", "100"]
                   for d in range(4, 11)]
        _run_sync_with(engine, [(fetched, {"ok": 2, "failed": 0})])

        assert _dates_of(engine, "AAA") == fresh_dates, "健康股的历史被删掉了"
        assert _dates_of(engine, "BBB") == fresh_dates, "落后股没有补齐"


def test_sync_refreshes_a_bar_it_refetches() -> None:
    """重抓同一根 K 线时用新值覆盖旧值，且不产生重复行。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        _seed_bars(engine, "AAA", ["2026-01-01"])

        fetched = [["AAA", "2026-01-01", "9", "9", "9", "9", "500", "500"]]
        _run_sync_with(engine, [(fetched, {"ok": 1, "failed": 0})])

        with closing(sqlite3.connect(engine.db_path)) as conn:
            rows = conn.execute(
                "SELECT close, volume FROM stock_daily WHERE symbol='AAA' AND date='2026-01-01'"
            ).fetchall()
    assert len(rows) == 1, "同一 (symbol, date) 出现了重复行"
    assert rows[0] == (9.0, 500.0), "重抓的数据没有覆盖旧值"


def _seed_stale_symbols(engine: DataEngine, n: int) -> None:
    """写 n 只很旧的股票，让 sync_today_bulk 有足够任务分给多个 worker。"""
    with closing(sqlite3.connect(engine.db_path)) as conn, conn:
        conn.executemany(
            "INSERT INTO stock_daily (symbol, date, open, high, low, close, volume, turnover) "
            "VALUES (?, '2024-01-02', 1, 1, 1, 1, 1, 1)",
            [(f"{600000 + i:06d}",) for i in range(n)],
        )


# Feature: sequoia-x-v2, Property 70: 同步并发数取自配置，且 worker 拿得到自己的序号
@given(configured=st.integers(min_value=1, max_value=16))
@h_settings(max_examples=12, deadline=None)
def test_sync_worker_count_comes_from_settings(configured: int) -> None:
    """属性 70：Pool 的进程数等于 settings.sync_workers，不是写死的 8。

    并发数写死过 8，而实测 8 个 worker 同时握手会被 baostock 全部拒绝。
    不同机器的网络差异很大，这个值必须能调——写死就意味着下次还得改代码。

    同时验证每个 chunk 都带上了 worker 序号：序号是错开登录的依据，
    丢了它 _bs_login_with_retry 的错开逻辑就退化成"全部立即重试"。
    """
    n_symbols = 20
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / "test.db"),
            start_date="2024-01-01",
            feishu_webhook_url="https://example.com/hook",
            sync_workers=configured,
        )
        engine = DataEngine(settings)
        _seed_stale_symbols(engine, n_symbols)

        pool = MagicMock()
        pool.__enter__.return_value.map.return_value = [([], {"ok": 1, "failed": 0})]
        with patch("multiprocessing.Pool", return_value=pool) as mock_pool:
            engine.sync_today_bulk()

        expected = min(configured, n_symbols)
        assert mock_pool.call_args.args[0] == expected, "Pool 进程数没有跟随配置"

        chunks = pool.__enter__.return_value.map.call_args.args[1]
        assert len(chunks) == expected
        assert [c[0] for c in chunks] == list(range(expected)), "chunk 未携带 worker 序号"
        # 任务必须完整分发，一只都不能漏
        assert sum(len(c[1]) for c in chunks) == n_symbols


# Feature: sequoia-x-v2, Property 71: 登录失败会重试，不是一锤子买卖
def test_login_retries_before_giving_up() -> None:
    """属性 71：前几次 login 失败后仍会重试，最终成功则返回 True。

    此前一次 bs.login() 不通，这个 worker 负责的几百只股票就全记为失败。
    而实测的失败形态恰恰是"N 个 worker 同时握手被集体拒绝"——
    这种失败退避一下大概率就过了，直接放弃等于把可恢复的故障变成数据缺口。
    """
    bs = MagicMock()
    bs.login.side_effect = [
        MagicMock(error_code="10001"),
        MagicMock(error_code="10001"),
        MagicMock(error_code="0"),
    ]
    with patch("time.sleep") as slept:  # 别在测试里真睡
        ok = engine_module._bs_login_with_retry(bs, worker_index=2, attempts=3)

    assert ok is True
    assert bs.login.call_count == 3
    assert slept.called


def test_first_login_is_staggered_by_worker_index() -> None:
    """首次连接必须按 worker 序号错开，哪怕这次登录一把就成功。

    实测的失败形态是"N 个 worker 同时握手被集体拒绝"，错开首连是最省事的
    规避方式。必须在**登录成功**的路径上验证：如果只在失败路径上断言
    "sleep 过"，退避那次 sleep 会替错开背书，去掉错开也测不出来——
    这条测试就是补上那个漏洞的（变异测试发现的）。
    """
    bs = MagicMock()
    bs.login.return_value = MagicMock(error_code="0")
    with patch("time.sleep") as slept:
        assert engine_module._bs_login_with_retry(bs, worker_index=3) is True

    # 一次成功 = 没有退避，所以唯一的 sleep 只可能是错开
    assert slept.call_count == 1
    assert slept.call_args.args[0] == 3 * engine_module._LOGIN_STAGGER_SECONDS


def test_login_gives_up_after_all_attempts() -> None:
    """一直失败时返回 False，让调用方把整批记为 failed（而非静默成功）。"""
    bs = MagicMock()
    bs.login.return_value = MagicMock(error_code="10001")
    with patch("time.sleep"):
        ok = engine_module._bs_login_with_retry(bs, worker_index=0, attempts=3)

    assert ok is False
    assert bs.login.call_count == 3


def test_sync_workers_rejects_zero() -> None:
    """0 会让 range(n_workers) 产出零个 chunk —— 一只都不拉却什么都不报。

    这种"配置成 0 就静默不干活"是最难查的一类故障，用字段约束挡在门口。
    """
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            db_path="x.db",
            feishu_webhook_url="https://example.com/hook",
            sync_workers=0,
        )


def test_worker_zero_does_not_stagger() -> None:
    """0 号 worker 不该白等：错开是相对的，第一个直接开跑。"""
    bs = MagicMock()
    bs.login.return_value = MagicMock(error_code="0")
    with patch("time.sleep") as slept:
        assert engine_module._bs_login_with_retry(bs, worker_index=0) is True
    slept.assert_not_called()
