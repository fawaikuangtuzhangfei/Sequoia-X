/** 展示层的纯函数：分组、共振计算、文案格式化。不涉及请求。 */

import type { SelectionItem } from './api'

export interface StrategyGroup {
  strategy: string
  items: SelectionItem[]
}

export interface ResonantStock {
  symbol: string
  name: string | null
  xueqiuCode: string
  /** 选中它的策略及各自给出的名次，按策略名排序 */
  picks: { strategy: string; rank: number }[]
}

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/**
 * 去掉策略类名末尾的 Strategy。
 *
 * 七个策略全都以 Strategy 结尾，这个后缀不携带任何区分信息，
 * 占掉的却是列表里最显眼的横向空间。完整类名保留在 title 属性里。
 */
export function shortStrategy(name: string): string {
  return name.endsWith('Strategy') ? name.slice(0, -'Strategy'.length) : name
}

/**
 * 按策略切成连续分组。
 *
 * rank 是"该策略内的名次"，每个策略都从 0 重新开始。不分组直接平铺，
 * 序号列会读成 1,2,1,2,3 —— 看起来像坏了。
 * 接口已按 (策略, rank) 排序，所以只需按相邻相同项切段。
 */
export function groupByStrategy(items: SelectionItem[]): StrategyGroup[] {
  const groups: StrategyGroup[] = []
  for (const item of items) {
    const last = groups[groups.length - 1]
    if (last && last.strategy === item.strategy) {
      last.items.push(item)
    } else {
      groups.push({ strategy: item.strategy, items: [item] })
    }
  }
  return groups
}

/**
 * 找出被两个及以上策略同时选中的股票。
 *
 * 这是这份数据里最有分析价值、却在原始列表中完全看不出来的信息：
 * 多个独立策略在同一天指向同一只票。按共振策略数降序，同数按代码升序。
 */
export function findResonance(items: SelectionItem[]): ResonantStock[] {
  const bySymbol = new Map<string, ResonantStock>()

  for (const item of items) {
    const found = bySymbol.get(item.symbol)
    if (found) {
      found.picks.push({ strategy: item.strategy, rank: item.rank })
      // 名称可能只在部分记录上有（理论上不会，但不值得为此崩掉）
      found.name = found.name ?? item.name
    } else {
      bySymbol.set(item.symbol, {
        symbol: item.symbol,
        name: item.name,
        xueqiuCode: item.xueqiu_code,
        picks: [{ strategy: item.strategy, rank: item.rank }],
      })
    }
  }

  return [...bySymbol.values()]
    .filter((s) => s.picks.length > 1)
    .map((s) => ({
      ...s,
      picks: [...s.picks].sort((a, b) => a.strategy.localeCompare(b.strategy)),
    }))
    .sort((a, b) => b.picks.length - a.picks.length || a.symbol.localeCompare(b.symbol))
}

/** 'YYYY-MM-DD' -> { big: '09.05', year: '2026', weekday: '周六' } */
export function splitDate(date: string): { big: string; year: string; weekday: string } {
  const [y, m, d] = date.split('-').map(Number)
  // 用分量构造而不是 new Date(date)：后者按 UTC 解析，
  // 在 UTC 以西的时区会算出前一天的星期。
  const weekday = WEEKDAYS[new Date(y, m - 1, d).getDay()] ?? ''
  return { big: `${String(m).padStart(2, '0')}.${String(d).padStart(2, '0')}`, year: String(y), weekday }
}

/** 把日期列表按月份切段，用于左栏分组。输入已是倒序。 */
export function groupDatesByMonth(dates: string[]): { month: string; dates: string[] }[] {
  const out: { month: string; dates: string[] }[] = []
  for (const date of dates) {
    const month = date.slice(0, 7)
    const last = out[out.length - 1]
    if (last && last.month === month) {
      last.dates.push(date)
    } else {
      out.push({ month, dates: [date] })
    }
  }
  return out
}
