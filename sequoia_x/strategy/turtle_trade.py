"""海龟交易策略：20日新高突破 + 成交额过亿 + 动量阳线过滤。"""

import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class TurtleTradeStrategy(BaseStrategy):
    """海龟交易策略（A股防诱多改良版）。

    选股条件（向量化，严禁 iterrows）：
    1. 突破新高：今日 close > 前20个交易日 high 的最大值
    2. 流动性：今日 turnover > 100,000,000
    3. 防诱多过滤：今日必须是实体阳线（今日 close > 今日 open），且必须真涨（今日 close > 昨日 close）

    Attributes:
        webhook_key: 路由到 'turtle' 专属飞书机器人。
    """

    webhook_key: str = "turtle"
    _MIN_BARS: int = 21  # 至少需要 21 根 K 线（20日窗口 + 当日）

    title = "海龟突破"
    summary = "创 20 日新高，同时要求成交额过亿、当天是实体阳线。"
    criteria = (
        "今日收盘价 > 前 20 个交易日的最高价",
        "今日成交额 > 1 亿元",
        "今日收盘价 > 今日开盘价（实体阳线）",
        "今日收盘价 > 昨日收盘价（排除高开低走的假阳线）",
    )
    data_source = "本地日线；排序时另向 baostock 查基准日的不复权价与换手率"
    ordering = (
        "按流通市值从大到小。流通市值 = 成交量 ÷ 换手率 × 不复权收盘价，"
        "取自最后一根 K 线当天（即做出判断的那一天），不是运行当天"
    )
    min_bars = "21 个交易日"

    # 向前多查几天，容错"本地最后一根 K 线在 baostock 侧不是交易日"的边角情况。
    # 查询次数不变，只是把单点查询换成小区间后取最后一行。
    _CAP_LOOKBACK_DAYS: int = 7
    # 市值覆盖率低于此比例时告警：排序已经部分失真
    _CAP_COVERAGE_FLOOR: float = 0.8

    def _get_market_caps(self, symbols: list[str], as_of: str) -> dict[str, float]:
        """查询候选股票在 `as_of` 当天的流通市值（不复权收盘价 × 流通股本）。

        流通股本 = 成交量 / (换手率% / 100)
        流通市值 = 流通股本 × 不复权收盘价

        **`as_of` 必须是做出选股判断的那根 K 线的日期，不能用 `date.today()`。**
        原先用今天：周末、节假日、盘后数据未发布时 baostock 返回空，
        `market_caps` 全空，`sort` 稳定排序原样不动——排序静默失效，
        而日志一个字都不说。实测 2026-09-06（周日）跑出来的海龟名单
        恰好是代码升序，就是这个 bug 的现场。

        用同一根 K 线的日期还有个附带好处：不再依赖机器时钟，
        回测重放时天然取到 as-of 日而不是墙上时间。

        Args:
            symbols: 候选股票代码。
            as_of: 基准日 `YYYY-MM-DD`，即 `run()` 里 `df.iloc[-1]` 的日期。

        Returns:
            `{代码: 流通市值}`。查不到的代码不会出现在结果里。
        """
        from datetime import date, timedelta

        import baostock as bs

        end_str = as_of
        lookback = timedelta(days=self._CAP_LOOKBACK_DAYS)
        start_str = (date.fromisoformat(as_of) - lookback).isoformat()
        market_caps: dict[str, float] = {}

        lg = bs.login()
        if lg.error_code != "0":
            logger.error(
                f"baostock 登录失败（{lg.error_code}: {lg.error_msg}），"
                "取不到流通市值，本次排序将退化为代码序"
            )
            return {}

        try:
            for symbol in symbols:
                bs_code = self.engine._to_baostock_code(symbol)
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "close,volume,turn",
                    start_date=start_str,
                    end_date=end_str,
                    frequency="d",
                    adjustflag="3",  # 不复权，真实价格
                )
                if rs.error_code != "0":
                    continue
                # 区间可能返回多行，只要最后一行（离 as_of 最近的交易日）
                last_row = None
                while rs.next():
                    last_row = rs.get_row_data()
                if last_row is None:
                    continue
                try:
                    close = float(last_row[0])
                    volume = float(last_row[1])
                    turn = float(last_row[2])
                    if turn > 0:
                        circulating_shares = volume / (turn / 100)
                        market_caps[symbol] = circulating_shares * close
                except (ValueError, ZeroDivisionError):
                    continue
        finally:
            bs.logout()

        return market_caps

    def run(self) -> list[str]:
        """
        遍历全市场，返回满足海龟突破条件的股票代码列表。
        """
        symbols = self.engine.get_local_symbols()
        candidates: list[str] = []
        # 各候选最后一根 K 线的日期，取最大值作为全市场的基准日
        candidate_dates: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:
                    continue

                # 向量化：前20日 high 的滚动最大值（不含当日，shift(1) 后取 rolling(20)）
                df["high_20"] = df["high"].shift(1).rolling(20).max()

                last = df.iloc[-1]
                prev = df.iloc[-2]  # 获取昨日数据，用于对比

                if pd.isna(last["high_20"]):
                    continue

                # 核心条件 1：突破前 20 天最高点
                breakout = last["close"] > last["high_20"]
                # 核心条件 2：流动性过亿
                liquid = last["turnover"] > 100_000_000

                # 【新增防守条件】拒绝郑州煤电式的高开低走大阴线！
                is_yang = last["close"] > last["open"]   # 实体必须是阳线（红柱）
                is_up = last["close"] > prev["close"]    # 必须是真涨，不能是假阳线

                if breakout and liquid and is_yang and is_up:
                    candidates.append(symbol)
                    candidate_dates.append(str(last["date"]))

            except Exception as exc:
                logger.warning(f"[{symbol}] TurtleTradeStrategy 计算失败：{exc}")
                continue

        # 按流通市值从大到小排序
        if candidates:
            as_of = max(candidate_dates)
            market_caps = self._get_market_caps(candidates, as_of)

            # 排序失效必须说出来。市值全空时 .get(s, 0) 对所有股票返回 0，
            # sort 是稳定排序，结果原样不动——看起来像"排过了"，其实没有。
            if not market_caps:
                logger.warning(
                    f"as_of={as_of} 一条流通市值都没取到，"
                    f"「按流通市值排序」未生效，{len(candidates)} 只按原始顺序返回"
                )
            elif len(market_caps) < len(candidates) * self._CAP_COVERAGE_FLOOR:
                logger.warning(
                    f"as_of={as_of} 流通市值仅覆盖 {len(market_caps)}/{len(candidates)} 只，"
                    "排序部分失真：缺失的按 0 处理，会被排到末尾"
                )

            candidates.sort(key=lambda s: market_caps.get(s, 0), reverse=True)

        logger.info(f"TurtleTradeStrategy 选出 {len(candidates)} 只股票")
        return candidates