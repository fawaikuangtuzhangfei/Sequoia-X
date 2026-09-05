/** 选股结果表格，含 loading / error / empty 三态。 */

import type { SelectionItem, SelectionPage } from '../api'

interface SelectionListProps {
  loading: boolean
  error: string | null
  page: SelectionPage | null
}

interface StrategyGroup {
  strategy: string
  items: SelectionItem[]
}

/**
 * 按策略把结果切成连续的分组。
 *
 * rank 是"该策略内的名次"，每个策略都从 0 重新开始。不分组直接平铺，
 * 序号列会读成 1,2,1,2,3,1,2,3,4 —— 看起来像坏了。
 * 接口已按 (策略, rank) 排好序，所以这里只需按相邻相同项切段。
 */
function groupByStrategy(items: SelectionItem[]): StrategyGroup[] {
  const groups: StrategyGroup[] = []
  for (const item of items) {
    const last = groups[groups.length - 1]
    if (last && last.strategy === item.strategy) {
      last.items.push(item)
    } else {
      groups.push({ strategy: item.strategy, items: [item] })
    }
  }
  return groups
}

export function SelectionList({ loading, error, page }: SelectionListProps) {
  if (loading) {
    return <p className="state">加载中…</p>
  }

  if (error) {
    // 后端没起来时最常见，提示必须能指导用户下一步动作
    return <p className="state state-error">{error}</p>
  }

  if (!page || page.items.length === 0) {
    // 有日期但没结果 ≠ 完全没数据，两种情况提示不同
    return (
      <p className="state">
        {page?.date
          ? `${page.date} 没有符合条件的选股结果`
          : '暂无数据，请先运行 python main.py 执行一次选股'}
      </p>
    )
  }

  const truncated = page.total > page.items.length

  return (
    <>
      <p className="summary">
        {page.date} · 共 {page.total} 条
        {truncated && `（受单页上限限制，仅显示前 ${page.items.length} 条）`}
      </p>
      <table className="results">
        <thead>
          <tr>
            <th className="num">序号</th>
            <th>代码</th>
            <th>名称</th>
          </tr>
        </thead>
        {groupByStrategy(page.items).map((group) => (
          // 每个策略一个 tbody：分组标题让"序号从 1 重新开始"变得显而易见
          <tbody key={group.strategy}>
            <tr className="group">
              <th colSpan={3}>
                {group.strategy}
                <span className="count">{group.items.length} 只</span>
              </th>
            </tr>
            {group.items.map((item) => (
              // rank 只在同一策略内唯一，所以键要带上策略名
              <tr key={`${item.strategy}-${item.symbol}`}>
                <td className="num">{item.rank + 1}</td>
                <td>
                  <a
                    href={`https://xueqiu.com/S/${item.xueqiu_code}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {item.symbol}
                  </a>
                </td>
                {/* 名称来自 stock_basic，查不到时降级显示占位符而不是空白 */}
                <td>{item.name ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </>
  )
}
