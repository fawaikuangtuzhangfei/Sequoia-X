"""策略注册表：全项目唯一的"有哪些策略"清单。

新增策略只需在 STRATEGIES 里加一行，main.py 会执行它，网页的策略说明也会
自动出现。在此之前 main.py 里硬编码着这份清单，漏加不会有任何报错——
策略就是静悄悄地不跑（见 spec/project/architecture.md 的新增策略清单）。

顺序即 main.py 的执行顺序。
"""

from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy

STRATEGIES: tuple[type[BaseStrategy], ...] = (
    MaVolumeStrategy,
    TurtleTradeStrategy,
    HighTightFlagStrategy,
    LimitUpShakeoutStrategy,
    UptrendLimitDownStrategy,
    RpsBreakoutStrategy,
    PrivatePlacementStrategy,
)
