/**
 * 后端接口封装。类型与 sequoia_x/api/schemas.py 一一对应，改动需同步。
 */

/** 单页最大条数，与后端 _MAX_PAGE_SIZE 一致；超出会被后端 422 拒绝。 */
export const MAX_PAGE_SIZE = 200

export interface SelectionItem {
  symbol: string
  name: string | null
  /** 选出该股票的策略。不过滤策略时同一只票可能出现多行，靠这个字段区分。 */
  strategy: string
  /** 该策略内的输出顺序，从 0 开始。跨策略之间不可比。 */
  rank: number
  xueqiu_code: string
}

export interface SelectionPage {
  date: string | null
  strategy: string | null
  total: number
  page: number
  page_size: number
  items: SelectionItem[]
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path)
  if (!resp.ok) {
    throw new Error(`接口返回 ${resp.status}（${path}）`)
  }
  return (await resp.json()) as T
}

export function fetchDates(): Promise<string[]> {
  return getJson<string[]>('/api/dates')
}

export function fetchStrategies(): Promise<string[]> {
  return getJson<string[]>('/api/strategies')
}

export function fetchSelections(options: {
  date?: string
  strategy?: string
}): Promise<SelectionPage> {
  const query = new URLSearchParams()
  if (options.date) query.set('date', options.date)
  if (options.strategy) query.set('strategy', options.strategy)
  // 一天的选股结果通常几十条，一次拉满一页即可，无需前端翻页
  query.set('page_size', String(MAX_PAGE_SIZE))
  return getJson<SelectionPage>(`/api/selections?${query.toString()}`)
}

/**
 * 把异常转成给用户看的中文提示。
 *
 * fetch 在后端没起来时抛的是 TypeError（"Failed to fetch"），
 * 直接展示这个英文串对用户毫无意义，必须翻译成可操作的提示。
 */
export function describeError(error: unknown): string {
  if (error instanceof TypeError) {
    return '无法连接后端服务，请确认已运行 python serve.py'
  }
  return error instanceof Error ? error.message : String(error)
}
