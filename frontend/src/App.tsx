import { useEffect, useState } from 'react'

import {
  describeError,
  fetchDates,
  fetchSelections,
  fetchStrategies,
  type SelectionPage,
} from './api'
import { Filters } from './components/Filters'
import { SelectionList } from './components/SelectionList'

export default function App() {
  const [dates, setDates] = useState<string[]>([])
  const [strategies, setStrategies] = useState<string[]>([])
  const [date, setDate] = useState('')          // '' = 交给后端取最新
  const [strategy, setStrategy] = useState('')  // '' = 全部策略
  const [page, setPage] = useState<SelectionPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 首屏拉筛选项。失败不单独报错——下面的结果请求会报同样的错，
  // 两处都弹提示只会重复刷屏。
  useEffect(() => {
    let cancelled = false
    Promise.all([fetchDates(), fetchStrategies()])
      .then(([d, s]) => {
        if (cancelled) return
        setDates(d)
        setStrategies(s)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  // 筛选条件变化就重新拉结果。
  // cancelled 标志用于丢弃过期响应：用户快速切换筛选项时，先发的请求
  // 可能后返回，不拦住就会用旧数据覆盖新数据。
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    fetchSelections({ date: date || undefined, strategy: strategy || undefined })
      .then((result) => {
        if (!cancelled) setPage(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(describeError(err))
          setPage(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [date, strategy])

  return (
    <div className="app">
      <header>
        <h1>Sequoia-X 选股结果</h1>
      </header>

      <Filters
        dates={dates}
        strategies={strategies}
        date={date}
        strategy={strategy}
        disabled={loading}
        onDateChange={setDate}
        onStrategyChange={setStrategy}
      />

      <SelectionList loading={loading} error={error} page={page} />
    </div>
  )
}
