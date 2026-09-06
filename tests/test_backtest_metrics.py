"""指标汇总的属性测试。

重点守"扣费后"那两列。交易成本对短线策略是决定性的——实测 T+1 有三个策略
超额为正，扣费之后只剩一个。算错了不会报错，只会把不赚钱的策略显示成赚钱的。
"""

import pandas as pd

from sequoia_x.backtest.metrics import ROUND_TRIP_COST, overall, summarize


def make_returns(excess_values: list[float]) -> pd.DataFrame:
    """按给定的超额收益序列造一份最小可用的收益明细。"""
    return pd.DataFrame(
        [
            {
                "run_date": f"2024-01-{i + 1:02d}",
                "strategy": "S",
                "symbol": f"60000{i % 10}",
                "horizon": 1,
                "ret": ex,
                "excess_ret": ex,
                "tradable": 1,
            }
            for i, ex in enumerate(excess_values)
        ]
    )


# Feature: sequoia-x-v2, Property 64: 扣费后胜率逐条样本计算
def test_net_win_rate_is_computed_per_sample() -> None:
    """属性 64：扣费后胜率是"多少条样本扣完费仍为正"，不能由均值推出。

    在均值上减一刀也能得到正确的扣费后**均值**，但胜率必须逐条判断。
    一组毛超额全在 0 与成本之间的样本，毛胜率是 100%、净胜率应该是 0%——
    这个差别在均值口径下完全看不出来。
    """
    # 全部为正但都小于交易成本：毛胜率 100%，净胜率必须是 0%
    values = [ROUND_TRIP_COST * f for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
    rows = summarize(make_returns(values))

    assert len(rows) == 1
    row = rows[0]
    assert row["all_excess_win_rate"] == 1.0
    assert row["all_net_excess_win_rate"] == 0.0


# Feature: sequoia-x-v2, Property 65: 扣费后均值等于毛均值减去成本
def test_net_mean_equals_gross_mean_minus_cost() -> None:
    """属性 65：扣费后平均超额 == 平均超额 − 成本，对任意样本成立。

    与属性 64 成对：64 管胜率不能走均值捷径，65 管均值本身别扣错次数
    （比如买卖各扣一次成本，等于扣了两倍）。
    """
    values = [-0.05, -0.01, 0.0, 0.003, 0.02, 0.11]
    for rows in (summarize(make_returns(values)), overall(make_returns(values))):
        for row in rows:
            expected = row["all_mean_excess"] - ROUND_TRIP_COST
            assert abs(row["all_mean_net_excess"] - expected) < 1e-12
