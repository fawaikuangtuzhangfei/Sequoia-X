/** 共振区：被多个策略同时选中的票。全页唯一使用朱红的地方。 */

import { shortStrategy, type ResonantStock } from '../format'

interface ResonanceProps {
  stocks: ResonantStock[]
}

export function Resonance({ stocks }: ResonanceProps) {
  if (stocks.length === 0) {
    return null
  }

  return (
    <section className="resonance" aria-label="多策略共振">
      <div className="resonance-head">
        <span className="title">共振 {stocks.length}</span>
        <span className="note">被两个及以上策略同时选中</span>
      </div>

      {stocks.map((stock) => (
        <div className="resonance-item" key={stock.symbol}>
          <a
            className="resonance-code"
            href={`https://xueqiu.com/S/${stock.xueqiuCode}`}
            target="_blank"
            rel="noreferrer"
          >
            {stock.symbol}
          </a>
          <span className="resonance-name">{stock.name ?? '—'}</span>
          <span className="resonance-tags">
            {stock.picks.map((pick) => (
              <span className="resonance-tag" key={pick.strategy} title={pick.strategy}>
                {shortStrategy(pick.strategy)} <b>{pick.rank + 1}</b>
              </span>
            ))}
          </span>
        </div>
      ))}
    </section>
  )
}
