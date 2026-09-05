/** 结果列表：每个策略一段，行内标出共振。 */

import type { CSSProperties } from 'react'

import { shortStrategy, type StrategyGroup } from '../format'

interface SelectionListProps {
  groups: StrategyGroup[]
  /** 共振股票代码，用于在行内打标记 */
  resonant: Set<string>
}

export function SelectionList({ groups, resonant }: SelectionListProps) {
  return (
    <>
      {groups.map((group) => (
        <section className="strat" key={group.strategy}>
          <div className="strat-head">
            <h2 className="strat-name" title={group.strategy}>
              {shortStrategy(group.strategy)}
            </h2>
            <span className="strat-n">{group.items.length}</span>
            <span className="strat-rule" />
          </div>

          <div className="rows">
            {group.items.map((item, i) => (
              // rank 只在同一策略内唯一，键要带上策略名
              <a
                className="row"
                key={`${item.strategy}-${item.symbol}`}
                style={{ '--i': Math.min(i, 12) } as CSSProperties}
                href={`https://xueqiu.com/S/${item.xueqiu_code}`}
                target="_blank"
                rel="noreferrer"
              >
                <span className="row-rank">{String(item.rank + 1).padStart(2, '0')}</span>
                <span className="row-code">{item.symbol}</span>
                {/* 名称来自 stock_basic，查不到时降级显示占位符而不是空白 */}
                <span className={item.name ? 'row-name' : 'row-name is-missing'}>
                  {item.name ?? '未收录名称'}
                </span>
                {/* 这一列只留给共振标记。原本每行都有个 ↗，出现 26 次却不携带
                    任何信息，反而把稀少且重要的共振标记稀释掉了。 */}
                {resonant.has(item.symbol) && <span className="row-mark">共振</span>}
              </a>
            ))}
          </div>
        </section>
      ))}
    </>
  )
}
