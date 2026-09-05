"""数据引擎模块：负责 SQLite 行情数据存储与 baostock 增量同步。"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """打开 SQLite 连接，退出时提交事务并**关闭连接**。

    `with sqlite3.connect(...)` 只负责提交/回滚事务，并不关闭连接——
    连接要等垃圾回收才释放。这会导致：
      - Windows 上文件句柄未释放，临时数据库文件无法删除（测试失败）
      - 常驻进程（Web 服务）中每次查询都残留一个连接，句柄持续累积

    因此数据层统一通过本函数获取连接，不要直接写 `with sqlite3.connect(...)`。

    Yields:
        sqlite3.Connection: 事务语义与原先一致（正常退出提交，异常回滚）。
    """
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    turnover REAL,
    UNIQUE (symbol, date)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_symbol_date ON stock_daily (symbol, date);
"""

# rank 保存 run() 返回列表中的下标：策略输出顺序是有意义的
# （海龟按流通市值降序、定增按公告日期降序），不存下来等于丢失结果的一部分。
_CREATE_SELECTION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS selection_result (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT    NOT NULL,
    strategy TEXT    NOT NULL,
    symbol   TEXT    NOT NULL,
    rank     INTEGER NOT NULL,
    UNIQUE (run_date, strategy, symbol)
);
"""

_CREATE_SELECTION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_selection_date_strategy
    ON selection_result (run_date, strategy);
"""

_CREATE_STOCK_BASIC_SQL = """
CREATE TABLE IF NOT EXISTS stock_basic (
    symbol     TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _bs_fetch_batch(tasks: list) -> list:
    """多进程 worker：独立 login，批量拉取 baostock 数据。"""
    import baostock as bs
    bs.login()
    results = []
    for symbol, bs_code, start, end in tasks:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="1",  # 后复权
        )
        if rs.error_code != "0":
            continue
        while rs.next():
            results.append([symbol] + rs.get_row_data())
    bs.logout()
    return results


class DataEngine:
    """行情数据引擎，负责 SQLite 存储和 baostock 数据同步。"""

    def __init__(self, settings: Settings) -> None:
        self.db_path: str = settings.db_path
        self.start_date: str = settings.start_date
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.db_path) as conn:
            # WAL 模式：选股写入（cron）与 Web 只读查询可能并发，WAL 下读不阻塞写
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_SQL)
            conn.execute(_CREATE_SELECTION_TABLE_SQL)
            conn.execute(_CREATE_SELECTION_INDEX_SQL)
            conn.execute(_CREATE_STOCK_BASIC_SQL)
            conn.commit()
        logger.info(f"数据库初始化完成：{self.db_path}")

    def _get_last_date(self, symbol: str) -> str | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(date) FROM stock_daily WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        with _connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT * FROM stock_daily WHERE symbol = ? ORDER BY date",
                conn,
                params=(symbol,),
            )
        return df

    @staticmethod
    def _to_baostock_code(symbol: str) -> str:
        """将纯数字代码转为 baostock 格式：6/9开头 -> sh，其余 -> sz。"""
        prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
        return f"{prefix}.{symbol}"

    # ── 数据同步 ──

    def sync_today_bulk(self) -> int:
        """多进程并行通过 baostock 拉取增量数据（后复权），写入 SQLite。"""
        from datetime import date, timedelta
        from multiprocessing import Pool

        today_str = date.today().strftime("%Y-%m-%d")

        tasks = []
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT symbol, MAX(date) FROM stock_daily GROUP BY symbol"
            ).fetchall()

        if not rows:
            logger.warning("本地无股票数据，请先执行 --backfill")
            return 0

        for symbol, last_date in rows:
            if last_date and last_date >= today_str:
                continue
            start = today_str
            if last_date:
                start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            tasks.append((symbol, self._to_baostock_code(symbol), start, today_str))

        if not tasks:
            logger.info("所有股票已是最新，无需更新")
            return 0

        logger.info(f"需要更新 {len(tasks)} 只股票，启动多进程并行拉取...")

        n_workers = min(8, len(tasks))
        chunks = [tasks[i::n_workers] for i in range(n_workers)]

        with Pool(n_workers) as pool:
            batch_results = pool.map(_bs_fetch_batch, chunks)

        all_rows = []
        for batch in batch_results:
            all_rows.extend(batch)

        if not all_rows:
            logger.info("无新数据（可能非交易日）")
            return 0

        df = pd.DataFrame(all_rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]

        count = len(df)
        with _connect(self.db_path) as conn:
            for d in df["date"].unique().tolist():
                conn.execute("DELETE FROM stock_daily WHERE date = ?", (d,))
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi", chunksize=500)
            conn.commit()

        logger.info(f"sync_today_bulk: 写入 {count} 条数据")
        return count

    def backfill(self, symbols: list[str]) -> None:
        """通过 baostock 批量回填历史日 K 线数据（后复权）。

        容错机制：
        - 单只股票失败自动重试 3 次，间隔递增（2s/4s/8s）
        - 每 200 只股票自动重连 baostock（防止长连接超时）
        - 已入库的自动 skip，中断后可重跑续传
        """
        import time
        from datetime import date, timedelta

        import baostock as bs

        today_str = date.today().strftime("%Y-%m-%d")
        max_retries = 3
        reconnect_interval = 200  # 每处理 N 只股票重连一次

        def _login():
            lg = bs.login()
            if lg.error_code != "0":
                logger.error(f"baostock 登录失败: {lg.error_msg}")
                return False
            return True

        if not _login():
            return

        success = 0
        skipped = 0
        failed = 0
        since_reconnect = 0

        try:
            for i, symbol in enumerate(symbols):
                last_date = self._get_last_date(symbol)
                if last_date and last_date >= today_str:
                    skipped += 1
                    if (i + 1) % 500 == 0:
                        logger.info(
                            f"已处理 {i + 1}/{len(symbols)}，"
                            f"成功 {success} 跳过 {skipped} 失败 {failed}"
                        )
                    continue

                # 定期重连，防止长连接超时
                since_reconnect += 1
                if since_reconnect >= reconnect_interval:
                    bs.logout()
                    time.sleep(1)
                    if not _login():
                        logger.error("重连失败，终止回填")
                        return
                    since_reconnect = 0

                start = last_date or self.start_date
                if last_date:
                    start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")

                bs_code = self._to_baostock_code(symbol)

                # 带重试的查询
                rows = []
                query_ok = False
                for attempt in range(max_retries):
                    try:
                        rs = bs.query_history_k_data_plus(
                            bs_code,
                            "date,open,high,low,close,volume,amount",
                            start_date=start,
                            end_date=today_str,
                            frequency="d",
                            adjustflag="1",  # 后复权
                        )

                        if rs.error_code != "0":
                            raise RuntimeError(rs.error_msg)

                        rows = []
                        while rs.next():
                            rows.append(rs.get_row_data())
                        query_ok = True
                        break

                    except Exception as exc:
                        if attempt < max_retries - 1:
                            wait = 2 ** (attempt + 1)
                            logger.warning(
                                f"[{symbol}] 第{attempt + 1}次失败: {exc}，{wait}s 后重试"
                            )
                            time.sleep(wait)
                            # 重连 baostock
                            bs.logout()
                            time.sleep(1)
                            _login()
                        else:
                            logger.warning(f"[{symbol}] {max_retries}次重试均失败，跳过")

                if not query_ok:
                    failed += 1
                    continue

                if not rows:
                    skipped += 1
                    continue

                df = pd.DataFrame(rows, columns=rs.fields)
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"])
                df = df[df["volume"] > 0]

                if df.empty:
                    skipped += 1
                    continue

                df["symbol"] = symbol
                df = df.rename(columns={"amount": "turnover"})
                df = df[["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]]

                try:
                    with _connect(self.db_path) as conn:
                        df.to_sql(
                            "stock_daily", conn, if_exists="append",
                            index=False, method="multi", chunksize=500,
                        )
                except sqlite3.IntegrityError:
                    pass

                success += 1

                if (i + 1) % 500 == 0:
                    logger.info(
                        f"已处理 {i + 1}/{len(symbols)}，"
                        f"成功 {success} 跳过 {skipped} 失败 {failed}"
                    )

        finally:
            bs.logout()

        logger.info(f"回填完成 — 成功: {success} | 跳过: {skipped} | 失败: {failed}")

    # ── 股票列表 ──

    def get_all_symbols(self) -> list[str]:
        """
        通过 baostock 获取全市场 A 股代码列表。

        **副作用**：同一份 baostock 响应里就带着股票名称，本方法会顺手把
        (代码, 名称) 写入 stock_basic 表，避免为了拿名称再发一轮网络请求。
        名称写库失败只记 ERROR 日志，不影响本方法的返回值。

        Returns:
            上市状态的 A 股纯数字代码列表；查询失败时返回空列表。
        """
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            logger.error(f"baostock 登录失败: {lg.error_msg}")
            return []

        try:
            rs = bs.query_stock_basic(code_name="", code="")
            symbols = []
            basics: list[tuple[str, str]] = []
            while rs.next():
                row = rs.get_row_data()
                code = row[0]           # "sh.600000" or "sz.000001"
                name = row[1]           # 股票名称
                status = row[4]         # "1" = 上市
                stock_type = row[5]     # "1" = 股票
                if status == "1" and stock_type == "1":
                    symbol = code.split(".")[1]  # 提取纯数字代码
                    symbols.append(symbol)
                    # 名称本来就在同一份响应里，顺手入库，零额外网络开销
                    if name:
                        basics.append((symbol, name))
            logger.info(f"获取股票列表完成，共 {len(symbols)} 只")

            if basics:
                try:
                    self.upsert_stock_basic(basics)
                except Exception as exc:
                    # 名称只用于展示，写失败不能影响股票列表这个主返回值
                    logger.error(f"股票名称写库失败：{exc}")

            return symbols
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return []
        finally:
            bs.logout()

    def get_local_symbols(self) -> list[str]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM stock_daily"
            ).fetchall()
        return [row[0] for row in rows]

    def upsert_stock_basic(self, rows: list[tuple[str, str]]) -> int:
        """
        批量写入股票代码与名称，已存在则更新名称。

        Args:
            rows: (symbol, name) 二元组列表。

        Returns:
            写入的记录条数。
        """
        if not rows:
            return 0

        from datetime import date

        today_str = date.today().strftime("%Y-%m-%d")
        payload = [(symbol, name, today_str) for symbol, name in rows]

        with _connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO stock_basic (symbol, name, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET name = excluded.name, "
                "updated_at = excluded.updated_at",
                payload,
            )
            conn.commit()

        return len(payload)

    def get_stock_names(self, symbols: list[str]) -> dict[str, str]:
        """
        批量查询股票名称。

        取代 notify 层原先"每只股票发一次 baostock 请求"的做法：一个策略选出
        13 只就是 13 次网络往返，四个策略叠加，推送链路的耗时和失败面都被
        放大了一个数量级。名称本来就在 stock_basic 表里。

        Args:
            symbols: 纯 6 位代码列表。

        Returns:
            {代码: 名称}。查不到的代码**不出现在结果里**，由调用方决定降级展示，
            而不是在这里编一个占位符。stock_basic 为空时返回空字典。
        """
        if not symbols:
            return {}

        # SQLite 对单条语句的参数个数有上限（新版 32766，旧版 999），
        # 分批查询，避免选股结果特别多时踩到这个限制。
        chunk_size = 500
        mapping: dict[str, str] = {}

        with _connect(self.db_path) as conn:
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT symbol, name FROM stock_basic WHERE symbol IN ({placeholders})",
                    chunk,
                ).fetchall()
                mapping.update({row[0]: row[1] for row in rows})

        return mapping

    # ── 选股结果 ──

    def save_selection(self, run_date: str, strategy: str, symbols: list[str]) -> int:
        """
        持久化一次选股结果。

        同一 (run_date, strategy) 组合先删后插，保证重复运行 main.py 幂等。
        采用先删再插而非 INSERT OR REPLACE，是因为重跑时结果集可能变小，
        必须让上一次遗留的多余记录消失。

        symbols 为空列表时只执行删除，写入 0 条——这样"当天该策略没选出票"
        与"当天没跑过该策略"在数据上可以区分。

        重复代码会被去重（保留首次出现的位置）并记 WARNING。策略本不该返回
        重复项，但直接让 UNIQUE 约束抛错会导致该策略当天的结果整批丢失，
        代价远大于收益；去重加告警既保住数据，又让异常在日志里可见。

        Args:
            run_date: 选股日期，格式 'YYYY-MM-DD'。
            strategy: 策略类名，如 'TurtleTradeStrategy'。
            symbols: run() 返回的股票代码列表，顺序即 rank。

        Returns:
            实际写入的记录条数（去重后）。
        """
        seen: set[str] = set()
        unique_symbols: list[str] = []
        for symbol in symbols:
            if symbol not in seen:
                seen.add(symbol)
                unique_symbols.append(symbol)

        if len(unique_symbols) != len(symbols):
            logger.warning(
                f"[{strategy}] 选股结果含重复代码，已去重："
                f"{len(symbols)} -> {len(unique_symbols)}"
            )

        with _connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM selection_result WHERE run_date = ? AND strategy = ?",
                (run_date, strategy),
            )
            if unique_symbols:
                conn.executemany(
                    "INSERT INTO selection_result (run_date, strategy, symbol, rank) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        (run_date, strategy, symbol, i)
                        for i, symbol in enumerate(unique_symbols)
                    ],
                )
            conn.commit()

        return len(unique_symbols)

    def get_selection_dates(self) -> list[str]:
        """有选股结果的日期列表，按时间倒序（最新在前）。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT run_date FROM selection_result ORDER BY run_date DESC"
            ).fetchall()
        return [row[0] for row in rows]

    def get_selection_strategies(self) -> list[str]:
        """有选股结果的策略名列表，按字母序。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT strategy FROM selection_result ORDER BY strategy"
            ).fetchall()
        return [row[0] for row in rows]

    def get_selections(
        self,
        run_date: str | None = None,
        strategy: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """
        分页查询选股结果，附带股票名称。

        名称通过 LEFT JOIN stock_basic 获取，查不到为 None——名称只是展示信息，
        不应成为查询失败的理由。结果一律按 rank 升序，还原策略的输出顺序。

        Args:
            run_date: 按日期过滤，None 表示不限。
            strategy: 按策略名过滤，None 表示不限。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            (记录列表, 符合条件的总条数)。记录含 run_date / strategy /
            symbol / name / rank 五个键。
        """
        # WHERE 子句由源码中的字面量片段拼成，过滤值一律走 ? 占位符，
        # 不存在注入面。不要改写成 (? IS NULL OR col = ?)——那种写法会让
        # SQLite 放弃 idx_selection_date_strategy 而全表扫描。
        clauses: list[str] = []
        params: list[str] = []
        if run_date:
            clauses.append("s.run_date = ?")
            params.append(run_date)
        if strategy:
            clauses.append("s.strategy = ?")
            params.append(strategy)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with _connect(self.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM selection_result s {where_sql}",
                params,
            ).fetchone()[0]

            rows = conn.execute(
                "SELECT s.run_date, s.strategy, s.symbol, b.name, s.rank "
                "FROM selection_result s "
                "LEFT JOIN stock_basic b ON b.symbol = s.symbol "
                f"{where_sql} "
                "ORDER BY s.run_date DESC, s.strategy, s.rank "
                "LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()

        items = [
            {
                "run_date": row[0],
                "strategy": row[1],
                "symbol": row[2],
                "name": row[3],
                "rank": row[4],
            }
            for row in rows
        ]
        return items, total
