"""跟踪编排：刷新基准 → 取待算样本 → 算收益 → 落库 → 出报表。

compute_returns 刻意不查表（它只吃三元组），所以"从哪张表取样本、结果写回哪里"
这件事需要一个地方来串。串联逻辑放在这里而不是 main.py，是为了让实盘跟踪和
回放跟踪走同一段代码——两者只差一个 source 参数。
"""

from collections.abc import Sequence

from sequoia_x.backtest.analysis import (
    concentration,
    filter_since,
    rank_strata,
    recency,
    resonance,
    selectivity,
    selectivity_within_strategy,
)
from sequoia_x.backtest.benchmark import refresh_benchmark, warn_thin_coverage
from sequoia_x.backtest.metrics import overall, summarize
from sequoia_x.backtest.report import render_analysis, render_summary
from sequoia_x.backtest.returns import DEFAULT_HORIZONS, ComputeStats, compute_returns
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)


def track_returns(
    engine: DataEngine,
    source: str = "live",
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    recompute: bool = False,
) -> ComputeStats:
    """
    为某个来源的选股结果补算收益并写库。

    幂等：已经算过的 (样本, 持有期) 不会被重复取出；即便被取出，
    写入也是按唯一键更新，重复执行结果一致。

    Args:
        engine: 数据引擎。
        source: 'live' 取 selection_result，'replay' 取 selection_replay。
        horizons: 持有期列表，单位交易日。
        recompute: 先清空该来源的旧明细再重算。收益口径改动之后必须用它，
            否则旧数据会被"已经算过"的判断保护起来，永远不会被修正。

    Returns:
        本次计算的统计。
    """
    refresh_benchmark(engine)
    warn_thin_coverage(engine)

    if recompute:
        deleted = engine.clear_returns(source)
        logger.info(f"已清空旧收益明细 {deleted} 条（source={source}），将全量重算")

    pending = engine.get_pending_samples(source, horizons)
    logger.info(f"待算样本 {len(pending)} 条（source={source}）")

    rows, stats = compute_returns(engine, pending, horizons=horizons, source=source)
    if rows:
        engine.save_returns([tuple(row) for row in rows])
        logger.info(f"收益明细写入 {len(rows)} 条")

    return stats


def print_report(
    engine: DataEngine, source: str = "live", since: str | None = None
) -> None:
    """
    读出已落库的收益明细并打印汇总报表。

    Args:
        engine: 数据引擎。
        source: 'live' 或 'replay'。
        since: 只统计该日期及之后的推荐，'YYYY-MM-DD'。None 表示全部。
    """
    df = filter_since(engine.get_returns(source), since)
    coverage = engine.get_return_coverage(source)
    render_summary(summarize(df), overall(df), coverage, source)


def print_analysis(
    engine: DataEngine, source: str = "replay", since: str | None = None
) -> None:
    """
    读出收益明细并打印分层分析报表。

    与 print_report 分开：主报表回答"策略整体赚不赚钱"，
    分层分析回答"有没有哪一部分是赚钱的"。两者受众和读法都不同，
    塞进一张表只会让人两个都读不进去。

    Args:
        engine: 数据引擎。
        source: 'live' 或 'replay'。默认 replay——实盘样本量长期不足以分层。
        since: 只统计该日期及之后的推荐。想看"最近三个月"用这个，
            不必为一个时间窗重跑回放。
    """
    df = filter_since(engine.get_returns(source), since)
    render_analysis(
        rank_strata(df),
        resonance(df),
        selectivity(df),
        selectivity_within_strategy(df),
        # 分期对比刻意**不受 --since 限制**：它的全部价值就在于把最近和
        # 以往并排比较，砍掉历史等于砍掉对照组。
        recency(engine.get_returns(source)),
        concentration(df),
        since=since,
    )
