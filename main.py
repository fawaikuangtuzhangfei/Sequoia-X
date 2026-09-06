"""Sequoia-X V2 主程序入口。

两种运行模式：
  python main.py               # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送（2~3分钟）
  python main.py --backfill    # 回填模式：baostock 拉全市场历史K线（首次/补数据用，约12分钟）
"""

import argparse
import sys
from dotenv import load_dotenv
load_dotenv()

from datetime import date

import socket
socket.setdefaulttimeout(10.0)

from sequoia_x.core.config import get_settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.notify.feishu import FeishuNotifier
from sequoia_x.strategy.base import BaseStrategy
from sequoia_x.strategy.registry import STRATEGIES


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X V2 选股系统")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="回填模式：通过 baostock 拉取全市场历史 K 线（约12分钟）",
    )
    parser.add_argument(
        "--backfill-delisted",
        action="store_true",
        help="回填已退市股票的历史 K 线，消除回测的幸存者偏差（约1分钟）",
    )
    parser.add_argument(
        "--refresh-names",
        action="store_true",
        help="仅刷新股票名称表（stock_basic），约几秒，供 Web 页面展示名称",
    )
    parser.add_argument(
        "--track-returns",
        action="store_true",
        help="为已有选股结果补算 T+N 收益与超额，并打印汇总报表",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="历史回放：按 as-of 日期重跑策略，产出历史选股样本",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        default=None,
        help="回放起始日期 YYYY-MM-DD，缺省为本地行情最早的交易日",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        default=None,
        help="回放结束日期 YYYY-MM-DD，缺省为本地行情最新的交易日",
    )
    parser.add_argument(
        "--source",
        choices=["live", "replay"],
        default="live",
        help="--track-returns 的样本来源：live=实盘推荐，replay=历史回放",
    )
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="配合 --track-returns：清空该来源的旧收益明细后全量重算",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="报表只统计该日期及之后的推荐 YYYY-MM-DD，如 --since 2026-06-01",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="在已算好的收益明细上做分层分析（rank 对照、多策略共振、选择性）",
    )
    args = parser.parse_args()

    try:
        # 1. 初始化配置
        settings = get_settings()

        # 2. 初始化日志
        logger = get_logger(__name__)
        logger.info("Sequoia-X V2 启动")

        # 3. 初始化数据引擎
        engine = DataEngine(settings)

        if args.backfill:
            # ── 回填模式：单线程保守拉历史 K 线，自动多轮重跑 ──
            logger.info("进入回填模式...")
            all_symbols = engine.get_all_symbols()
            engine.backfill(all_symbols)
            logger.info("Sequoia-X V2 回填模式运行完成")
            return

        if args.backfill_delisted:
            # ── 退市股回填：只为回测服务，日常选股用不到 ──
            # 单独一个入口而不是并进 --backfill：退市股名单要另发一次
            # baostock 查询，且这批数据补完就基本不变，没必要每次回填都拉。
            #
            # 补进来的股票不会污染日常推荐：候选池按"当日有行情"筛选
            # （get_local_symbols），退市股的最后一根 bar 停在退市日，
            # 自然就落选了。
            logger.info("进入退市股回填模式...")
            delisted = engine.get_delisted_symbols()
            if not delisted:
                logger.warning("未获取到退市股名单，回填无事可做")
                return
            engine.backfill(delisted)
            logger.info(f"Sequoia-X V2 退市股回填完成，覆盖 {len(delisted)} 只")
            return

        if args.refresh_names:
            # ── 仅刷新名称表 ──
            # get_all_symbols() 会顺手把 (代码, 名称) 写入 stock_basic。
            # 名称平时只在 --backfill 时被动填充，只跑日常模式的用户
            # 需要这个独立入口，否则 Web 页面上永远显示不出股票名称。
            logger.info("刷新股票名称表...")
            symbols = engine.get_all_symbols()
            logger.info(f"Sequoia-X V2 名称刷新完成，覆盖 {len(symbols)} 只股票")
            return

        if args.replay:
            # ── 回放模式：按历史日期重跑策略，产出可供收益跟踪的历史样本 ──
            # 结果写 selection_replay，绝不碰 selection_result——
            # 那张表是实盘推荐的事实记录。
            from sequoia_x.backtest.replay import replay_range

            logger.info("进入历史回放模式...")
            picks = replay_range(
                engine, settings, start=args.date_from, end=args.date_to
            )
            logger.info(f"Sequoia-X V2 回放完成，共写入 {picks} 条历史选股样本")
            logger.info("接着跑 `python main.py --track-returns --source replay` 出收益")
            return

        if args.analyze:
            # ── 分层分析模式：只读已落库的收益明细，不重算 ──
            from sequoia_x.backtest.tracker import print_analysis

            logger.info(f"分层分析（source={args.source}）...")
            print_analysis(engine, source=args.source, since=args.since)
            return

        if args.track_returns:
            # ── 收益跟踪模式：补算收益并打印报表 ──
            from sequoia_x.backtest.tracker import print_report, track_returns

            logger.info(f"开始补算收益（source={args.source}）...")
            stats = track_returns(
                engine, source=args.source, recompute=args.recompute
            )
            logger.info(
                f"收益补算完成：新增 {stats.computed} 条明细，"
                f"未到期 {stats.immature} 条"
            )
            print_report(engine, source=args.source, since=args.since)
            return

        # ── 日常模式：单次 API 补今天 + 策略 + 推送 ──
        logger.info("开始拉取最新快照...")
        count = engine.sync_today_bulk()
        logger.info(f"快照同步完成，写入 {count} 只股票")

        # 4. 策略列表。清单在 sequoia_x/strategy/registry.py，
        #    新增策略只需在那里加一行，Web 的策略说明也会一并出现。
        strategies: list[BaseStrategy] = [
            cls(engine=engine, settings=settings) for cls in STRATEGIES
        ]

        notifier = FeishuNotifier(settings, engine)
        today_str = date.today().strftime("%Y-%m-%d")

        # 5. 遍历策略，落库并推送至对应机器人
        for strategy in strategies:
            strategy_name = type(strategy).__name__
            logger.info(f"执行策略：{strategy_name}")

            selected: list[str] = strategy.run()
            logger.info(f"{strategy_name} 选出 {len(selected)} 只股票")

            # 持久化选股结果。必须包住异常：main 不单独包裹每个策略，
            # 任何逃逸异常会终止整轮运行，让后续策略全部不执行。
            # 落库是次要功能，不能拖累飞书推送这条主链路。
            try:
                engine.save_selection(today_str, strategy_name, selected)
            except Exception as exc:
                logger.error(f"{strategy_name} 选股结果写库失败：{exc}")

            if selected:
                notifier.send(
                    symbols=selected,
                    strategy_name=strategy_name,
                    webhook_key=strategy.webhook_key,
                )
            else:
                logger.info(f"{strategy_name} 无选股结果，跳过推送")

    except Exception:
        try:
            _logger = get_logger(__name__)
            _logger.exception("主流程发生未捕获异常，程序终止")
        except Exception:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    logger.info("Sequoia-X V2 运行完成")


if __name__ == "__main__":
    main()
