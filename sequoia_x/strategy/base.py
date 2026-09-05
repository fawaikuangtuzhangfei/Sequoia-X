"""策略基类模块：定义所有选股策略的抽象接口。"""

from abc import ABC, abstractmethod

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine


class BaseStrategy(ABC):
    """选股策略抽象基类。

    所有具体策略必须继承此类并实现 run() 方法。

    Attributes:
        webhook_key: 策略对应的飞书 webhook 标识，用于路由到不同机器人。
            默认为 'default'，将使用 Settings.feishu_webhook_url。
            子类可覆盖此属性以路由到专属机器人，例如 'ma_volume'。

    面向使用者的说明字段见下方。它们**故意和实现放在同一个文件里**：
    判据里写的阈值就是 run() 里用的阈值，改一个不改另一个会立刻显得刺眼。
    这些字段由 api 层读取后送到网页上，措辞面向使用者，不要写实现约定
    （"严禁 iterrows" 这类话属于 docstring，不属于这里）。

    tests/test_strategy_docs.py 会断言每个已注册策略都填了这些字段，
    漏填会让测试变红，而不是在页面上留一片空白。
    """

    webhook_key: str = "default"

    # ── 面向使用者的说明 ──
    title: str = ""
    """中文名，如 '海龟突破'。"""

    summary: str = ""
    """一句话说清它在找什么。"""

    criteria: tuple[str, ...] = ()
    """逐条判据，顺序与 run() 里的判断顺序一致。"""

    data_source: str = ""
    """数据来源，说清是本地行情还是外部接口。"""

    ordering: str = ""
    """结果顺序的依据，也就是 rank 的含义。未排序时如实写明。"""

    min_bars: str = ""
    """需要多少历史数据才会参与计算。"""

    caveat: str | None = None
    """使用时需要留神的地方，没有则为 None。"""

    def __init__(self, engine: DataEngine, settings: Settings) -> None:
        """
        初始化策略。

        Args:
            engine: DataEngine 实例，用于读取行情数据。
            settings: Settings 实例，用于读取配置。
        """
        self.engine = engine
        self.settings = settings

    @abstractmethod
    def run(self) -> list[str]:
        """
        执行选股逻辑，返回选中的股票代码列表。

        Returns:
            满足策略条件的股票代码列表，如 ['000001', '600519']。
            无选股结果时返回空列表。
        """
        ...
