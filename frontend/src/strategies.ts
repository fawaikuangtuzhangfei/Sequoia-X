/**
 * 策略说明文案。
 *
 * **每一条判据都逐字核对自 sequoia_x/strategy/ 下的源码**，包括阈值。
 * 这里是给人读的文案，不是代码注释的副本——源码 docstring 里混着
 * "严禁 iterrows" 这类实现约定，对使用者毫无意义。
 *
 * 因此它是一份人工维护的副本，会漂移：README 的策略表就已经漂了
 * （海龟那条还写着"按涨幅排序"，实际早就改成按流通市值）。
 * **改动任何策略的阈值或排序，必须同步这里。**
 */

export interface StrategyDoc {
  /** 与 selection_result.strategy 一致的类名 */
  className: string
  /** 中文名 */
  title: string
  /** 一句话说清它在找什么 */
  summary: string
  /** 逐条判据，顺序与源码一致 */
  criteria: string[]
  /** 数据来源 */
  source: string
  /** 结果顺序的依据 —— rank 的含义 */
  ordering: string
  /** 需要多少历史数据才会参与计算 */
  minBars: string
  /** 使用时需要留神的地方 */
  caveat?: string
}

export const STRATEGY_DOCS: StrategyDoc[] = [
  {
    className: 'TurtleTradeStrategy',
    title: '海龟突破',
    summary: '创 20 日新高，同时要求成交额过亿、当天是实体阳线。',
    criteria: [
      '今日收盘价 > 前 20 个交易日的最高价',
      '今日成交额 > 1 亿元',
      '今日收盘价 > 今日开盘价（实体阳线）',
      '今日收盘价 > 昨日收盘价（排除高开低走的假阳线）',
    ],
    source: '本地日线；排序时另向 baostock 查当日不复权价与换手率',
    ordering: '按流通市值从大到小。流通市值 = 成交量 ÷ 换手率 × 不复权收盘价',
    minBars: '21 个交易日',
  },
  {
    className: 'MaVolumeStrategy',
    title: '均线金叉放量',
    summary: '5 日均线上穿 20 日均线的当天，并且明显放量。',
    criteria: [
      '昨日 MA5 < MA20，今日 MA5 > MA20（金叉发生在今天）',
      '今日成交量 > 20 日均量 × 1.5',
    ],
    source: '本地日线',
    ordering: '未排序，按股票代码的遍历顺序',
    minBars: '20 个交易日',
  },
  {
    className: 'HighTightFlagStrategy',
    title: '高旗形整理',
    summary: '大涨之后极度缩量收敛，价格没跌下来，等变盘。',
    criteria: [
      '近 40 日最高价 ÷ 最低价 > 1.6（区间涨幅超 60%）',
      '近 10 日最高价 ÷ 最低价 < 1.15（振幅收窄到 15% 以内）',
      '近 10 日最低价 ≥ 近 40 日最高价 × 0.8（高位没破位）',
      '今日成交量 < 前 20 日均量 × 0.6（缩量）',
    ],
    source: '本地日线',
    ordering: '未排序，按股票代码的遍历顺序',
    minBars: '40 个交易日',
  },
  {
    className: 'LimitUpShakeoutStrategy',
    title: '涨停洗盘',
    summary: '涨停次日放量收阴，但最低价没跌破前一日收盘——当作洗盘而非见顶。',
    criteria: [
      '昨日涨幅 ≥ 9.5%（视作涨停）',
      '今日收盘价 < 今日开盘价（收阴）',
      '今日成交量 > 昨日成交量 × 2',
      '今日最低价 ≥ 昨日收盘价（支撑未破）',
    ],
    source: '本地日线',
    ordering: '未排序，按股票代码的遍历顺序',
    minBars: '3 个交易日',
  },
  {
    className: 'UptrendLimitDownStrategy',
    title: '上升趋势跌停',
    summary: '还在上升趋势里却放量跌停，当作错杀来观察。',
    criteria: [
      '昨日 MA20 > MA60（处于上升趋势）',
      '今日跌幅 ≥ 9.5%（视作跌停）',
      '今日成交量 > 20 日均量 × 2',
    ],
    source: '本地日线',
    ordering: '未排序，按股票代码的遍历顺序',
    minBars: '60 个交易日',
  },
  {
    className: 'RpsBreakoutStrategy',
    title: 'RPS 强度突破',
    summary: '120 日涨幅排进全市场前 10%，且股价仍贴着区间高点。',
    criteria: [
      '计算每只股票最近 120 个交易日的涨幅',
      '横向排名，取 RPS ≥ 90（即涨幅前 10%）',
      '今日收盘价 ≥ 该股 120 日最高价 × 0.9',
    ],
    source: '本地日线（一次读入全表做横向排名）',
    ordering: '未排序，按横向排名后的表顺序',
    minBars: '120 个交易日（滚动最高价 60 根起算）',
    caveat:
      'RPS 是相对排名，"前 10%" 只相对于本地已回填的股票池。池子越小，排名越不可信——回填不完整时这个策略的结果参考价值有限。',
  },
  {
    className: 'PrivatePlacementStrategy',
    title: '定增公告',
    summary: '最近 7 天发布定向增发公告的股票。这是公告监控，不是技术形态。',
    criteria: ['发行方式为「定向增发」（排除公开增发）', '发行日期在最近 7 天内'],
    source: 'akshare 东方财富「全部增发」，完全不使用本地行情',
    ordering: '按发行日期从新到旧',
    minBars: '不需要历史行情',
    caveat: '同一只股票可能有多条定增记录，已按代码去重，只保留最新的一条位置。',
  },
]

const BY_CLASS = new Map(STRATEGY_DOCS.map((doc) => [doc.className, doc]))

/** 按类名取说明。未收录的策略返回 undefined，调用方负责降级。 */
export function docFor(className: string): StrategyDoc | undefined {
  return BY_CLASS.get(className)
}
