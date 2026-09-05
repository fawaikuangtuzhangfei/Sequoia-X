/** 主视觉：日期。这个工具的全部意义是"看某一天"，所以日期就是主角。 */

import { splitDate } from '../format'

interface DayHeaderProps {
  date: string
  total: number
  strategyCount: number
}

export function DayHeader({ date, total, strategyCount }: DayHeaderProps) {
  const { big, year, weekday } = splitDate(date)

  return (
    <header className="daymark">
      <div className="daymark-date">{big}</div>
      <div className="daymark-meta">
        <span className="daymark-year">
          {year} · {weekday}
        </span>
        <span className="daymark-count">
          <b>{total}</b> 只 · <b>{strategyCount}</b> 个策略选出
        </span>
      </div>
    </header>
  )
}
