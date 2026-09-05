"""基准模块：由本地日线合成全市场等权日收益，作为超额收益的参照。

为什么用全市场等权而不是沪深300：选股池和基准池是同一批股票，两边扛着
同样的幸存者偏差，相减之后偏差大部分抵消。沪深300 需要额外拉指数数据，
且成分股与本地池不重合，超额收益反而更难解释。

代价是这个基准不是可交易的标的，也没有"开盘价"这个概念——
这一点直接决定了 BenchmarkIndex.compound 的区间约定。
"""

from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)

# 单日涨跌幅绝对值超过这个数的记录会被剔除后再取均值。
# 后复权序列在复权因子异常时会冒出几百倍的跳变，一条就能把当天基准带跑偏。
_OUTLIER_ABS_RET: float = 0.5


def refresh_benchmark(engine: DataEngine) -> int:
    """
    重算并写入缺失日期的全市场等权日收益。

    Args:
        engine: 数据引擎。

    Returns:
        本次新写入的日期条数。已经算过的日期不会重写。
    """
    rows, raw_count = engine.compute_market_benchmark(_OUTLIER_ABS_RET)

    if not rows:
        logger.warning("基准计算无结果：stock_daily 可能为空或只有一个交易日")
        return 0

    kept = sum(row[2] for row in rows)
    dropped = raw_count - kept
    if dropped > 0:
        logger.info(
            f"基准计算剔除异常日收益 {dropped} 条"
            f"（|涨跌幅| >= {_OUTLIER_ABS_RET:.0%}），保留 {kept} 条"
        )

    existing = {date for date, _ in engine.get_benchmark_series()}
    new_rows = [row for row in rows if row[0] not in existing]

    written = engine.save_benchmark(new_rows)
    logger.info(
        f"基准刷新完成：新增 {written} 个交易日，全表覆盖 {rows[0][0]} .. {rows[-1][0]}"
    )
    return written


# 某日参与基准计算的股票数低于全期中位数的这个比例时，判定该日覆盖不足。
_THIN_COVERAGE_RATIO: float = 0.5


def thin_coverage_days(engine: DataEngine) -> list[tuple[str, int]]:
    """
    找出本地行情覆盖明显不足的交易日。

    这些日子的等权基准是在极小的横截面上取的均值，噪声大；同样重要的是，
    策略在这些日子上跑出来的选股结果也建立在残缺的股票池上。
    两者都会让落在这段区间的结论失真，必须让使用者看见，而不是悄悄算完给个数。

    Args:
        engine: 数据引擎。

    Returns:
        (date, symbol_count) 列表，按日期升序。没有异常时为空列表。
    """
    coverage = engine.get_benchmark_coverage()
    if not coverage:
        return []

    counts = sorted(count for _, count in coverage)
    median = counts[len(counts) // 2]
    floor = median * _THIN_COVERAGE_RATIO
    return [(date, count) for date, count in coverage if count < floor]


def warn_thin_coverage(engine: DataEngine) -> list[tuple[str, int]]:
    """检查覆盖度并在发现问题时打 WARNING 日志。返回异常日列表。"""
    thin = thin_coverage_days(engine)
    if not thin:
        return []

    logger.warning(
        f"本地行情有 {len(thin)} 个交易日覆盖明显不足"
        f"（{thin[0][0]} .. {thin[-1][0]}，最少的一天只有 {min(c for _, c in thin)} 只股票）。"
        "这些日子的基准与选股结果都建立在残缺的股票池上，结论不可尽信；"
        "跑一次 `python main.py --backfill` 补齐后重算会更可靠。"
    )
    return thin


class BenchmarkIndex:
    """基准日收益的累计因子索引，用于取任意日期区间的复合收益。

    把日收益预先累乘成因子序列，区间收益就退化成两次查表加一次除法，
    避免为每个样本重新连乘一遍。回放场景下样本数以万计，这个差别很实在。
    """

    def __init__(self, series: list[tuple[str, float]]) -> None:
        """
        Args:
            series: 按日期升序的 (date, ret) 列表，通常来自
                DataEngine.get_benchmark_series()。
        """
        self._index: dict[str, int] = {}
        # _cum[i] 是截至第 i 个交易日（含）的累计因子；
        # _cum_before[i] 是它前一日的累计因子，首日为 1.0。
        self._cum: list[float] = []
        self._cum_before: list[float] = []

        factor = 1.0
        for i, (date, ret) in enumerate(series):
            self._index[date] = i
            self._cum_before.append(factor)
            factor *= 1.0 + ret
            self._cum.append(factor)

    def __len__(self) -> int:
        return len(self._cum)

    def compound(self, start_date: str, end_date: str) -> float | None:
        """
        取 [start_date, end_date] **闭区间**的基准复合收益。

        闭区间意味着 start_date 当天的日收益（前收盘 → 当日收盘）也被算进来，
        而个股那一侧是当日开盘买入。两者在买入日差着一个隔夜跳空——
        这是本口径已知的不对称，因为等权合成的基准没有"开盘价"。

        选闭区间而非开区间是刻意偏保守：基准多吃一天市场收益，
        算出来的超额只会更难看，不会虚高。反过来（基准跳过买入日、
        个股却吃到买入日的日内涨幅）会系统性地把超额做高，
        而策略挑的恰恰是容易高开高走的票。

        Args:
            start_date: 区间起始日（含），通常是买入日。
            end_date: 区间结束日（含），通常是卖出日。

        Returns:
            区间复合收益；任一端不在基准索引内时返回 None。
        """
        i = self._index.get(start_date)
        j = self._index.get(end_date)
        if i is None or j is None or j < i:
            return None
        return self._cum[j] / self._cum_before[i] - 1.0
