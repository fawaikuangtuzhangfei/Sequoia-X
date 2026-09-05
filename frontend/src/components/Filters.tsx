/** 日期与策略两个下拉筛选器。 */

interface FiltersProps {
  dates: string[]
  strategies: string[]
  /** 空串表示"最新日期" */
  date: string
  /** 空串表示"全部策略" */
  strategy: string
  disabled: boolean
  onDateChange: (value: string) => void
  onStrategyChange: (value: string) => void
}

export function Filters(props: FiltersProps) {
  const { dates, strategies, date, strategy, disabled } = props

  return (
    <div className="filters">
      <label>
        日期
        <select
          value={date}
          disabled={disabled || dates.length === 0}
          onChange={(e) => props.onDateChange(e.target.value)}
        >
          <option value="">最新</option>
          {dates.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </label>

      <label>
        策略
        <select
          value={strategy}
          disabled={disabled || strategies.length === 0}
          onChange={(e) => props.onStrategyChange(e.target.value)}
        >
          <option value="">全部</option>
          {strategies.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
