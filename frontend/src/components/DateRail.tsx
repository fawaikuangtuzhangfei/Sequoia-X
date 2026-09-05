/** 左栏交易日导航。日期是这个工具的主索引，所以它不是下拉框而是常驻列表。 */

import { groupDatesByMonth } from '../format'

interface DateRailProps {
  dates: string[]
  active: string | null
  onPick: (date: string) => void
}

export function DateRail({ dates, active, onPick }: DateRailProps) {
  return (
    <nav className="rail" aria-label="交易日">
      <div className="rail-brand">
        <b>SEQUOIA·X</b>
        <span>选股结果</span>
      </div>

      <div className="rail-head">
        <span className="label">交易日</span>
      </div>

      {dates.length === 0 ? (
        <p className="rail-empty">暂无记录</p>
      ) : (
        groupDatesByMonth(dates).map((group) => (
          <div key={group.month}>
            <div className="rail-month">{group.month.replace('-', ' / ')}</div>
            <div className="rail-days">
              {group.dates.map((date) => (
                <button
                  key={date}
                  type="button"
                  className="rail-day"
                  aria-current={date === active}
                  onClick={() => onPick(date)}
                >
                  {date.slice(5)}
                </button>
              ))}
            </div>
          </div>
        ))
      )}
    </nav>
  )
}
