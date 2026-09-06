/** 共振区：被多个策略同时选中的票。全页唯一使用朱红的地方。
 *
 * 朱红在这里表示"值得注意"，**不表示"更值得买"**。回测结论正好相反：
 * 同一天被越多策略选中，后续表现越差，且 T+1/T+5/T+20 上单调一致
 * （2 个策略那档有 9,366 个样本，结论扎实）。见
 * `.trellis/tasks/09-06-selection-backtest/findings.md`。
 *
 * 所以副标题必须把这件事说出来。共振本身是有用的信息——它告诉你几个独立
 * 判据同时触发了——但"因此更该买"这个推论是错的，而红色和置顶的版面
 * 会让人自动做出这个推论。
 */

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
        <span className="note">
          被两个及以上策略同时选中 · 回测显示共振越强后续表现越弱，不代表更该买
        </span>
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
