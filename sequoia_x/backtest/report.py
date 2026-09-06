"""报表输出：把汇总指标渲染成终端表格，并固定附上口径与偏差声明。

偏差声明是**写死的**，没有开关。这些话正是几个月后最容易忘、
忘了最容易照着虚高的胜率去下单的部分——能关掉的警告等于不存在的警告。
"""

from rich.console import Console
from rich.table import Table

from sequoia_x.backtest.metrics import ROUND_TRIP_COST

# 报表固定按这个宽度渲染。让 rich 自适应终端宽度的话，
# 窄终端下这张表会被截成一排 "…"，还不如换行。
_CONSOLE_WIDTH: int = 132

# 已知会让数字偏乐观的因素。每次打报表都原样输出。
_CAVEATS: tuple[str, ...] = (
    "幸存者偏差：本地池只含当前仍在市的股票，退市股不在其中。"
    "基准取自同一批股票，两边偏差大部分相抵，但不会完全抵消。",
    "价格为后复权序列，含未来的复权因子。",
    "「扣费后」两列按一买一卖合计 0.2% 折算（佣金+过户费+印花税+滑点的保守估计）。"
    "不含冲击成本；单笔金额大时实际更差。",
    "本地池并非全市场，横截面排名类策略（RPS）的分位数只相对本地池成立。",
    "买入日个股按开盘价成交，基准是前收盘到收盘的全天涨幅，两者差一个隔夜跳空。"
    "该不对称让超额收益偏保守，不会虚高。",
    "海龟策略的回放结果不含流通市值排序（需要当日实时数据），其 rank 为选股顺序。",
)


def _short_name(strategy: str) -> str:
    """去掉类名末尾的 Strategy，让策略列窄一半。"""
    return strategy[: -len("Strategy")] if strategy.endswith("Strategy") else strategy


def _pct(value: float | None) -> str:
    """把小数渲染成带符号的百分数；None 渲染为占位符。"""
    return "—" if value is None else f"{value * 100:+.2f}%"


def _rate(value: float | None) -> str:
    """把比率渲染成百分数（不带符号）；None 渲染为占位符。"""
    return "—" if value is None else f"{value * 100:.1f}%"


def _build_table(title: str, rows: list[dict], sections: list[dict], prefix: str) -> Table:
    """按 prefix（all_ 或 tradable_）取一组指标建表。"""
    table = Table(title=title, header_style="bold", title_style="bold", title_justify="left")
    table.add_column("策略", no_wrap=True)
    table.add_column("持有", justify="right")
    table.add_column("样本", justify="right")
    table.add_column("平均收益", justify="right")
    table.add_column("中位收益", justify="right")
    table.add_column("胜率", justify="right")
    table.add_column("平均超额", justify="right")
    table.add_column("超额胜率", justify="right")
    table.add_column("扣费后超额", justify="right", style="cyan")
    table.add_column("扣费后胜率", justify="right", style="cyan")

    def add(row: dict) -> None:
        table.add_row(
            _short_name(row["strategy"]),
            str(row["horizon"]),
            str(row[f"{prefix}n"]),
            _pct(row[f"{prefix}mean_ret"]),
            _pct(row[f"{prefix}median_ret"]),
            _rate(row[f"{prefix}win_rate"]),
            _pct(row[f"{prefix}mean_excess"]),
            _rate(row[f"{prefix}excess_win_rate"]),
            _pct(row[f"{prefix}mean_net_excess"]),
            _rate(row[f"{prefix}net_excess_win_rate"]),
        )

    for row in rows:
        add(row)
    if sections:
        table.add_section()
        for row in sections:
            add(row)
    return table


def render_summary(
    summary_rows: list[dict],
    overall_rows: list[dict],
    coverage: dict,
    source: str,
    console: Console | None = None,
) -> None:
    """
    打印收益汇总报表。

    分成两张表而不是一张宽表：全样本与可成交并排会有 11 列，
    在任何正常宽度的终端里都会被截断，读不出数字就等于没有报表。

    Args:
        summary_rows: metrics.summarize 的输出。
        overall_rows: metrics.overall 的输出。
        coverage: DataEngine.get_return_coverage 的输出。
        source: 'live' 或 'replay'，写进标题避免两份报表被看混。
        console: rich Console，缺省新建一个。测试可注入以捕获输出。
    """
    console = console or Console(width=_CONSOLE_WIDTH)
    label = "实盘推荐" if source == "live" else "历史回放"

    if not summary_rows:
        console.print(
            f"\n[yellow]暂无成熟样本（source={source} / {label}）。[/yellow]\n"
            "推荐日之后还没有足够的行情数据，收益无法计算。\n"
            "这不是错误：等行情推进到卖出日之后再跑一次，结果就会出现。\n"
        )
        return

    console.print(
        f"\n[bold]选股收益汇总 · {label}[/bold]  "
        f"{coverage.get('first_date')} .. {coverage.get('last_date')}，"
        f"共 {coverage.get('rows', 0)} 条明细"
    )

    console.print()
    console.print(_build_table("全样本", summary_rows, overall_rows, "all_"))
    console.print()
    console.print(
        _build_table(
            "可成交样本（剔除买入日一字涨停，做决策请以这张为准）",
            summary_rows,
            overall_rows,
            "tradable_",
        )
    )

    console.print("\n[bold]口径[/bold]")
    console.print("  推荐日次日（T+1）开盘买入，持有 N 个交易日后收盘卖出，等权单票。")
    console.print("  基准为全市场等权日收益；超额 = 个股收益 − 同期基准复合收益。")
    console.print(
        f"  扣费后 = 每条样本的超额各减 {ROUND_TRIP_COST:.1%} 再统计"
        "（逐条扣，不是在均值上减一刀，否则胜率会算错）。"
    )

    console.print("\n[bold]以下因素会让上表数字偏乐观[/bold]")
    for i, caveat in enumerate(_CAVEATS, 1):
        console.print(f"  {i}. {caveat}", highlight=False)
    console.print()


def _strata_table(title: str, rows: list[dict], first_col: str) -> Table:
    """把一组分层统计渲染成表格。"""
    table = Table(title=title, header_style="bold", title_style="bold", title_justify="left")
    table.add_column("持有", justify="right")
    table.add_column(first_col, no_wrap=True)
    table.add_column("样本", justify="right")
    table.add_column("平均超额", justify="right")
    table.add_column("超额胜率", justify="right")
    table.add_column("平均收益", justify="right")

    last_horizon = None
    for row in rows:
        if last_horizon is not None and row["horizon"] != last_horizon:
            table.add_section()
        last_horizon = row["horizon"]
        table.add_row(
            f"T+{row['horizon']}",
            row["bucket"],
            str(row["n"]),
            _pct(row["mean_excess"]),
            _rate(row["excess_win_rate"]),
            _pct(row["mean_ret"]),
        )
    return table


def render_analysis(
    rank_rows: list[dict],
    resonance_rows: list[dict],
    selectivity_rows: list[dict],
    within_rows: list[dict],
    console: Console | None = None,
) -> None:
    """
    打印分层分析报表。

    每张表上面都写清楚它是什么、该怎么读——尤其第一张是**对照组**。
    一张没有解读的分层表最容易被当成"发现了信号"。

    Args:
        rank_rows: analysis.rank_strata 的输出。
        resonance_rows: analysis.resonance 的输出。
        selectivity_rows: analysis.selectivity 的输出。
        within_rows: analysis.selectivity_within_strategy 的输出。
        console: rich Console，缺省新建一个。
    """
    console = console or Console(width=_CONSOLE_WIDTH)

    if not any([rank_rows, resonance_rows, selectivity_rows, within_rows]):
        console.print(
            "\n[yellow]没有足够的收益明细可供分层分析。[/yellow]\n"
            "先跑 `--replay` 产出样本，再跑 `--track-returns --source replay`。\n"
        )
        return

    console.print("\n[bold]分层分析[/bold]  在已算好的收益明细上切几刀，"
                  "看有没有比「全买」更好的子集\n")

    if rank_rows:
        console.print(_strata_table("① 按 rank 分层【对照组，预期无信号】", rank_rows, "名次"))
        console.print(
            "  回放里 rank 不携带信息：五个策略自称「未排序，按代码遍历顺序」，"
            "海龟的市值\n  排序在回放中被禁用。这里**不该**出现跨持有期一致的效应；"
            "若出现，先怀疑\n  收益计算漏了未来数据。实测 T+1 与 T+20 符号相反，"
            "测到的是板块而非选股质量。\n"
        )

    if resonance_rows:
        table = _strata_table("② 多策略共振", resonance_rows, "同时选中")
        console.print(table)
        console.print(
            "  [bold]结论与直觉相反：共振越强表现越差，且各持有期单调一致。[/bold]\n"
            "  前端的「多策略共振」功能据此需要修正措辞——它目前把人往更差的票上引。\n"
        )

    if selectivity_rows:
        console.print(_strata_table("③ 选择性：策略当天选出多少只", selectivity_rows, "当天选出"))
        console.print("  跨策略比较会有混淆，决定性的是下面这张策略内部对照。\n")

    if within_rows:
        table = Table(
            title="④ 策略内部对照：按各自当日选股数的中位数切两半（决定性检验）",
            header_style="bold",
            title_style="bold",
            title_justify="left",
        )
        table.add_column("策略", no_wrap=True)
        table.add_column("中位", justify="right")
        table.add_column("少的日子", justify="right")
        table.add_column("样本", justify="right")
        table.add_column("多的日子", justify="right")
        table.add_column("样本", justify="right")
        table.add_column("差", justify="right")
        for row in within_rows:
            table.add_row(
                _short_name(row["strategy"]),
                f"{row['median_picks']:.0f}",
                _pct(row["few_excess"]),
                str(row["few_n"]),
                _pct(row["many_excess"]),
                str(row["many_n"]),
                f"{row['spread'] * 100:+.2f}pp",
            )
        console.print(table)
        console.print(
            "  差为正 = 选得少的日子表现更好。信号数量像是市场情绪的代理：\n"
            "  几百只同时触发突破的日子是亢奋日，买在了顶上。\n"
            "  [bold]但它救不了策略[/bold]——最好那档的超额也在单边 0.1% 的交易成本量级内，\n"
            "  且 T+20 上效应消失。它的价值是解释亏损来自哪里，不是给出能赚钱的规则。\n"
        )
