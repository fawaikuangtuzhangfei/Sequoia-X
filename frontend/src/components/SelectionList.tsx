/** 结果列表：每个策略一段，行内标出共振。 */

import type { CSSProperties } from 'react'

import { shortStrategy, type StrategyGroup } from '../format'
import { docFor } from '../strategies'

interface SelectionListProps {
  groups: StrategyGroup[]
  /** 共振股票代码，用于在行内打标记 */
  resonant: Set<string>
}

export function SelectionList({ groups, resonant }: SelectionListProps) {
  return (
    <>
      {groups.map((group) => {
        const doc = docFor(group.strategy)

        return (
          <section className="strat" key={group.strategy}>
            <div className="strat-head">
              {/* 中文名做主标题，英文类名降为标识符：类名才是库里和飞书推送里
                  出现的东西，得留着，但它不该占据最显眼的位置 */}
              <h2 className="strat-name">{doc?.title ?? shortStrategy(group.strategy)}</h2>
              <code className="strat-class">{shortStrategy(group.strategy)}</code>
              <span className="strat-n">{group.items.length}</span>
              <span className="strat-rule" />
            </div>

            {/* "这是什么"是看着结果时冒出来的疑问，就在原地回答。
                完整判据在左栏的策略说明里。 */}
            {doc && <p className="strat-note">{doc.summary}</p>}

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
        )
      })}
    </>
  )
}
