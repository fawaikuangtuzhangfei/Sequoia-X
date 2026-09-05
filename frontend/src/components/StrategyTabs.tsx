/** 策略筛选。选项只来自当天真实跑出结果的策略，选了不会得到空列表。 */

import type { StrategyDoc } from '../api'
import { shortStrategy, type StrategyGroup } from '../format'

interface StrategyTabsProps {
  groups: StrategyGroup[]
  /** 类名 -> 说明，用于显示中文名。取失败时降级为英文短名 */
  docs: Map<string, StrategyDoc>
  active: string | null
  total: number
  onPick: (strategy: string | null) => void
}

export function StrategyTabs({ groups, docs, active, total, onPick }: StrategyTabsProps) {
  if (groups.length < 2) {
    return null // 只有一个策略时，筛选器没有意义
  }

  return (
    <div className="tabs" role="tablist" aria-label="策略">
      <button
        type="button"
        role="tab"
        className="tab"
        aria-selected={active === null}
        onClick={() => onPick(null)}
      >
        全部 <span className="n">{total}</span>
      </button>

      {groups.map((group) => (
        <button
          key={group.strategy}
          type="button"
          role="tab"
          className="tab"
          title={group.strategy}
          aria-selected={active === group.strategy}
          onClick={() => onPick(group.strategy)}
        >
          {docs.get(group.strategy)?.title ?? shortStrategy(group.strategy)}{' '}
          <span className="n">{group.items.length}</span>
        </button>
      ))}
    </div>
  )
}
