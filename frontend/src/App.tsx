import { useEffect, useMemo, useState } from 'react'

import {
  describeError,
  fetchDates,
  fetchSelections,
  fetchStrategyDocs,
  type SelectionPage,
  type StrategyDoc,
} from './api'
import { findResonance, groupByStrategy } from './format'
import { DateRail } from './components/DateRail'
import { DayHeader } from './components/DayHeader'
import { Resonance } from './components/Resonance'
import { SelectionList } from './components/SelectionList'
import { StrategyGuide } from './components/StrategyGuide'
import { StrategyTabs } from './components/StrategyTabs'

export default function App() {
  const [dates, setDates] = useState<string[]>([])
  const [date, setDate] = useState<string | null>(null)
  const [strategy, setStrategy] = useState<string | null>(null)
  // 说明是一个视图切换而不是一条路由：只有两个视图，引入路由库
  // 换不来任何东西，还会破坏"运行时依赖只有 react"的约束。
  const [guideOpen, setGuideOpen] = useState(false)
  const [page, setPage] = useState<SelectionPage | null>(null)
  const [docs, setDocs] = useState<StrategyDoc[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 策略说明。取失败只是没有说明文字，结果照常能看，
  // 所以这里刻意不上报错误——dates 那条链路已经会报同一个后端故障。
  useEffect(() => {
    let cancelled = false
    fetchStrategyDocs()
      .then((list) => {
        if (!cancelled) setDocs(list)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  // 首屏：取交易日列表，并选中最新的一天。
  // 这里的失败必须上报——后面按日期取结果的请求不会发出，
  // 没人接手的话后端挂掉就成了永久"加载中"。
  useEffect(() => {
    let cancelled = false
    fetchDates()
      .then((list) => {
        if (cancelled) return
        setDates(list)
        setDate(list[0] ?? null)
        if (list.length === 0) setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(describeError(err))
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 一次取回当天全部结果，策略筛选在前端做。
  // 这样切策略是瞬时的，也才能算出"共振"——共振是整天的属性，
  // 按策略过滤后的数据里根本看不出来。
  useEffect(() => {
    if (date === null) return

    let cancelled = false
    setLoading(true)
    setError(null)

    fetchSelections({ date })
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
  }, [date])

  const items = page?.items ?? []
  const groups = useMemo(() => groupByStrategy(items), [items])
  const resonance = useMemo(() => findResonance(items), [items])
  const resonantSymbols = useMemo(
    () => new Set(resonance.map((s) => s.symbol)),
    [resonance],
  )

  const shownGroups = strategy ? groups.filter((g) => g.strategy === strategy) : groups
  const truncated = page !== null && page.total > page.items.length

  const strategiesToday = useMemo(
    () => new Set(groups.map((g) => g.strategy)),
    [groups],
  )
  const docMap = useMemo(
    () => new Map(docs.map((d) => [d.class_name, d])),
    [docs],
  )

  const pickDate = (next: string) => {
    setDate(next)
    setStrategy(null) // 换天后旧的策略筛选多半不适用，重置更可预期
    setGuideOpen(false)
  }

  return (
    <div className="shell">
      <DateRail
        dates={dates}
        active={date}
        guideOpen={guideOpen}
        onPick={pickDate}
        onOpenGuide={() => setGuideOpen(true)}
      />

      <main className="main">
        {guideOpen ? (
          <StrategyGuide docs={docs} activeToday={strategiesToday} />
        ) : error ? (
          <p className="state state-error">
            {error}
            <br />
            服务未启动时，请在项目根目录运行 <code>python serve.py</code>
          </p>
        ) : loading ? (
          <p className="state">读取中…</p>
        ) : page?.date == null ? (
          <p className="state">
            还没有选股记录。
            <br />
            运行 <code>python main.py</code> 跑一轮，结果会自动落库。
          </p>
        ) : (
          <>
            <DayHeader date={page.date} total={page.total} strategyCount={groups.length} />

            <Resonance stocks={resonance} />

            <StrategyTabs
              groups={groups}
              docs={docMap}
              active={strategy}
              total={items.length}
              onPick={setStrategy}
            />

            {items.length === 0 ? (
              <p className="state">这天跑过，但没有股票通过筛选。</p>
            ) : (
              <SelectionList
                groups={shownGroups}
                docs={docMap}
                resonant={resonantSymbols}
              />
            )}

            {truncated && (
              <p className="truncated">
                受单页上限限制，仅显示前 {items.length} 条，共 {page.total} 条。
              </p>
            )}
          </>
        )}
      </main>
    </div>
  )
}
