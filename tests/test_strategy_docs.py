"""策略注册表与说明字段的完整性测试。

这两条属性是把说明搬到策略类上之后的守卫：说明只有在"不填就变红"的前提下
才不会烂掉。
"""

import importlib
import inspect
import pkgutil

import pytest

import sequoia_x.strategy as strategy_pkg
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.registry import STRATEGIES

_DOC_FIELDS = ("title", "summary", "data_source", "ordering", "min_bars")


def _all_strategy_classes() -> list[type[BaseStrategy]]:
    """扫描 sequoia_x.strategy 包，找出所有 BaseStrategy 的具体子类。"""
    found: list[type[BaseStrategy]] = []
    for info in pkgutil.iter_modules(strategy_pkg.__path__):
        module = importlib.import_module(f"{strategy_pkg.__name__}.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseStrategy)
                and obj is not BaseStrategy
                and not inspect.isabstract(obj)
                # 只认在本模块里定义的类，避免 import 进来的重复计数
                and obj.__module__ == module.__name__
            ):
                found.append(obj)
    return found


# Feature: sequoia-x-v2, Property 28: 每个已注册策略都填了说明
@pytest.mark.parametrize("cls", STRATEGIES, ids=lambda c: c.__name__)
def test_every_registered_strategy_is_documented(cls: type[BaseStrategy]) -> None:
    """属性 28：注册表里的每个策略都必须填齐说明字段，且至少一条判据。

    说明会直接渲染到网页上。漏填不会报错，只会在页面上留一片空白——
    这个测试把"沉默的空白"变成"红色的失败"。
    """
    for field in _DOC_FIELDS:
        value = getattr(cls, field)
        assert isinstance(value, str) and value.strip(), f"{cls.__name__}.{field} 为空"

    assert isinstance(cls.criteria, tuple), f"{cls.__name__}.criteria 应为 tuple"
    assert len(cls.criteria) >= 1, f"{cls.__name__}.criteria 至少要有一条"
    assert all(isinstance(c, str) and c.strip() for c in cls.criteria)

    assert cls.caveat is None or (isinstance(cls.caveat, str) and cls.caveat.strip())


# Feature: sequoia-x-v2, Property 29: 新增的策略文件不会漏进注册表
def test_registry_covers_every_strategy_class() -> None:
    """属性 29：strategy 包里每个具体策略类都必须出现在 STRATEGIES 中。

    这正是注册表要消灭的老问题：新写一个策略文件却忘了登记，
    main.py 不会报错，策略只是静悄悄地永远不跑。
    """
    discovered = set(_all_strategy_classes())
    registered = set(STRATEGIES)

    missing = sorted(c.__name__ for c in discovered - registered)
    assert not missing, f"这些策略没有登记到 registry.STRATEGIES：{missing}"

    stale = sorted(c.__name__ for c in registered - discovered)
    assert not stale, f"注册表里有找不到定义的策略：{stale}"


def test_registry_has_no_duplicates() -> None:
    """同一个策略登记两次会让它每天跑两遍、推两条。"""
    names = [cls.__name__ for cls in STRATEGIES]
    assert len(names) == len(set(names)), f"注册表有重复项：{names}"
