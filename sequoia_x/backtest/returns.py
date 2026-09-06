"""收益计算：把选股样本换算成 T+N 收益、基准收益与超额收益。

口径（改动前务必读完，这里每一条都能单独把结论带偏）：

- 买入：推荐日之后**下一个全市场交易日**的开盘价。该股当天没有行情
  （停牌、或本地回填有空洞）就判定这个信号执行不了，整条样本丢弃——
  详见 _MAX_HOLD_SLIP 上方那段注释，这是最容易被写成"顺延到能买为止"的地方。
- 卖出：自买入日起持有 N 个交易日，第 N 日的收盘价。N=1 即当日开盘买、收盘卖。
- 价格用后复权序列，已含分红送转。
- 未到期样本（卖出日行情还不存在）**直接跳过**，不落库、不记 0。

本模块不关心样本从哪来。实盘推荐和历史回放都被调用方读成同样的三元组再喂进来，
这是两条路径能共用一套计算的原因——也是它不该去查选股表的原因。
"""

import bisect
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import NamedTuple

from sequoia_x.backtest.benchmark import BenchmarkIndex
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine

logger = get_logger(__name__)

# 默认持有期，单位是交易日。
#
# 2 和 3 是后加的，不是凑数：实测六个策略**全部**在 T+1 超额最高、之后单调
# 恶化，而符号翻转就发生在 T+1 到 T+5 之间。只有 1 和 5 两个点，看不出是第 2 天
# 就转负还是撑到第 4 天——而这恰恰是短线持有最该看清的地方。
#
# 加档的代价只是多算几行收益，不需要重跑回放；样本本身与持有期无关。
DEFAULT_HORIZONS: tuple[int, ...] = (1, 2, 3, 5, 10, 20)

# 涨停阈值。判定的是"买入日开盘即涨停"，也就是根本买不进的情形。
#
# 三处都是近似，方向上一律偏保守（宁可把能买的判成买不进）：
#   1. 用后复权价算涨跌幅，除权日会有误差——涨停板按除权后的前收盘价算；
#   2. ST 判定依赖 stock_basic 里的**当前**名称，不是推荐日当时的名称；
#   3. 只看开盘是否涨停，不管"开盘没涨停但盘中封死"。
# 反向的错误（把买不进的当成买得进）才是危险的，那会让收益虚高。
_LIMIT_MAIN_BOARD: float = 0.098
_LIMIT_GROWTH_BOARD: float = 0.196  # 创业板 300/301、科创板 688，涨跌幅 20%
_LIMIT_ST: float = 0.048
_GROWTH_PREFIXES: tuple[str, ...] = ("300", "301", "688")

# 持有期内允许该股相对全市场交易日多停牌的天数上限。
#
# 为什么需要这两道闸：把"买入日"定义成"推荐日之后第一个该股有行情的交易日"
# 听上去合理，实际会让一只停牌两个月的股票在信号发出两个月后成交。
# 实测中 605566 在 2026-07-01 起连续七天被选中，买入日全部落在 2026-09-03——
# 那已经不是这个策略的交易了，而且那段时间的收益会被算作策略的功劳。
#
# 买入侧的规则最严：必须能在推荐日的下一个全市场交易日买到，否则整条丢弃。
# 卖出侧宽松一些——持仓中途停牌是躲不掉的，只能持有到复牌——但超过这个
# 松弛量就说明"持有 N 天"已经名不副实，同样丢弃。
_MAX_HOLD_SLIP: int = 10


class ReturnRow(NamedTuple):
    """一条收益明细。字段顺序与 selection_return 的列顺序一致，可直接入库。"""

    source: str
    run_date: str
    strategy: str
    symbol: str
    horizon: int
    buy_date: str
    sell_date: str
    buy_price: float
    sell_price: float
    ret: float
    bench_ret: float
    excess_ret: float
    tradable: int
    computed_at: str


class ComputeStats(NamedTuple):
    """一次计算的产出统计，用于让跳过的样本可见而不是悄悄消失。"""

    computed: int
    """算出收益的明细条数。"""

    immature: int
    """因未到期被跳过的 (样本, 持有期) 组合数。"""

    no_bars: int
    """因推荐日之后没有任何行情而被跳过的样本数。"""

    no_benchmark: int
    """因买卖日落在基准覆盖范围外而被跳过的组合数。"""

    unbuyable: int
    """因推荐日次个交易日该股停牌、信号无法执行而被丢弃的样本数。"""

    hold_slipped: int
    """因持有期内停牌过久、"持有 N 天"名不副实而被丢弃的组合数。"""


def _limit_threshold(symbol: str, st_symbols: set[str]) -> float:
    """按板块和 ST 状态返回涨停幅度阈值。"""
    if symbol in st_symbols:
        return _LIMIT_ST
    if symbol.startswith(_GROWTH_PREFIXES):
        return _LIMIT_GROWTH_BOARD
    return _LIMIT_MAIN_BOARD


def compute_returns(
    engine: DataEngine,
    samples: Iterable[tuple[str, str, str]],
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    source: str = "live",
) -> tuple[list[ReturnRow], ComputeStats]:
    """
    计算一批选股样本的收益明细。

    样本按 symbol 归组后逐只读一次行情，而不是每条样本读一次——
    回放场景下样本数以万计，但涉及的股票最多两千只。

    单只股票计算失败只记 WARNING 并跳过，不中断整批（project spec 第一条铁律）。

    Args:
        engine: 数据引擎。
        samples: (run_date, strategy, symbol) 三元组，顺序无关。
        horizons: 持有期列表，单位交易日。
        source: 'live' 或 'replay'，写进每条明细用于区分实盘与回放。

    Returns:
        (收益明细列表, 统计)。未到期的组合不会出现在明细里。
    """
    horizons = tuple(sorted(set(int(h) for h in horizons)))
    if not horizons or any(h < 1 for h in horizons):
        raise ValueError(f"horizons 必须是正整数且非空：{horizons}")

    by_symbol: dict[str, list[tuple[str, str]]] = {}
    for run_date, strategy, symbol in samples:
        by_symbol.setdefault(symbol, []).append((run_date, strategy))

    if not by_symbol:
        return [], ComputeStats(0, 0, 0, 0, 0, 0)

    bench = BenchmarkIndex(engine.get_benchmark_series())
    st_symbols = engine.get_st_symbols()
    computed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 全市场交易日历。判断"该股是否在推荐日的下一个交易日正常交易"必须拿它做参照：
    # 只看这只股票自己的 bar 序列，是分不出"停牌"和"那天全市场都休市"的。
    market_dates: list[str] = engine.get_trading_dates()
    market_rank: dict[str, int] = {d: i for i, d in enumerate(market_dates)}

    rows: list[ReturnRow] = []
    immature = no_bars = no_benchmark = unbuyable = hold_slipped = 0

    for symbol, entries in by_symbol.items():
        try:
            df = engine.get_ohlcv(symbol)
            if df.empty:
                no_bars += len(entries)
                continue

            # 转成 Python list 后按下标取值。这不是 iterrows：每条样本只需要
            # 三个位置上的标量，转成列表后是 O(1) 索引，比反复切片 DataFrame 快。
            dates: list[str] = df["date"].tolist()
            opens: list[float] = df["open"].tolist()
            closes: list[float] = df["close"].tolist()
            n_bars = len(dates)
            threshold = _limit_threshold(symbol, st_symbols)

            for run_date, strategy in entries:
                # 第一个 date > run_date 的下标。**必须是严格大于**：
                # 策略是收盘后跑的，用 >= 等于拿当天收盘信息买当天，是未来函数。
                buy_idx = bisect.bisect_right(dates, run_date)

                # buy_idx == 0 意味着推荐日早于该股所有行情，既算不出前收盘价、
                # 也判不了涨停，样本本身就可疑，整条跳过。
                if buy_idx == 0 or buy_idx >= n_bars:
                    no_bars += 1
                    continue

                # 信号必须能在推荐日的下一个全市场交易日执行。该股那天没有
                # 行情，说明停牌（或本地数据有洞），这笔交易根本下不出去；
                # 顺延到复牌再买是另一笔交易，不能算进这个策略的账上。
                market_idx = bisect.bisect_right(market_dates, run_date)
                if market_idx >= len(market_dates):
                    no_bars += 1
                    continue
                if dates[buy_idx] != market_dates[market_idx]:
                    unbuyable += 1
                    continue

                buy_price = opens[buy_idx]
                prev_close = closes[buy_idx - 1]
                if not buy_price or buy_price <= 0 or not prev_close or prev_close <= 0:
                    no_bars += 1
                    continue

                open_gain = buy_price / prev_close - 1.0
                tradable = 0 if open_gain >= threshold else 1
                buy_date = dates[buy_idx]

                for horizon in horizons:
                    sell_idx = buy_idx + horizon - 1
                    # 未到期：卖出日的行情还不存在。跳过，不落库。
                    # 若这里改成"取最后一根 bar"，所有未到期样本都会被记成一段
                    # 不足 N 天的收益，统计量被系统性稀释向 0。
                    if sell_idx >= n_bars:
                        immature += 1
                        continue

                    sell_date = dates[sell_idx]
                    sell_price = closes[sell_idx]
                    if not sell_price or sell_price <= 0:
                        immature += 1
                        continue

                    # 持仓中途停牌躲不掉，但跨度拖得太长时"持有 N 个交易日"
                    # 就名不副实了——收益里混进的是停牌期间的市场变化。
                    span = market_rank.get(sell_date, 0) - market_rank.get(buy_date, 0)
                    if span > horizon - 1 + _MAX_HOLD_SLIP:
                        hold_slipped += 1
                        continue

                    bench_ret = bench.compound(buy_date, sell_date)
                    if bench_ret is None:
                        no_benchmark += 1
                        continue

                    ret = sell_price / buy_price - 1.0
                    rows.append(
                        ReturnRow(
                            source=source,
                            run_date=run_date,
                            strategy=strategy,
                            symbol=symbol,
                            horizon=horizon,
                            buy_date=buy_date,
                            sell_date=sell_date,
                            buy_price=float(buy_price),
                            sell_price=float(sell_price),
                            ret=float(ret),
                            bench_ret=float(bench_ret),
                            excess_ret=float(ret - bench_ret),
                            tradable=tradable,
                            computed_at=computed_at,
                        )
                    )

        except Exception as exc:
            logger.warning(f"[{symbol}] 收益计算失败：{exc}")
            continue

    stats = ComputeStats(
        computed=len(rows),
        immature=immature,
        no_bars=no_bars,
        no_benchmark=no_benchmark,
        unbuyable=unbuyable,
        hold_slipped=hold_slipped,
    )
    logger.info(
        f"收益计算完成（source={source}）：产出 {stats.computed} 条；"
        f"跳过——未到期 {stats.immature}、次日停牌买不到 {stats.unbuyable}、"
        f"持仓期停牌过久 {stats.hold_slipped}、无行情 {stats.no_bars}、"
        f"无基准 {stats.no_benchmark}"
    )
    return rows, stats
