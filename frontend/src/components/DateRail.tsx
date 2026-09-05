/** 左栏导航。日期是这个工具的主索引，所以它不是下拉框而是常驻列表。 */

import { groupDatesByMonth } from '../format'

interface DateRailProps {
  dates: string[]
  active: string | null
  /** 当前是否停留在策略说明视图 */
  guideOpen: boolean
  onPick: (date: string) => void
  onOpenGuide: () => void
}

export function DateRail({ dates, active, guideOpen, onPick, onOpenGuide }: DateRailProps) {
  return (
    <nav className="rail" aria-label="导航">
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
                  aria-current={!guideOpen && date === active}
                  onClick={() => onPick(date)}
                >
                  {date.slice(5)}
                </button>
              ))}
            </div>
          </div>
        ))
      )}

      {/* 说明必须独立于结果页：当天没选出票的策略，在结果页里根本不出现，
          而那些恰恰是最需要解释的。 */}
      <div className="rail-foot">
        <button
          type="button"
          className="rail-day rail-guide"
          aria-current={guideOpen}
          onClick={onOpenGuide}
        >
          策略说明
        </button>
      </div>
    </nav>
  )
}
