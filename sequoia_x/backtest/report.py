"""报表输出：把汇总指标渲染成终端表格，并固定附上口径与偏差声明。

偏差声明是**写死的**，没有开关。这些话正是几个月后最容易忘、
忘了最容易照着虚高的胜率去下单的部分——能关掉的警告等于不存在的警告。
"""

from rich.console import Console
from rich.table import Table

# 报表固定按这个宽度渲染。让 rich 自适应终端宽度的话，
# 窄终端下这张表会被截成一排 "…"，还不如换行。
_CONSOLE_WIDTH: int = 110

# 已知会让数字偏乐观的因素。每次打报表都原样输出。
_CAVEATS: tuple[str, ...] = (
    "幸存者偏差：本地池只含当前仍在市的股票，退市股不在其中。"
    "基准取自同一批股票，两边偏差大部分相抵，但不会完全抵消。",
    "价格为后复权序列，含未来的复权因子。",
    "不计手续费、印花税与滑点。按单边约 0.1% 折算，持有期越短影响越大。",
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

    console.print("\n[bold]以下因素会让上表数字偏乐观[/bold]")
    for i, caveat in enumerate(_CAVEATS, 1):
        console.print(f"  {i}. {caveat}", highlight=False)
    console.print()
