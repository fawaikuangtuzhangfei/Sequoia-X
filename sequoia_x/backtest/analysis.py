"""分层分析：在已算好的收益明细上切几刀，看有没有比"全买"更好的子集。

主报表回答"策略整体赚不赚钱"，这里回答"有没有哪一部分是赚钱的"。
三个切法各有各的用途，其中第一个是**对照组**，不是结论——
读输出前先看下面每个函数的 docstring。

所有函数都是纯函数，输入是 DataEngine.get_returns() 的 DataFrame，
不碰数据库。这样加一个新切法不需要动数据层。
"""

import pandas as pd

from sequoia_x.backtest.metrics import ROUND_TRIP_COST
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)


def filter_since(df: pd.DataFrame, since: str | None) -> pd.DataFrame:
    """只保留 run_date >= since 的样本。since 为 None 时原样返回。

    date 是 'YYYY-MM-DD' 字符串，字典序即时间序，直接比字符串即可。

    想看"最近三个月"不需要重跑回放——样本已经在库里，切片是零成本的。
    """
    if not since:
        return df
    return df[df["run_date"] >= since]

# rank 分层的分档边界，左闭右闭，单位是 0 起的名次。
_RANK_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0, 4, "前 5 名"),
    (5, 9, "第 6-10"),
    (10, 19, "第 11-20"),
    (20, 10**9, "第 21 名以后"),
)

# 选择性分层：策略当天选出多少只。
_COUNT_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (1, 3, "1-3 只"),
    (4, 10, "4-10 只"),
    (11, 30, "11-30 只"),
    (31, 10**9, "31 只以上"),
)

# 一档少于这么多样本就不报，避免拿几十个样本的均值当结论。
_MIN_BUCKET_N: int = 30


def _stat(group: pd.DataFrame, label: str) -> dict | None:
    """算一档的统计量。样本不足时返回 None。"""
    if len(group) < _MIN_BUCKET_N:
        return None
    return {
        "bucket": label,
        "n": int(len(group)),
        "mean_excess": float(group["excess_ret"].mean()),
        "excess_win_rate": float((group["excess_ret"] > 0).mean()),
        "mean_ret": float(group["ret"].mean()),
    }


def _tradable_only(df: pd.DataFrame) -> pd.DataFrame:
    """只保留买入日买得进的样本。分层分析一律在这个子集上做。"""
    return df[df["tradable"] == 1]


def rank_strata(df: pd.DataFrame, horizons: tuple[int, ...] = (1, 20)) -> list[dict]:
    """
    按选股时的 rank 分层——**这是对照组，不是结论**。

    回放数据里 rank 对所有策略都不携带信息：六个可回放策略中五个自己声明
    "未排序，按股票代码的遍历顺序"，而 TurtleTrade 唯一真实的市值排序在回放中
    被禁用（需要当日实时换手率）。所以"前 5 名"取到的只是代码序号最小的 5 只。

    正因如此它是个好用的健全性检查：**这里不该出现跨持有期一致的效应**。
    如果出现了，先怀疑收益计算漏了未来数据，而不是庆祝发现了信号。

    实测确实无信号：T+1 看似前段略优，但 T+20 符号反转，说明测到的是板块
    （rank ≈ 代码 ≈ 000/300/600/688）而非选股质量。

    Args:
        df: DataEngine.get_returns() 的输出，需含 rank 列。
        horizons: 要看的持有期。

    Returns:
        每档一个字典，含 horizon / bucket / n / mean_excess 等键。
    """
    t = _tradable_only(df).dropna(subset=["rank"])
    rows: list[dict] = []
    for horizon in horizons:
        sub = t[t["horizon"] == horizon]
        for lo, hi, label in _RANK_BUCKETS:
            stat = _stat(sub[(sub["rank"] >= lo) & (sub["rank"] <= hi)], label)
            if stat:
                rows.append({"horizon": int(horizon), **stat})
    return rows


def resonance(df: pd.DataFrame, horizons: tuple[int, ...] = (1, 3, 5, 20)) -> list[dict]:
    """
    多策略共振：同一天被 k 个策略同时选中的股票，表现如何。

    先按 (日期, 股票, 持有期) 去重再统计。不去重的话，被 3 个策略选中的股票
    会以 3 条收益完全相同的记录进入均值，等于给它三倍权重——
    那样算出来的"共振组表现"里混着重复计数造成的偏差。

    实测结论与直觉相反：**共振越强表现越差，且三个持有期上单调一致**。
    这直接影响前端的「多策略共振」功能。

    Args:
        df: DataEngine.get_returns() 的输出。
        horizons: 要看的持有期。

    Returns:
        每 (持有期, k) 一个字典。
    """
    t = _tradable_only(df)
    grouped = (
        t.groupby(["run_date", "symbol", "horizon"])
        .agg(
            k=("strategy", "nunique"),
            excess_ret=("excess_ret", "first"),
            ret=("ret", "first"),
        )
        .reset_index()
    )

    rows: list[dict] = []
    for horizon in horizons:
        sub = grouped[grouped["horizon"] == horizon]
        for k in sorted(sub["k"].unique()):
            stat = _stat(sub[sub["k"] == k], f"{int(k)} 个策略")
            if stat:
                rows.append({"horizon": int(horizon), "k": int(k), **stat})
    return rows


def recency(df: pd.DataFrame, horizon: int = 1) -> list[dict]:
    """
    按季度拆开看每个策略的净超额，回答"这个策略最近还灵吗"。

    比单独回放最近三个月强，原因有两个：
      - 样本已经在库里，切片是零成本的，不必为一个时间窗重跑几十分钟；
      - **有对照**。孤立地看最近三个月，没法判断 -0.3% 是策略失效了，
        还是它一贯如此。并排放着才看得出趋势。

    用净超额（已扣交易成本）而不是毛超额：短线判断"还灵不灵"，
    毛超额为正但扣费为负的季度算不上"灵"。

    Args:
        df: DataEngine.get_returns() 的输出。
        horizon: 在哪个持有期上看，默认 T+1。

    Returns:
        每 (策略, 季度) 一个字典，按策略、季度升序。样本不足的季度会被略过。
    """
    t = _tradable_only(df)
    t = t[t["horizon"] == horizon].copy()
    if t.empty:
        return []

    # run_date 是 'YYYY-MM-DD' 字符串，直接切片取年月即可，不必转 datetime。
    quarter = t["run_date"].str[:4] + "Q" + (
        (t["run_date"].str[5:7].astype(int) - 1) // 3 + 1
    ).astype(str)
    t["quarter"] = quarter

    rows: list[dict] = []
    for (strategy, q), group in t.groupby(["strategy", "quarter"], sort=True):
        if len(group) < _MIN_BUCKET_N:
            continue
        net = group["excess_ret"] - ROUND_TRIP_COST
        rows.append(
            {
                "strategy": str(strategy),
                "quarter": str(q),
                "horizon": int(horizon),
                "n": int(len(group)),
                "mean_excess": float(group["excess_ret"].mean()),
                "mean_net_excess": float(net.mean()),
                "net_win_rate": float((net > 0).mean()),
            }
        )
    return rows


def concentration(df: pd.DataFrame, horizon: int = 1) -> list[dict]:
    """
    超额收益集中在多少只票上——判断"能不能用少量仓位执行"的关键。

    一个平均超额为正、但一半以上持仓在亏、且超额的一半来自最好的 1% 的策略，
    实际上不可执行：你得几乎全买才能抓到那几张彩票，漏掉几只就由正转负。
    反过来，中位数为正、去掉最好的 1% 之后均值几乎不变的策略，
    边际是广泛分布的，拿几个仓位也能吃到。

    这两种情况在"平均超额"那一列上长得一模一样，这也是为什么要单独看这个。

    Args:
        df: DataEngine.get_returns() 的输出。
        horizon: 在哪个持有期上看。

    Returns:
        每个策略一个字典，含亏损占比、中位超额、前 1% 的贡献占比、
        以及剔除前 1% 之后的均值。按"剔除后均值"降序。
    """
    t = _tradable_only(df)
    t = t[t["horizon"] == horizon]

    rows: list[dict] = []
    for strategy, group in t.groupby("strategy", sort=False):
        if len(group) < _MIN_BUCKET_N:
            continue
        values = group["excess_ret"].sort_values(ascending=False).to_numpy()
        n = len(values)
        total = values.sum()
        top_n = max(1, n // 100)
        # 总超额接近 0 时占比会炸成天文数字，这种情况下这个比值没有意义
        top_share = float(values[:top_n].sum() / total) if abs(total) > 1e-9 else None
        rows.append(
            {
                "strategy": str(strategy),
                "horizon": int(horizon),
                "n": n,
                "loss_rate": float((values < 0).mean()),
                "median_excess": float(pd.Series(values).median()),
                "mean_excess": float(values.mean()),
                "top1pct_share": top_share,
                "mean_ex_top1pct": float(values[top_n:].mean()) if n > top_n else None,
            }
        )

    rows.sort(
        key=lambda r: (r["mean_ex_top1pct"] is None, -(r["mean_ex_top1pct"] or 0.0))
    )
    return rows


def _picks_per_day(df: pd.DataFrame) -> pd.DataFrame:
    """给每条样本标上"该策略当天一共选了多少只"。

    用单一持有期计数，避免同一只票在 4 个持有期上被数 4 遍。
    """
    horizon = int(df["horizon"].min())
    counts = (
        df[df["horizon"] == horizon]
        .groupby(["run_date", "strategy"])
        .size()
        .rename("picks")
        .reset_index()
    )
    return df.merge(counts, on=["run_date", "strategy"], how="inner")


def selectivity(df: pd.DataFrame, horizons: tuple[int, ...] = (1, 20)) -> list[dict]:
    """
    选择性：策略当天选出的股票越少，选得越准吗。

    跨策略看会有混淆——本来就选得少的策略可能碰巧更好。真正决定性的是
    `selectivity_within_strategy`，那个在每个策略内部比较。

    Args:
        df: DataEngine.get_returns() 的输出。
        horizons: 要看的持有期。

    Returns:
        每 (持有期, 档) 一个字典。
    """
    t = _picks_per_day(_tradable_only(df))
    rows: list[dict] = []
    for horizon in horizons:
        sub = t[t["horizon"] == horizon]
        for lo, hi, label in _COUNT_BUCKETS:
            stat = _stat(sub[(sub["picks"] >= lo) & (sub["picks"] <= hi)], label)
            if stat:
                rows.append({"horizon": int(horizon), **stat})
    return rows


def selectivity_within_strategy(df: pd.DataFrame, horizon: int = 1) -> list[dict]:
    """
    策略内部的选择性对照：按该策略自己的当日选股数中位数切两半。

    这是判断"选得少更准"是真信号还是混淆的**决定性检验**。跨策略分层里，
    选得少的那档可能只是由那些本来就精挑细选的策略构成；在策略内部比较，
    这个混淆就消失了。

    实测四个大样本策略全部为正（少的日子更好），为负的两个样本量只有几百。
    解读是信号数量像市场情绪的代理：几百只同时触发突破的日子是亢奋日，
    买在了顶上。

    Args:
        df: DataEngine.get_returns() 的输出。
        horizon: 在哪个持有期上比较。

    Returns:
        每个策略一个字典，含 median_picks / few_* / many_* / spread。
        当日选股数没有变化（中位数两侧分不开）的策略会被跳过。
    """
    t = _picks_per_day(_tradable_only(df))
    t = t[t["horizon"] == horizon]

    rows: list[dict] = []
    for strategy, group in t.groupby("strategy", sort=True):
        median = float(group["picks"].median())
        few = group[group["picks"] <= median]
        many = group[group["picks"] > median]
        if len(few) < _MIN_BUCKET_N or len(many) < _MIN_BUCKET_N:
            logger.debug(f"{strategy} 当日选股数无足够变化，跳过策略内部对照")
            continue
        few_excess = float(few["excess_ret"].mean())
        many_excess = float(many["excess_ret"].mean())
        rows.append(
            {
                "strategy": str(strategy),
                "horizon": int(horizon),
                "median_picks": median,
                "few_n": int(len(few)),
                "few_excess": few_excess,
                "many_n": int(len(many)),
                "many_excess": many_excess,
                "spread": few_excess - many_excess,
            }
        )

    rows.sort(key=lambda r: r["spread"], reverse=True)
    return rows
