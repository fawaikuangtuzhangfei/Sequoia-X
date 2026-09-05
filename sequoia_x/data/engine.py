"""数据引擎模块：负责 SQLite 行情数据存储与 baostock 增量同步。"""

import sqlite3
from collections.abc import Iterator, Sequence
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

# 回放推荐。与 selection_result 同构但独立成表，绝不混用：
# 那张表是实盘推荐的事实记录，掺进模拟数据不可逆，且回放数据量是实盘的
# 数百倍，混表会让 Web 的日期列表被模拟数据淹没。
_CREATE_SELECTION_REPLAY_SQL = """
CREATE TABLE IF NOT EXISTS selection_replay (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT    NOT NULL,
    strategy TEXT    NOT NULL,
    symbol   TEXT    NOT NULL,
    rank     INTEGER NOT NULL,
    UNIQUE (run_date, strategy, symbol)
);
"""

_CREATE_SELECTION_REPLAY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_replay_date_strategy
    ON selection_replay (run_date, strategy);
"""

# 收益明细。source 进 UNIQUE 键，让同一天同一策略的实盘与回放样本并存
# 而不互相覆盖；任何汇总查询都必须带 source 过滤，否则实盘和模拟被混在
# 一起平均，得出的数字没有任何意义。
#
# 未到期样本（卖出日行情尚不存在）根本不写入本表，而不是写成 ret = NULL：
# NULL 会被 AVG() 静默跳过，行为取决于写查询的人记不记得；
# "这条记录不存在"没有歧义。到期后自然被补算进来。
_CREATE_SELECTION_RETURN_SQL = """
CREATE TABLE IF NOT EXISTS selection_return (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    run_date    TEXT    NOT NULL,
    strategy    TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    horizon     INTEGER NOT NULL,
    buy_date    TEXT    NOT NULL,
    sell_date   TEXT    NOT NULL,
    buy_price   REAL    NOT NULL,
    sell_price  REAL    NOT NULL,
    ret         REAL    NOT NULL,
    bench_ret   REAL    NOT NULL,
    excess_ret  REAL    NOT NULL,
    tradable    INTEGER NOT NULL,
    computed_at TEXT    NOT NULL,
    UNIQUE (source, run_date, strategy, symbol, horizon)
);
"""

_CREATE_SELECTION_RETURN_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_return_strategy_horizon
    ON selection_return (source, strategy, horizon);
"""

# 全市场等权日收益。存日收益而非区间收益：区间基准由日收益连乘推出，
# 任意持有期共用同一份数据，不必为每个 horizon 各存一份。
# symbol_count 是可信度指标——回填早期只有少数股票有数据时等权收益噪声极大，
# 这一列让人看得见，而不是被蒙在鼓里。
_CREATE_BENCHMARK_SQL = """
CREATE TABLE IF NOT EXISTS market_benchmark (
    date         TEXT PRIMARY KEY,
    ret          REAL    NOT NULL,
    symbol_count INTEGER NOT NULL
);
"""

# source -> 选股结果表名的白名单。表名无法用 ? 占位符传参，
# 这份映射保证进入 SQL 文本的表名永远来自源码字面量而非调用方输入。
_PICK_TABLES: dict[str, str] = {
    "live": "selection_result",
    "replay": "selection_replay",
}

# stock_daily 的可读列白名单，get_market_ohlcv 用它校验调用方请求的列名。
_MARKET_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
)


def _bs_fetch_batch(tasks: list) -> tuple[list, dict[str, int]]:
    """多进程 worker：独立 login，批量拉取 baostock 数据。

    **必须把失败计数回传给父进程。** 这些 worker 跑在独立进程里，它们的日志
    未必能汇总；而在此之前失败是完全静默的：login 连不上、每次查询超时，
    最终都只表现为"没有数据"，与真正的非交易日无法区分。

    实测过这个场景：8 个 worker 同时连 baostock 时全部被拒，2000 次查询
    逐个超时，父进程照常记一条 INFO"无新数据（可能非交易日）"。cron 每天
    这样跑，策略会一直用过期数据选股，而没有任何告警。

    Returns:
        (数据行, {"ok": 成功只数, "failed": 失败只数})。
    """
    import baostock as bs

    stats = {"ok": 0, "failed": 0}

    lg = bs.login()
    if lg.error_code != "0":
        # 连不上就别再逐只重试了，整批直接记为失败
        stats["failed"] = len(tasks)
        return [], stats

    results = []
    try:
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
                stats["failed"] += 1
                continue
            while rs.next():
                results.append([symbol] + rs.get_row_data())
            stats["ok"] += 1
    finally:
        bs.logout()

    return results, stats


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
            conn.execute(_CREATE_SELECTION_REPLAY_SQL)
            conn.execute(_CREATE_SELECTION_REPLAY_INDEX_SQL)
            conn.execute(_CREATE_SELECTION_RETURN_SQL)
            conn.execute(_CREATE_SELECTION_RETURN_INDEX_SQL)
            conn.execute(_CREATE_BENCHMARK_SQL)
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
        n_ok = 0
        n_failed = 0
        for batch, stats in batch_results:
            all_rows.extend(batch)
            n_ok += stats["ok"]
            n_failed += stats["failed"]

        # 全军覆没和"今天休市"在数据上长得一模一样，但含义天差地别：
        # 前者意味着策略即将用过期数据选股，必须吵起来。
        if n_failed and not n_ok:
            logger.error(
                f"baostock 拉取全部失败（{n_failed} 只），本次同步没有拿到任何数据。"
                "这不是非交易日，是数据源不可用——策略将基于过期数据运行。"
            )
            return 0

        if n_failed:
            logger.warning(f"baostock 拉取部分失败：成功 {n_ok} 只，失败 {n_failed} 只")

        if not all_rows:
            logger.info(f"无新数据，可能是非交易日（{n_ok} 只查询成功但都没有新 K 线）")
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

    def _save_picks(
        self, source: str, run_date: str, strategy: str, symbols: list[str]
    ) -> int:
        """按 source 把一批选股结果写入对应的表。

        实盘（selection_result）与回放（selection_replay）两张表同构，
        去重、先删后插、空结果也写这三条语义完全一致，因此共用这一份实现。
        复制一份会让两边的行为在后续修改中悄悄分叉。

        Args:
            source: 'live' 或 'replay'，决定写入哪张表。
            run_date: 选股日期，格式 'YYYY-MM-DD'。
            strategy: 策略类名。
            symbols: run() 返回的股票代码列表，顺序即 rank。

        Returns:
            实际写入的记录条数（去重后）。

        Raises:
            ValueError: source 不是 'live' 或 'replay' 时抛出。
        """
        # 表名不能用 ? 占位符，因此从字面量白名单里查——调用方传进来的字符串
        # 永远不会进入 SQL 文本，注入面为零。
        table = _PICK_TABLES.get(source)
        if table is None:
            raise ValueError(f"未知的 source：{source}")

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
                f"DELETE FROM {table} WHERE run_date = ? AND strategy = ?",
                (run_date, strategy),
            )
            if unique_symbols:
                conn.executemany(
                    f"INSERT INTO {table} (run_date, strategy, symbol, rank) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        (run_date, strategy, symbol, i)
                        for i, symbol in enumerate(unique_symbols)
                    ],
                )
            conn.commit()

        return len(unique_symbols)

    def save_replay(self, run_date: str, strategy: str, symbols: list[str]) -> int:
        """
        持久化一次历史回放的选股结果，写入 selection_replay。

        语义与 save_selection 完全一致，只是落到另一张表。回放结果绝不能写进
        selection_result——那张表是实盘推荐的事实记录，掺进模拟数据不可逆。

        Args:
            run_date: 回放的 as-of 日期，格式 'YYYY-MM-DD'。
            strategy: 策略类名。
            symbols: run() 返回的股票代码列表，顺序即 rank。

        Returns:
            实际写入的记录条数（去重后）。
        """
        return self._save_picks("replay", run_date, strategy, symbols)

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
        return self._save_picks("live", run_date, strategy, symbols)

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

    # ── 回测：全市场读取、基准、收益明细 ──

    def get_market_ohlcv(self, columns: Sequence[str] = _MARKET_COLUMNS) -> pd.DataFrame:
        """
        一次性读入全市场日线，按 (symbol, date) 升序。

        横截面策略（RPS 排名）和回放缓存都需要整张表，逐只 get_ohlcv 会产生
        数千次查询。这是 sqlite-guidelines 里指明的做法——需要全市场数据时
        在 DataEngine 上加方法，而不是在调用方另开一条裸 SQL 连接。

        Args:
            columns: 需要的列，默认 symbol/date/open/high/low/close/volume/turnover。
                只取用得上的列可以显著降低内存占用。

        Returns:
            含所请求列的 DataFrame，按 symbol、date 升序。

        Raises:
            ValueError: 请求了 stock_daily 不存在的列时抛出。
        """
        unknown = [col for col in columns if col not in _MARKET_COLUMNS]
        if unknown:
            raise ValueError(f"stock_daily 无此列：{unknown}")

        # 列名同样不能用 ? 占位符，故先对着字面量白名单校验再拼接。
        col_sql = ", ".join(columns)
        with _connect(self.db_path) as conn:
            df = pd.read_sql(
                f"SELECT {col_sql} FROM stock_daily ORDER BY symbol, date",
                conn,
            )
        return df

    def get_trading_dates(self) -> list[str]:
        """全库出现过的交易日，升序。回放区间与基准都以此为准，不引入日历库。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM stock_daily ORDER BY date"
            ).fetchall()
        return [row[0] for row in rows]

    def get_st_symbols(self) -> set[str]:
        """
        当前名称含 ST 的股票代码集合，用于涨停阈值判定。

        依据的是 stock_basic 里的**当前**名称，不是推荐日当时的名称——
        stock_basic 只存一个快照，没有历史。这个近似会让个别"当时不是 ST、
        现在是 ST"的样本用错阈值，方向上偏保守（更容易被判成买不进）。
        """
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT symbol FROM stock_basic WHERE name LIKE '%ST%'"
            ).fetchall()
        return {row[0] for row in rows}

    def compute_market_benchmark(
        self, outlier_abs_ret: float
    ) -> tuple[list[tuple[str, float, int]], int]:
        """
        由 stock_daily 现算全市场等权日收益。

        窗口函数要求 SQLite >= 3.25，Python 3.10 自带的版本已满足。
        一次全表扫描算出所有交易日，不做按日期的增量计算——LAG 需要前一个
        交易日的收盘价，把输入按日期截断会让区间第一天的收益变成 NULL。

        Args:
            outlier_abs_ret: 单日涨跌幅绝对值的剔除阈值，如 0.5 表示
                剔除 |涨跌幅| >= 50% 的记录后再取均值。

        Returns:
            ((date, ret, symbol_count) 列表, 剔除前的原始记录数)。
            两者相减即被剔除的异常记录数。
        """
        # gap = 1 这个条件是本查询的要害：它要求某只股票的前一根 K 线正好落在
        # 上一个**全市场交易日**上。少了它，停牌复牌或回填不完整造成的空洞会让
        # LAG 把跨越两周的涨跌幅当成一天的日收益算进等权均值——实测能把某一天的
        # 基准从 -1% 拉到 -8.6%。用交易日序号相减而不是日历天数相减，
        # 是为了让春节这类长假自然落在 gap = 1 上，不被误伤。
        # DENSE_RANK() OVER (ORDER BY date) 直接给出该 bar 落在第几个全市场
        # 交易日上，省掉与 DISTINCT date 子表的 join——那种写法在百万行上
        # 会退化成逐行查找，实测跑二十分钟都出不来。
        ranked_sql = (
            "SELECT symbol, date, close, DENSE_RANK() OVER (ORDER BY date) AS rn "
            "FROM stock_daily"
        )
        rets_sql = (
            f"WITH ranked AS ({ranked_sql}), rets AS ("
            "  SELECT date,"
            "         close / LAG(close) OVER w - 1 AS daily_ret,"
            "         rn - LAG(rn) OVER w AS gap"
            "  FROM ranked"
            "  WINDOW w AS (PARTITION BY symbol ORDER BY rn)"
            ")"
        )

        with _connect(self.db_path) as conn:
            rows = conn.execute(
                rets_sql + "SELECT date, AVG(daily_ret), COUNT(*) FROM rets "
                "WHERE daily_ret IS NOT NULL AND gap = 1 "
                "  AND daily_ret > ? AND daily_ret < ? "
                "GROUP BY date ORDER BY date",
                (-outlier_abs_ret, outlier_abs_ret),
            ).fetchall()

            raw_count = conn.execute(
                rets_sql + "SELECT COUNT(*) FROM rets "
                "WHERE daily_ret IS NOT NULL AND gap = 1"
            ).fetchone()[0]

        return [(row[0], float(row[1]), int(row[2])) for row in rows], int(raw_count)

    def save_benchmark(self, rows: list[tuple[str, float, int]]) -> int:
        """
        批量写入全市场等权日收益。

        Args:
            rows: (date, ret, symbol_count) 三元组列表。

        Returns:
            写入的记录条数。
        """
        if not rows:
            return 0

        with _connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO market_benchmark (date, ret, symbol_count) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET ret = excluded.ret, "
                "symbol_count = excluded.symbol_count",
                rows,
            )
            conn.commit()

        return len(rows)

    def get_benchmark_last_date(self) -> str | None:
        """基准表中最新的日期，空表时为 None。供增量刷新判断从哪天接着算。"""
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT MAX(date) FROM market_benchmark").fetchone()
        return row[0] if row and row[0] else None

    def get_benchmark_series(self) -> list[tuple[str, float]]:
        """全部基准日收益，按日期升序的 (date, ret) 列表。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT date, ret FROM market_benchmark ORDER BY date"
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def get_benchmark_coverage(self) -> list[tuple[str, int]]:
        """每个基准日参与计算的股票数，按日期升序的 (date, symbol_count) 列表。

        用来识别本地回填不完整的时段：某几天只有几百只股票有行情，
        那几天的等权基准噪声极大，落在这段区间上的收益结论不可信。
        """
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT date, symbol_count FROM market_benchmark ORDER BY date"
            ).fetchall()
        return [(row[0], int(row[1])) for row in rows]

    def clear_returns(self, source: str) -> int:
        """
        删除某个来源的全部收益明细，用于口径变更后强制重算。

        只动 selection_return，选股结果表不受影响。

        Args:
            source: 'live' 或 'replay'。

        Returns:
            删除的记录条数。
        """
        with _connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM selection_return WHERE source = ?", (source,)
            )
            deleted = cursor.rowcount
            conn.commit()
        return max(deleted, 0)

    def get_pending_samples(
        self, source: str, horizons: Sequence[int]
    ) -> list[tuple[str, str, str]]:
        """
        取尚未算全收益的选股样本。

        "算全"指该 (run_date, strategy, symbol) 在 selection_return 里
        对所请求的每个持有期都已有记录。少一个就重算整条——重算是幂等的，
        为了省这点开销去做增量的持有期粒度追踪不划算。

        未到期样本会持续出现在返回值里，直到行情数据推进到它的卖出日为止。
        这正是期望行为：收益不是算不出来，是还没到期。

        Args:
            source: 'live' 或 'replay'。
            horizons: 关心的持有期列表。

        Returns:
            (run_date, strategy, symbol) 三元组列表，按日期、策略、rank 排序。

        Raises:
            ValueError: source 不合法或 horizons 为空时抛出。
        """
        table = _PICK_TABLES.get(source)
        if table is None:
            raise ValueError(f"未知的 source：{source}")
        if not horizons:
            raise ValueError("horizons 不能为空")

        # 占位符数量随 horizons 长度变化，拼的是 "?, ?, ?" 而非具体值，
        # 每个 horizon 仍然作为参数传入。
        holders = ", ".join("?" for _ in horizons)
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT p.run_date, p.strategy, p.symbol "
                f"FROM {table} p "
                "LEFT JOIN selection_return r "
                "  ON r.source = ? AND r.run_date = p.run_date "
                "  AND r.strategy = p.strategy AND r.symbol = p.symbol "
                f"  AND r.horizon IN ({holders}) "
                "GROUP BY p.run_date, p.strategy, p.symbol "
                "HAVING COUNT(r.id) < ? "
                "ORDER BY p.run_date, p.strategy, MIN(p.rank)",
                (source, *horizons, len(horizons)),
            ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def save_returns(self, rows: list[tuple]) -> int:
        """
        批量写入收益明细。

        按 (source, run_date, strategy, symbol, horizon) 冲突更新，
        因此重复计算同一批样本是幂等的。

        Args:
            rows: 与 selection_return 列顺序一致的元组列表，即
                (source, run_date, strategy, symbol, horizon, buy_date,
                 sell_date, buy_price, sell_price, ret, bench_ret,
                 excess_ret, tradable, computed_at)。

        Returns:
            写入的记录条数。
        """
        if not rows:
            return 0

        with _connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO selection_return ("
                "  source, run_date, strategy, symbol, horizon, buy_date, sell_date,"
                "  buy_price, sell_price, ret, bench_ret, excess_ret, tradable,"
                "  computed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, run_date, strategy, symbol, horizon) DO UPDATE SET "
                "  buy_date = excluded.buy_date, sell_date = excluded.sell_date, "
                "  buy_price = excluded.buy_price, sell_price = excluded.sell_price, "
                "  ret = excluded.ret, bench_ret = excluded.bench_ret, "
                "  excess_ret = excluded.excess_ret, tradable = excluded.tradable, "
                "  computed_at = excluded.computed_at",
                rows,
            )
            conn.commit()

        return len(rows)

    def get_returns(self, source: str) -> pd.DataFrame:
        """
        读出某个 source 的全部收益明细，供指标汇总使用。

        必须带 source 过滤：实盘与回放混在一起平均出来的数字没有任何意义。

        Args:
            source: 'live' 或 'replay'。

        Returns:
            含 strategy / horizon / ret / bench_ret / excess_ret / tradable
            等列的 DataFrame；无数据时为空 DataFrame。
        """
        with _connect(self.db_path) as conn:
            df = pd.read_sql(
                "SELECT run_date, strategy, symbol, horizon, buy_date, sell_date, "
                "       ret, bench_ret, excess_ret, tradable "
                "FROM selection_return WHERE source = ?",
                conn,
                params=(source,),
            )
        return df

    def get_return_coverage(self, source: str) -> dict:
        """
        收益明细的覆盖情况：样本数、涉及的策略数、日期区间。

        报表用它说明"这些数字是基于多少样本、哪段时间算出来的"。

        Args:
            source: 'live' 或 'replay'。

        Returns:
            含 rows / strategies / first_date / last_date 四个键的字典。
        """
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT strategy), MIN(run_date), MAX(run_date) "
                "FROM selection_return WHERE source = ?",
                (source,),
            ).fetchone()
        return {
            "rows": row[0] or 0,
            "strategies": row[1] or 0,
            "first_date": row[2],
            "last_date": row[3],
        }
