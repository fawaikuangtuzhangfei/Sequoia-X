"""指标汇总：把收益明细压成每个 (策略, 持有期) 一行的统计量。

均值和中位数**都要给**。两者差距大说明结果被少数极端值主导，
这时平均收益是个不能拿来做决策的数字——一只涨停板能把二十条样本的均值拉红。
"""

import pandas as pd

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


def _describe(group: pd.DataFrame) -> dict:
    """算一组样本的六个指标。空组返回全 None，调用方负责过滤。"""
    if group.empty:
        return {
            "n": 0,
            "mean_ret": None,
            "median_ret": None,
            "win_rate": None,
            "mean_excess": None,
            "median_excess": None,
            "excess_win_rate": None,
        }
    return {
        "n": int(len(group)),
        "mean_ret": float(group["ret"].mean()),
        "median_ret": float(group["ret"].median()),
        "win_rate": float((group["ret"] > 0).mean()),
        "mean_excess": float(group["excess_ret"].mean()),
        "median_excess": float(group["excess_ret"].median()),
        "excess_win_rate": float((group["excess_ret"] > 0).mean()),
    }


def summarize(df: pd.DataFrame) -> list[dict]:
    """
    按 (策略, 持有期) 汇总收益明细。

    每组算两遍：全样本，以及 tradable == 1 的可成交子集。两组并排展示，
    "买不进的票贡献了多少收益"就直接看得见。

    Args:
        df: 收益明细，需含 strategy / horizon / ret / excess_ret / tradable 列。
            通常来自 DataEngine.get_returns(source)。

    Returns:
        每个 (策略, 持有期) 一个字典，含 strategy、horizon、all_*、tradable_*
        三组键。按策略名、持有期升序排列。
    """
    if df.empty:
        return []

    results: list[dict] = []
    for (strategy, horizon), group in df.groupby(["strategy", "horizon"], sort=True):
        all_stats = _describe(group)
        tradable_stats = _describe(group[group["tradable"] == 1])
        row = {"strategy": str(strategy), "horizon": int(horizon)}
        row.update({f"all_{k}": v for k, v in all_stats.items()})
        row.update({f"tradable_{k}": v for k, v in tradable_stats.items()})
        results.append(row)

    results.sort(key=lambda r: (r["strategy"], r["horizon"]))
    return results


def overall(df: pd.DataFrame) -> list[dict]:
    """
    不分策略、只按持有期汇总，用来看"这套系统整体有没有超额"。

    Args:
        df: 与 summarize 相同的收益明细。

    Returns:
        每个持有期一个字典，结构与 summarize 的元素一致，strategy 为 '（全部）'。
    """
    if df.empty:
        return []

    results: list[dict] = []
    for horizon, group in df.groupby("horizon", sort=True):
        all_stats = _describe(group)
        tradable_stats = _describe(group[group["tradable"] == 1])
        row = {"strategy": "（全部）", "horizon": int(horizon)}
        row.update({f"all_{k}": v for k, v in all_stats.items()})
        row.update({f"tradable_{k}": v for k, v in tradable_stats.items()})
        results.append(row)

    return results
