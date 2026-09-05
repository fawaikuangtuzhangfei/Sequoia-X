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

/**
 * 一个策略的说明。
 *
 * 内容来自策略类自身的属性（`sequoia_x/strategy/*.py`），随接口下发。
 * 前端不再维护副本——判据里的阈值和 run() 里的阈值现在住在同一个文件里。
 */
export interface StrategyDoc {
  class_name: string
  title: string
  summary: string
  criteria: string[]
  data_source: string
  ordering: string
  min_bars: string
  caveat: string | null
}

export interface SelectionPage {
  date: string | null
  strategy: string | null
  total: number
  page: number
  page_size: number
  items: SelectionItem[]
}

const UNREACHABLE_HINT = '无法连接后端服务，请确认已运行 python serve.py'

/** 请求根本没到达后端（连接被拒，或被开发代理挡下）。 */
export class BackendUnreachableError extends Error {
  constructor() {
    super(UNREACHABLE_HINT)
    this.name = 'BackendUnreachableError'
  }
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(path)

  if (!resp.ok) {
    // 后端自己回的错一定带 {"detail": ...}（见 api/app.py 的异常处理器）。
    // 后端没启动时 vite proxy 回的是**空 body 的 5xx**，解析不出 detail——
    // 靠这个区分"后端报错了"和"根本没连上"，后者要给出可操作的提示。
    const detail = await resp
      .json()
      .then((body: unknown) =>
        typeof (body as { detail?: unknown })?.detail === 'string'
          ? ((body as { detail: string }).detail)
          : null,
      )
      .catch(() => null)

    if (detail) {
      throw new Error(`${detail}（${path}）`)
    }
    throw new BackendUnreachableError()
  }

  return (await resp.json()) as T
}

export function fetchDates(): Promise<string[]> {
  return getJson<string[]>('/api/dates')
}

export function fetchStrategies(): Promise<string[]> {
  return getJson<string[]>('/api/strategies')
}

/** 全部已注册策略的说明，包含当天没跑出结果的那些。 */
export function fetchStrategyDocs(): Promise<StrategyDoc[]> {
  return getJson<StrategyDoc[]>('/api/strategies/docs')
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
 * 后端连不上有两条路径，提示必须一致：
 *   - 生产态（FastAPI 直供页面）：页面已加载后进程挂掉，fetch 抛 TypeError
 *     （"Failed to fetch"），直接展示这个英文串对用户毫无意义
 *   - 开发态（vite dev server）：proxy 把连接失败转成空 body 的 5xx，
 *     由 getJson 归一成 BackendUnreachableError
 */
export function describeError(error: unknown): string {
  if (error instanceof TypeError) {
    return UNREACHABLE_HINT
  }
  return error instanceof Error ? error.message : String(error)
}
