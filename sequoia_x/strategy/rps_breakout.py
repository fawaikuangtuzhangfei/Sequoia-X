import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class RpsBreakoutStrategy(BaseStrategy):
    """RPS 极强动量突破策略"""

    webhook_key: str = "rps"
    rps_period: int = 120
    rps_threshold: int = 90

    title = "RPS 强度突破"
    summary = "120 日涨幅排进全市场前 10%，且股价仍贴着区间高点。"
    criteria = (
        "计算每只股票最近 120 个交易日的涨幅",
        "横向排名，取 RPS ≥ 90（即涨幅前 10%）",
        "今日收盘价 ≥ 该股 120 日最高价 × 0.9",
    )
    data_source = "本地日线（一次读入全表做横向排名）"
    ordering = "未排序，按横向排名后的表顺序"
    min_bars = "120 个交易日（滚动最高价 60 根起算）"
    caveat = (
        "RPS 是相对排名，「前 10%」只相对于本地已回填的股票池。"
        "池子越小，排名越不可信——回填不完整时这个策略的结果参考价值有限。"
    )

    def run(self) -> list[str]:
        # 走 engine 的公开读方法而不是自己开一条 sqlite 连接：
        # 裸连接绕过了数据层，历史回放时无法把可见数据截断到 as-of 日期，
        # 策略会读到未来的行情。engine 侧的边界违规记录见 data spec。
        try:
            df = self.engine.get_market_ohlcv(
                columns=("symbol", "date", "close", "high")
            )
        except Exception as exc:
            logger.error(f"读取数据库失败: {exc}")
            return []

        if df.empty:
            return []

        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values(['symbol', 'date'])

        # 纵向计算涨幅
        df['close_shift'] = df.groupby('symbol')['close'].shift(self.rps_period)
        df['pct_change'] = (df['close'] - df['close_shift']) / df['close_shift']

        latest_date = df['date'].max()
        latest_df = df[df['date'] == latest_date].copy()
        latest_df = latest_df.dropna(subset=['pct_change'])

        # 横向排位 (RPS)
        latest_df['rps'] = latest_df['pct_change'].rank(pct=True) * 100
        strong_stocks = latest_df[latest_df['rps'] >= self.rps_threshold].copy()

        # 计算滚动最高价
        roll_high = df.groupby('symbol')['high'].rolling(
            window=self.rps_period, min_periods=self.rps_period // 2
        ).max().reset_index(level=0, drop=True)
        df['roll_high'] = roll_high

        latest_roll_high = df[df['date'] == latest_date][['symbol', 'roll_high']]
        strong_stocks = strong_stocks.merge(latest_roll_high, on='symbol')

        # 突破判定
        breakout_condition = strong_stocks['close'] >= strong_stocks['roll_high'] * 0.90
        selected = strong_stocks[breakout_condition]

        logger.info(f"RpsBreakoutStrategy 选出 {len(selected)} 只股票")
        return selected['symbol'].tolist()