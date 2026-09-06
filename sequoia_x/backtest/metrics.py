"""指标汇总：把收益明细压成每个 (策略, 持有期) 一行的统计量。

均值和中位数**都要给**。两者差距大说明结果被少数极端值主导，
这时平均收益是个不能拿来做决策的数字——一只涨停板能把二十条样本的均值拉红。
"""

import pandas as pd

from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

# 一买一卖的交易成本合计，用于给出扣费后的净超额。
#
# A 股单边大致是：佣金 万2.5~万3、过户费 万0.1、卖出印花税 千分之0.5，
# 再加上买卖价差造成的滑点。取来回 0.2% 是个偏保守的整数近似。
#
# 为什么必须显式扣：短线策略的超额本来就和这个数同一量级，正负常常就差在
# 这 0.2% 上。不给净值列的表会把不赚钱的策略显示成赚钱的。
ROUND_TRIP_COST: float = 0.002


def _describe(group: pd.DataFrame, cost: float = ROUND_TRIP_COST) -> dict:
    """算一组样本的指标。空组返回全 None，调用方负责过滤。

    扣费后的指标**逐条样本扣**再统计，不是在均值上减一刀：
    净胜率必须是"有多少条样本扣完费还是正的"，在均值上做减法算不出这个数。
    """
    if group.empty:
        return {
            "n": 0,
            "mean_ret": None,
            "median_ret": None,
            "win_rate": None,
            "mean_excess": None,
            "median_excess": None,
            "excess_win_rate": None,
            "mean_net_excess": None,
            "net_excess_win_rate": None,
        }
    net = group["excess_ret"] - cost
    return {
        "n": int(len(group)),
        "mean_ret": float(group["ret"].mean()),
        "median_ret": float(group["ret"].median()),
        "win_rate": float((group["ret"] > 0).mean()),
        "mean_excess": float(group["excess_ret"].mean()),
        "median_excess": float(group["excess_ret"].median()),
        "excess_win_rate": float((group["excess_ret"] > 0).mean()),
        "mean_net_excess": float(net.mean()),
        "net_excess_win_rate": float((net > 0).mean()),
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
