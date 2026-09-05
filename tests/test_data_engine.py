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
