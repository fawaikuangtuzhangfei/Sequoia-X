/** 共振区：被多个策略同时选中的票。全页唯一使用朱红的地方。
 *
 * 朱红在这里表示"值得注意"，**不表示"更值得买"**。
 *
 * 回测跑过两轮，方向是反的：2,000 只池子 / 2.7 年那一轮，共振越强后续表现
 * 越差；补齐到 5,223 只、只看最近 12 个月那一轮，又转成正向。换个样本就换个
 * 结论，说明它更像特定池子和时段的产物，不是一条能拿来下注的规律。
 * 见 `.trellis/tasks/09-06-selection-backtest/findings.md`。
 *
 * 所以副标题只说事实（几个策略同时选中了它），不替用户下"因此更该买"
 * 的判断——红色加置顶的版面已经在暗示这个推论了，文字不能再帮腔。
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
          被两个及以上策略同时选中 · 回测未能证明共振更强就更该买，两轮结论方向相反
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
