/** 策略筛选。选项只来自当天真实跑出结果的策略，选了不会得到空列表。 */

import { shortStrategy, type StrategyGroup } from '../format'

interface StrategyTabsProps {
  groups: StrategyGroup[]
  active: string | null
  total: number
  onPick: (strategy: string | null) => void
}

export function StrategyTabs({ groups, active, total, onPick }: StrategyTabsProps) {
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
          {shortStrategy(group.strategy)} <span className="n">{group.items.length}</span>
        </button>
      ))}
    </div>
  )
}
