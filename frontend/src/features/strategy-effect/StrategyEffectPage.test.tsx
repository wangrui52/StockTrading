import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { StrategyEffectPage } from './StrategyEffectPage'

const activeStatus = {
  active_batch: { rule_version: 'rules-v2', trade_date: '2026-08-31', batch_id: 7 },
  latest_sync: null,
}
const summary = {
  total: 40, completed: 32, unavailable: 3, pending: 5, sample_size: 32,
  completion_rate: 0.875, mean_return_rate: 2.5, median_return_rate: 1.25,
  positive_return_ratio: 0.625, mean_mfe: 8.5, mean_mae: -4.25,
  max_drawdown_approx: -7.75,
  insufficient_sample: false, calculation_version: 'outcome-v1',
  filters: {}, data_date: '2026-08-31',
}
const item = {
  id: 1, candidate_result_id: 8, market: 'SH', stock_code: '600000', stock_name: '浦发银行',
  source_batch_id: 1, evaluation_batch_id: 7, source_trade_date: '2026-08-27',
  rule_version: 'rules-v2', horizon_trading_days: 5,
  reference_trade_date: '2026-08-28', evaluation_trade_date: '2026-09-03',
  expected_evaluation_trade_date: null,
  reference_price: 10, evaluation_price: 10.5, return_rate: 5, mfe: 8, mae: -3,
  status: 'COMPLETED', unavailable_reason: null, calculation_version: 'outcome-v1',
  updated_at: '2026-09-03T10:00:00Z',
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><StrategyEffectPage /></QueryClientProvider>)
}

function normalFetch(overrides: { summary?: object; items?: object[]; total?: number } = {}) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/system/status')) return Response.json(activeStatus)
    if (url.includes('/strategy/outcomes/summary?')) {
      return Response.json(overrides.summary ?? summary)
    }
    return Response.json({
      items: overrides.items ?? [item], total: overrides.total ?? 1, page: 1, page_size: 20,
      calculation_version: 'outcome-v1', filters: {}, data_date: '2026-08-31',
    })
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('StrategyEffectPage', () => {
  it('使用生产规则、T+5和最近60个交易日发起默认请求', async () => {
    const fetchMock = normalFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderPage()

    expect(await screen.findByRole('heading', { name: '策略效果' })).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = String(input)
      return url.includes('/strategy/outcomes?') && url.includes('rule_version=rules-v2')
        && url.includes('horizon=5') && url.includes('latest_trading_days=60')
        && url.includes('page=1') && url.includes('page_size=20')
    })).toBe(true))
  })

  it.each(['summary', 'list'] as const)(
    '初次加载时%s先返回仍等待另一项，不显示假空态',
    async (first) => {
      let resolveSummary: ((response: Response) => void) | undefined
      let resolveList: ((response: Response) => void) | undefined
      vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/system/status')) return Promise.resolve(Response.json(activeStatus))
        if (url.includes('/strategy/outcomes/summary?')) {
          return new Promise<Response>((resolve) => { resolveSummary = resolve })
        }
        return new Promise<Response>((resolve) => { resolveList = resolve })
      }))
      renderPage()
      await waitFor(() => expect(resolveSummary && resolveList).toBeTruthy())

      if (first === 'summary') resolveSummary?.(Response.json(summary))
      else resolveList?.(Response.json({
        items: [item], total: 1, page: 1, page_size: 20,
        calculation_version: 'outcome-v1', filters: {}, data_date: '2026-08-31',
      }))

      await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('正在读取策略效果'))
      expect(screen.queryByText('暂无候选评价数据')).not.toBeInTheDocument()
      expect(screen.queryByRole('table', { name: '策略效果明细' })).not.toBeInTheDocument()

      if (first === 'summary') resolveList?.(Response.json({
        items: [item], total: 1, page: 1, page_size: 20,
        calculation_version: 'outcome-v1', filters: {}, data_date: '2026-08-31',
      }))
      else resolveSummary?.(Response.json(summary))
      expect(await screen.findByRole('table', { name: '策略效果明细' })).toBeInTheDocument()
    },
  )

  it('覆盖加载、失败重试、无有效批次和空数据', async () => {
    let resolveStatus: ((response: Response) => void) | undefined
    const pendingFetch = vi.fn(() => new Promise<Response>((resolve) => { resolveStatus = resolve }))
    vi.stubGlobal('fetch', pendingFetch)
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('正在读取策略效果')
    resolveStatus?.(Response.json({ error: {} }, { status: 500 }))
    expect(await screen.findByText('策略效果读取失败')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(pendingFetch).toHaveBeenCalledTimes(2))

    cleanup()
    const noActive = vi.fn(async () => Response.json({ active_batch: null, latest_sync: null }))
    vi.stubGlobal('fetch', noActive)
    renderPage()
    expect(await screen.findByText('当前没有可用的生产数据批次')).toBeInTheDocument()
    expect(noActive).toHaveBeenCalledTimes(1)

    cleanup()
    vi.stubGlobal('fetch', normalFetch({ summary: { ...summary, total: 0 }, items: [], total: 0 }))
    renderPage()
    expect(await screen.findByText('暂无候选评价数据')).toBeInTheDocument()
  })

  it('展示服务端指标、明细、样本和部分不可用提示', async () => {
    vi.stubGlobal('fetch', normalFetch({ summary: { ...summary, insufficient_sample: true } }))
    renderPage()

    expect(await screen.findByText('87.50%')).toBeInTheDocument()
    expect(screen.getByText('62.50%')).toBeInTheDocument()
    expect(screen.getByText('平均 2.50% · 中位 1.25%')).toBeInTheDocument()
    const drawdown = screen.getByRole('article', { name: '最大回撤近似值' })
    expect(within(drawdown).getByText('-7.75%')).toBeInTheDocument()
    expect(drawdown).toHaveAttribute('title', expect.stringContaining('不是资金曲线最大回撤'))
    expect(within(drawdown).getByText('样本最差 MAE 近似，非资金曲线回撤')).toBeInTheDocument()
    expect(screen.getByText('样本不足，不用于判断策略有效性')).toBeInTheDocument()
    expect(screen.getByText('部分候选因行情缺失或停牌无法评价')).toBeInTheDocument()
    const table = screen.getByRole('table', { name: '策略效果明细' })
    expect(within(table).getByText('浦发银行')).toBeInTheDocument()
    expect(within(table).getByText('600000 · SH')).toBeInTheDocument()
    expect(within(table).getByText('T+5')).toBeInTheDocument()
    expect(screen.getByText('不复权、T+1开盘参考、仅供研究')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('胜率')
  })

  it('PENDING 仅显示预计日或等待日历，终态仍显示实际评价日', async () => {
    const pendingWithDate = {
      ...item,
      id: 2,
      stock_code: '600001',
      status: 'PENDING',
      evaluation_trade_date: null,
      expected_evaluation_trade_date: '2026-09-08',
      evaluation_price: null,
    }
    const pendingWithoutDate = {
      ...pendingWithDate,
      id: 3,
      stock_code: '600002',
      expected_evaluation_trade_date: null,
    }
    const completedWithDifferentExpectation = {
      ...item,
      expected_evaluation_trade_date: '2026-09-09',
    }
    vi.stubGlobal('fetch', normalFetch({
      items: [pendingWithDate, pendingWithoutDate, completedWithDifferentExpectation],
      total: 3,
    }))
    renderPage()

    const rows = within(await screen.findByRole('table', { name: '策略效果明细' })).getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('预计 2026-09-08')).toBeInTheDocument()
    expect(within(rows[0]).queryByText('2026-09-08')).not.toBeInTheDocument()
    expect(within(rows[1]).getByText('等待交易日历更新')).toBeInTheDocument()
    expect(within(rows[2]).getByText('2026-09-03')).toBeInTheDocument()
    expect(within(rows[2]).queryByText(/2026-09-09/)).not.toBeInTheDocument()
  })

  it('过滤变更进入请求key并重置页码，支持分页', async () => {
    const fetchMock = normalFetch({ total: 45 })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findByRole('table', { name: '策略效果明细' })

    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('page=2'))).toBe(true))
    await userEvent.selectOptions(screen.getByLabelText('评价周期'), '3')
    await userEvent.clear(screen.getByLabelText('规则版本'))
    await userEvent.type(screen.getByLabelText('规则版本'), 'rules-old')
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    await userEvent.type(screen.getByLabelText('开始日期'), '2026-08-01')
    await userEvent.type(screen.getByLabelText('结束日期'), '2026-08-31')
    await userEvent.selectOptions(screen.getByLabelText('评价状态'), 'UNAVAILABLE')

    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = String(input)
      return url.includes('/strategy/outcomes?') && url.includes('horizon=3')
        && url.includes('rule_version=rules-old') && url.includes('date_from=2026-08-01')
        && url.includes('date_to=2026-08-31') && url.includes('status=UNAVAILABLE')
        && url.includes('page=1')
    })).toBe(true))
    expect(screen.getByText('第 1 页 · 共 45 条')).toBeInTheDocument()
  })

  it('切换筛选后新请求未完成时不展示旧scope数据', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/system/status')) return Response.json(activeStatus)
      if (url.includes('horizon=3')) return new Promise<Response>(() => undefined)
      if (url.includes('/strategy/outcomes/summary?')) return Response.json(summary)
      return Response.json({
        items: [item], total: 1, page: 1, page_size: 20,
        calculation_version: 'outcome-v1', filters: {}, data_date: '2026-08-31',
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    expect(await screen.findByText('87.50%')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('评价周期'), '3')

    expect(await screen.findByRole('status')).toHaveTextContent('正在读取策略效果')
    expect(screen.queryByText('87.50%')).not.toBeInTheDocument()
    expect(screen.queryByRole('table', { name: '策略效果明细' })).not.toBeInTheDocument()
  })

  it('清空规则版本后不保留旧数据，也不发送空规则请求', async () => {
    const fetchMock = normalFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findByRole('table', { name: '策略效果明细' })
    const callsBeforeClear = fetchMock.mock.calls.length

    await userEvent.clear(screen.getByLabelText('规则版本'))
    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))

    expect(await screen.findByText('请输入规则版本')).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: '策略效果明细' })).not.toBeInTheDocument()
    expect(fetchMock.mock.calls).toHaveLength(callsBeforeClear)
  })

  it('规则版本先编辑草稿，应用后才发起一组新请求', async () => {
    const fetchMock = normalFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findByRole('table', { name: '策略效果明细' })
    const callsBeforeEdit = fetchMock.mock.calls.length

    await userEvent.clear(screen.getByLabelText('规则版本'))
    await userEvent.type(screen.getByLabelText('规则版本'), 'rules-next')
    expect(fetchMock.mock.calls).toHaveLength(callsBeforeEdit)

    await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
    await waitFor(() => {
      const outcomeCalls = fetchMock.mock.calls.filter(([input]) =>
        String(input).includes('/strategy/outcomes'),
      )
      expect(outcomeCalls).toHaveLength(4)
      expect(outcomeCalls.slice(-2).every(([input]) =>
        String(input).includes('rule_version=rules-next'),
      )).toBe(true)
    })
  })

  it('日期逆序时不发请求并保留筛选区，修正后自动恢复', async () => {
    const fetchMock = normalFetch()
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findByRole('table', { name: '策略效果明细' })

    await userEvent.type(screen.getByLabelText('结束日期'), '2026-08-01')
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) =>
      String(input).includes('date_to=2026-08-01'),
    )).toBe(true))
    const callsBeforeInvalidRange = fetchMock.mock.calls.length
    await userEvent.type(screen.getByLabelText('开始日期'), '2026-08-02')

    expect(await screen.findByRole('alert')).toHaveTextContent('开始日期不能晚于结束日期')
    expect(screen.getByLabelText('开始日期')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetchMock.mock.calls).toHaveLength(callsBeforeInvalidRange)

    await userEvent.clear(screen.getByLabelText('结束日期'))
    await userEvent.type(screen.getByLabelText('结束日期'), '2026-08-03')
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = String(input)
      return url.includes('date_from=2026-08-02') && url.includes('date_to=2026-08-03')
    })).toBe(true))
    expect(screen.queryByText('开始日期不能晚于结束日期')).not.toBeInTheDocument()
  })

  it('筛选 key 变更时通过 AbortSignal 取消旧的列表和汇总请求', async () => {
    const aborted: string[] = []
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/system/status')) return Promise.resolve(Response.json(activeStatus))
      if (url.includes('horizon=3')) {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            aborted.push(url)
            reject(new DOMException('Aborted', 'AbortError'))
          })
        })
      }
      if (url.includes('/strategy/outcomes/summary?')) return Promise.resolve(Response.json(summary))
      return Promise.resolve(Response.json({
        items: [item], total: 1, page: 1, page_size: 20,
        calculation_version: 'outcome-v1', filters: {}, data_date: '2026-08-31',
      }))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findByRole('table', { name: '策略效果明细' })

    await userEvent.selectOptions(screen.getByLabelText('评价周期'), '3')
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) =>
      String(input).includes('horizon=3'),
    )).toHaveLength(2))
    await userEvent.selectOptions(screen.getByLabelText('评价周期'), '1')

    await waitFor(() => expect(aborted).toHaveLength(2))
  })

  it('空数值显示占位符且不出现胜率', async () => {
    vi.stubGlobal('fetch', normalFetch({
      summary: { ...summary, positive_return_ratio: null, mean_return_rate: null, median_return_rate: null, mean_mfe: null, mean_mae: null, max_drawdown_approx: null },
      items: [{ ...item, reference_price: null, evaluation_price: null, return_rate: null, mfe: null, mae: null }],
    }))
    renderPage()
    expect((await screen.findAllByText('--')).length).toBeGreaterThan(4)
    expect(within(screen.getByRole('article', { name: '最大回撤近似值' })).getByText('--')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('胜率')
  })
})
