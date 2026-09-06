"""分层分析的属性测试。

分层分析最容易出的错是"重不重、漏不漏"：一条样本被数两次，或者悄悄掉了一档，
输出仍然是一张像模像样的表。下面几条属性守的就是这个。
"""

import pandas as pd
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.backtest.analysis import (
    _MIN_BUCKET_N,
    rank_strata,
    resonance,
    selectivity,
    selectivity_within_strategy,
)


def make_returns(
    n_days: int = 40,
    n_symbols: int = 12,
    strategies: tuple[str, ...] = ("A", "B"),
    horizons: tuple[int, ...] = (1, 20),
) -> pd.DataFrame:
    """造一份形状正确的收益明细。数值本身不重要，分层逻辑才是被测对象。

    两个刻意的设计：
      - **每天选出的只数要变化**，否则按中位数切两半时一侧会是空的，
        策略内部对照直接跳过该策略，依赖它的属性就成了空断言；
      - **两个策略选的股票要重叠**，否则共振分层只有 k=1 一档。
    """
    rows = []
    for d in range(n_days):
        # 每天选 n_symbols-4 .. n_symbols 只，既有变化又保证名次能铺满各档
        k = n_symbols - (d % 5)
        for s_i, strategy in enumerate(strategies):
            picked = range(s_i * 2, k)
            for rank, sym_i in enumerate(picked):
                for horizon in horizons:
                    rows.append(
                        {
                            "run_date": f"2024-01-{d + 1:02d}",
                            "strategy": strategy,
                            "symbol": f"60{sym_i:04d}",
                            "horizon": horizon,
                            "excess_ret": (sym_i - n_symbols / 2) / 100.0,
                            "ret": (sym_i - n_symbols / 3) / 100.0,
                            "tradable": 1,
                            "rank": rank,
                        }
                    )
    return pd.DataFrame(rows)


# Feature: sequoia-x-v2, Property 59: 共振分层按股票去重，不按策略重复计数
def test_resonance_deduplicates_per_symbol() -> None:
    """属性 59：被 k 个策略同时选中的股票只计入一次，不是 k 次。

    不去重的话，共振组里每只股票被按策略数加权，算出来的"共振组表现"
    混着重复计数的偏差——而且表格看上去完全正常。
    """
    df = make_returns()
    rows = resonance(df, horizons=(1,))
    total = sum(r["n"] for r in rows)

    # 去重后的样本数应等于 (日期, 股票) 组合数，而不是明细行数
    expected = len(df[df["horizon"] == 1].groupby(["run_date", "symbol"]))
    raw = len(df[df["horizon"] == 1])

    assert total == expected
    assert raw > expected, "测试数据必须存在共振，否则这条属性是空断言"


# Feature: sequoia-x-v2, Property 60: rank 分层不重不漏
@given(n_symbols=st.integers(min_value=25, max_value=40))
@h_settings(max_examples=10, deadline=None)
def test_rank_buckets_partition_the_samples(n_symbols: int) -> None:
    """属性 60：rank 各档样本数之和等于总样本数。

    分档边界写错（比如 5-9 和 10-19 之间漏掉一个名次）不会报错，
    只会让某些样本从报表里静静消失。
    """
    df = make_returns(n_symbols=n_symbols, strategies=("A",))
    rows = rank_strata(df, horizons=(1,))

    total = sum(r["n"] for r in rows)
    expected = len(df[df["horizon"] == 1])

    assert total == expected


# Feature: sequoia-x-v2, Property 61: 策略内部对照的两侧合起来是全体
def test_within_strategy_split_covers_every_sample() -> None:
    """属性 61：少的日子 + 多的日子 = 该策略在该持有期上的全部样本。

    按中位数切两半，用 <= 和 > 恰好互补。若两边都用 <=（或都用 >），
    中位数那一档会被算两次或漏掉，而 spread 仍然算得出来。
    """
    df = make_returns()
    rows = selectivity_within_strategy(df, horizon=1)
    assert rows, "测试数据应至少产出一个策略的对照"

    for row in rows:
        total = len(
            df[(df["strategy"] == row["strategy"]) & (df["horizon"] == 1)]
        )
        assert row["few_n"] + row["many_n"] == total


# Feature: sequoia-x-v2, Property 62: 样本不足的档不出现在结果里
def test_undersized_buckets_are_omitted() -> None:
    """属性 62：任何一档的样本数都不低于 _MIN_BUCKET_N，不够的档整档不出现。

    拿十几个样本的均值当结论比没有结论更危险——它看起来和别的档一样可信。
    """
    # 只有 12 只股票，名次最大到 11，"第 21 名以后"那一档必然是空的。
    df = make_returns(n_days=40, n_symbols=12)

    for rows in (
        rank_strata(df),
        resonance(df),
        selectivity(df),
        selectivity_within_strategy(df),
    ):
        for row in rows:
            n = row["n"] if "n" in row else min(row["few_n"], row["many_n"])
            assert n >= _MIN_BUCKET_N

    # 空档必须整档消失，而不是以 n=0 的形式留在表里
    buckets = {row["bucket"] for row in rank_strata(df)}
    assert "第 21 名以后" not in buckets
    assert "前 5 名" in buckets, "有数据的档不该被一起滤掉"


# Feature: sequoia-x-v2, Property 63: 分层分析只统计买得进的样本
def test_analysis_ignores_untradable_samples() -> None:
    """属性 63：tradable = 0 的样本不进入任何一档。

    主报表同时给全样本和可成交两组；分层分析只做可成交那一组，
    因为分层的目的就是找可执行的子集。
    """
    df = make_returns()
    df.loc[df["symbol"] == "600000", "tradable"] = 0
    assert (df["tradable"] == 0).any(), "测试数据必须含有不可成交样本"

    # 直接断言"带着不可成交样本"与"事先剔除它们"两种输入产出完全相同的结果。
    # 比"各档之和等于可成交总数"更强，也不受某档样本不足被丢弃的干扰。
    kept_only = df[df["tradable"] == 1].copy()

    assert rank_strata(df) == rank_strata(kept_only)
    assert resonance(df) == resonance(kept_only)
    assert selectivity(df) == selectivity(kept_only)
    assert selectivity_within_strategy(df) == selectivity_within_strategy(kept_only)
