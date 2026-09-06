"""历史回放：把策略放回过去的某一天重跑，产出可供收益跟踪的历史样本。

支点是一层只读的 as-of 包装。四个纯本地策略只通过 engine.get_local_symbols()
和 engine.get_ohlcv(symbol) 取数，并用 df.iloc[-1] 表示"今天"；把 get_ohlcv
的结果截断到 date <= as_of 之后，iloc[-1] 拿到的自然就是 as-of 当日的 bar，
**这四个策略的源文件一行都不用改**。

三个例外，见 REPLAYABLE_STRATEGIES 与 _ReplayTurtleTradeStrategy 的注释。
"""

import bisect

import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
from sequoia_x.strategy.registry import STRATEGIES
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy

logger = get_logger(__name__)


class _ReplayTurtleTradeStrategy(TurtleTradeStrategy):
    """回放专用的海龟策略：保留选股，跳过流通市值排序。

    原版排序要向 baostock 查决策日的不复权价与换手率。这是一次网络请求，
    而回放绝不该打网络——`AsOfEngine` 连 baostock 的入口都不暴露
    （属性 58），排序这一步是唯一还会绕过它的地方。

    覆盖 `_get_market_caps` 返回空字典后，`run()` 里的
    `candidates.sort(key=lambda s: market_caps.get(s, 0), reverse=True)`
    对所有元素取到同一个 key 0，而 Python 的 sort 是稳定的，
    因此候选顺序原样保留 —— 即选股顺序，而非市值顺序。

    **报表里必须标注**：该策略的回放结果不含市值排序，rank 的含义与实盘不同。

    **签名必须跟着父类走。** 父类调用的是 `_get_market_caps(candidates, as_of)`；
    少一个参数就是 TypeError，而 `replay_range` 会把它吞成一行警告，
    表现为海龟在整段回放里一只都选不出来。属性 57 守着这个。

    父类在市值全空时会记一条 WARNING 说"排序未生效"——在回放里那是**预期行为**，
    每个回放日各一条。不要去追它。

    这个类刻意放在 backtest 层而不是 strategy 包里：
    它不是一个可用于实盘的策略，不该出现在注册表和策略说明页上。
    """

    def _get_market_caps(self, symbols: list[str], as_of: str) -> dict[str, float]:
        """回放不查市值，返回空字典让排序退化为恒等变换。"""
        return {}


def _replayable_strategies() -> list[type[BaseStrategy]]:
    """从注册表派生出可回放的策略清单。

    不改 registry.py —— 那是日常执行清单，不该被回测需求污染。

    PrivatePlacementStrategy 被排除：它读的是定向增发公告接口，
    没有历史快照，任何"回放"出来的结果都只会是今天的公告，毫无意义。
    """
    result: list[type[BaseStrategy]] = []
    for cls in STRATEGIES:
        if cls is PrivatePlacementStrategy:
            continue
        if cls is TurtleTradeStrategy:
            result.append(_ReplayTurtleTradeStrategy)
            continue
        result.append(cls)
    return result


REPLAYABLE_STRATEGIES: list[type[BaseStrategy]] = _replayable_strategies()


class MarketCache:
    """全市场行情的一次性缓存，供回放反复按 as-of 切片。

    回放要跑几百个交易日，每天都从 SQLite 重读行情是不可接受的：
    RpsBreakout 每次 run() 都要全市场数据，650 天就是 650 次全表扫描。

    这里把全表读一次并预先分好组：
      - `_by_date` 按日期升序，全市场截断退化成一次连续切片；
      - `_by_symbol` 每只股票一份按日期升序的切片，供 get_ohlcv 用。
    两者都靠 date 是 'YYYY-MM-DD' 字符串、字典序即时间序这一点做二分。
    """

    def __init__(self, engine: DataEngine) -> None:
        """
        Args:
            engine: 数据引擎，只用于一次性读入全市场日线。
        """
        df = engine.get_market_ohlcv()

        # 按日期升序的整表：date <= as_of 是一段前缀，切片是 O(1)。
        by_date = df.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
        self._by_date: pd.DataFrame = by_date
        self._all_dates: list[str] = by_date["date"].tolist()

        # 每只股票一份按日期升序的切片。
        self._by_symbol: dict[str, pd.DataFrame] = {}
        self._symbol_dates: dict[str, list[str]] = {}
        for symbol, group in df.sort_values(["symbol", "date"], kind="stable").groupby(
            "symbol", sort=False
        ):
            g = group.reset_index(drop=True)
            self._by_symbol[symbol] = g
            self._symbol_dates[symbol] = g["date"].tolist()

        # 每个交易日当天**实际有行情**的股票。见 symbols_as_of 的注释。
        self._symbols_on_date: dict[str, list[str]] = {
            date: group["symbol"].tolist()
            for date, group in by_date.groupby("date", sort=False)
        }

        logger.info(
            f"行情缓存就绪：{len(self._by_symbol)} 只股票 / {len(by_date)} 根 K 线"
        )

    def trading_dates(self) -> list[str]:
        """缓存覆盖的全部交易日，升序。"""
        seen: dict[str, None] = dict.fromkeys(self._all_dates)
        return list(seen)

    def ohlcv_as_of(self, symbol: str, as_of: str) -> pd.DataFrame:
        """某只股票截至 as_of（含）的全部 bar。"""
        dates = self._symbol_dates.get(symbol)
        if dates is None:
            return pd.DataFrame()
        k = bisect.bisect_right(dates, as_of)
        if k == 0:
            return self._by_symbol[symbol].iloc[:0]
        return self._by_symbol[symbol].iloc[:k]

    def market_as_of(self, as_of: str) -> pd.DataFrame:
        """全市场截至 as_of（含）的全部 bar。"""
        k = bisect.bisect_right(self._all_dates, as_of)
        return self._by_date.iloc[:k]

    def symbols_as_of(self, as_of: str) -> list[str]:
        """**在 as_of 当天实际有行情**的股票代码。

        判据是"当天在交易"，不是"在此之前上市过"。五个策略用 `df.iloc[-1]`
        表示"今天"，一旦某只股票在 as_of 当天没有 bar，它们拿到的就是一根
        过期的 K 线，却会当作今日行情去判形态、去选股。

        用上市日判据（`first_date <= as_of`）时这个洞平时只在长期停牌股上
        偶尔漏水；补入退市股之后就是系统性的——一只 2026-01 退市的股票会在
        之后的每一个回放日都带着退市前的死 bar 进候选池。

        `RpsBreakout` 不受影响，它自己筛了 `date == latest_date`。
        """
        return self._symbols_on_date.get(as_of, [])


class AsOfEngine:
    """把 DataEngine 的读路径截断到某个历史日期。

    **只暴露读方法。** 写方法和网络同步方法一律不提供——回放绝不应该改动
    数据库或调用 baostock。策略拿到的是这个对象，任何越界访问都会
    直接 AttributeError，而不是悄悄读到未来数据或打出一次网络请求。
    """

    def __init__(self, cache: MarketCache, as_of: str, db_path: str) -> None:
        """
        Args:
            cache: 全市场行情缓存。
            as_of: 回放到的日期，'YYYY-MM-DD'。
            db_path: 数据库路径。仅为兼容仍读取该属性的调用方而透传，
                本类自身不用它开任何连接。
        """
        self._cache = cache
        self._as_of = as_of
        self.db_path = db_path

    @property
    def as_of(self) -> str:
        """当前回放到的日期。"""
        return self._as_of

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        """某只股票截至 as_of 的日线，列与 DataEngine.get_ohlcv 一致。"""
        return self._cache.ohlcv_as_of(symbol, self._as_of)

    def get_local_symbols(self) -> list[str]:
        """截至 as_of 已有行情的股票代码。"""
        return self._cache.symbols_as_of(self._as_of)

    def get_market_ohlcv(self, columns: tuple[str, ...] | None = None) -> pd.DataFrame:
        """全市场截至 as_of 的日线，供横截面策略使用。"""
        df = self._cache.market_as_of(self._as_of)
        return df if columns is None else df[list(columns)]


def replay_range(
    engine: DataEngine,
    settings: Settings,
    start: str | None = None,
    end: str | None = None,
) -> int:
    """
    在指定交易日区间上逐日回放所有可回放策略，结果写入 selection_replay。

    Args:
        engine: 数据引擎。
        settings: 配置，原样传给策略构造函数。
        start: 起始日期 'YYYY-MM-DD'，缺省为本地行情最早的交易日。
        end: 结束日期 'YYYY-MM-DD'，缺省为本地行情最新的交易日。

    Returns:
        写入 selection_replay 的记录总条数。
    """
    cache = MarketCache(engine)
    all_dates = cache.trading_dates()
    if not all_dates:
        logger.warning("本地无行情数据，回放无事可做")
        return 0

    lo = start or all_dates[0]
    hi = end or all_dates[-1]
    dates = [d for d in all_dates if lo <= d <= hi]
    if not dates:
        logger.warning(f"区间 {lo} .. {hi} 内没有交易日，回放无事可做")
        return 0

    skipped = [cls.__name__ for cls in STRATEGIES if cls not in REPLAYABLE_STRATEGIES]
    logger.info(
        f"回放区间 {dates[0]} .. {dates[-1]}，共 {len(dates)} 个交易日，"
        f"{len(REPLAYABLE_STRATEGIES)} 个策略"
    )
    logger.info(
        f"跳过 {PrivatePlacementStrategy.__name__}：依赖定向增发公告接口，"
        "无历史快照，回放不出有意义的结果"
    )
    logger.info(
        f"{TurtleTradeStrategy.__name__} 回放不含流通市值排序（需要当日实时数据），"
        "其 rank 为选股顺序"
    )
    logger.debug(f"注册表中未参与回放的类：{skipped}")

    total = 0
    for i, as_of in enumerate(dates, 1):
        as_of_engine = AsOfEngine(cache, as_of, engine.db_path)
        for cls in REPLAYABLE_STRATEGIES:
            # 回放里的策略名统一记成实盘的类名，否则收益报表会多出
            # 一个叫 _ReplayTurtleTradeStrategy 的陌生策略，和实盘对不上。
            name = (
                TurtleTradeStrategy.__name__
                if cls is _ReplayTurtleTradeStrategy
                else cls.__name__
            )
            try:
                strategy = cls(engine=as_of_engine, settings=settings)
                symbols = strategy.run()
                total += engine.save_replay(as_of, name, symbols)
            except Exception as exc:
                # 单个策略在单个日期上失败不该中断整段回放。
                logger.warning(f"[{as_of}] {name} 回放失败：{exc}")
                continue

        if i % 20 == 0 or i == len(dates):
            logger.info(f"回放进度 {i}/{len(dates)}（{as_of}），累计 {total} 条")

    return total
