import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'

vi.mock('../features/stock-detail/chart', () => ({
  initPriceChart: () => ({ setOption: vi.fn(), dispose: vi.fn() }),
}))

const dashboard = {
  trade_date: '2025-03-31',
  batch_id: 7,
  rule_version: 'v1',
  completeness_rate: 1,
  candidates: [
    {
      market: 'SH',
      stock_code: '600000',
      score: 4,
      reasons: ['MACD_GOLDEN_CROSS'],
    },
  ],
  market_summary: { up: 1200, down: 900, flat: 50, amount: 100000000 },
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.replaceState({}, '', '/')
})

describe('App', () => {
  it('renders loading then the active batch dashboard without calling it realtime data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/alerts')) {
          return Response.json({ ...dashboard, items: [] })
        }
        return Response.json(dashboard)
      }),
    )

    render(<App />)

    expect(screen.getByRole('status')).toHaveTextContent('正在读取有效数据批次')
    expect(await screen.findByText('600000')).toBeInTheDocument()
    expect(screen.getByText('交易日 2025-03-31')).toBeInTheDocument()
    expect(screen.getByText('历史数据')).toBeInTheDocument()
    expect(screen.queryByText(/实时/)).not.toBeInTheDocument()
  })

  it('shows the sync guide for the explicit no-active-batch error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json(
          { error: { code: 'NO_ACTIVE_BATCH', message: '当前没有可用数据批次', details: null } },
          { status: 409 },
        ),
      ),
    )

    render(<App />)

    expect(await screen.findByText('尚无有效数据批次')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '同步数据' })).toBeEnabled()
  })

  it('shows an independent error state and retry action', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({}, { status: 500 })))

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('数据读取失败')
    expect(screen.getByRole('button', { name: '重新加载' })).toBeEnabled()
  })

  it('provides navigable P0 work areas', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(dashboard)))
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('link', { name: '股票筛选' }))
    expect(screen.getByRole('heading', { name: '股票筛选' })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: '自选股' }))
    expect(screen.getByRole('heading', { name: '自选股' })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: '分析报告' }))
    expect(screen.getByRole('heading', { name: '分析报告' })).toBeInTheDocument()
  })

  it('renders stock detail series and initializes the chart', async () => {
    window.history.replaceState({}, '', '/stocks/SH/600000')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/prices')) return Response.json({ trade_date: '2025-03-31', batch_id: 7, rule_version: 'v1', items: [{ trade_date: '2025-03-31', adjustment: 'raw', open: 10, high: 11, low: 9, close: 10.2, volume: 100, amount: 1000, pct_change: 2, turnover_rate: 1 }] })
        if (url.endsWith('/indicators')) return Response.json({ trade_date: '2025-03-31', batch_id: 7, rule_version: 'v1', items: [{ trade_date: '2025-03-31', ma5: 10.1, ma20: 9.9, rsi14: 60 }] })
        if (url.endsWith('/signals')) return Response.json({ trade_date: '2025-03-31', batch_id: 7, rule_version: 'v1', items: [{ id: 1, market: 'SH', stock_code: '600000', trade_date: '2025-03-31', rule_code: 'MACD_GOLDEN_CROSS', payload: {} }] })
        return Response.json({ trade_date: '2025-03-31', batch_id: 7, rule_version: 'v1', market: 'SH', stock_code: '600000', stock_name: '浦发银行', price: { trade_date: '2025-03-31', adjustment: 'raw', open: 10, high: 11, low: 9, close: 10.2, volume: 100, amount: 1000, pct_change: 2, turnover_rate: 1 } })
      }),
    )

    render(<App />)

    expect(await screen.findByRole('heading', { name: '浦发银行' })).toBeInTheDocument()
    expect(screen.getByText(/MACD_GOLDEN_CROSS/)).toBeInTheDocument()
    expect(screen.getByLabelText('日线收盘价图')).toBeInTheDocument()
  })

  it('submits screening conditions and renders deterministic results', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/screenings')) return Response.json({ ...dashboard, items: dashboard.candidates })
      return Response.json(dashboard)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('link', { name: '股票筛选' }))
    await user.clear(screen.getByLabelText('最低得分'))
    await user.type(screen.getByLabelText('最低得分'), '3')
    await user.click(screen.getByRole('button', { name: '执行筛选' }))

    expect(await screen.findByText('筛选结果 · 交易日 2025-03-31')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/screenings', expect.objectContaining({ method: 'POST' }))
  })

  it('submits watchlist and report commands', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/watchlist/items') && init?.method === 'POST') return Response.json({ id: 1, group_id: 1, market: 'SH', stock_code: '600000', note: null })
      if (url.endsWith('/watchlist/items')) return Response.json({ items: [] })
      if (url.endsWith('/reports')) return Response.json({ id: 2, trade_date: '2025-03-31', batch_id: 7, rule_version: 'v1', market: 'SH', stock_code: '600000', template_version: 'v1', report_version: 1, content: '不构成投资建议' })
      return Response.json(dashboard)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('link', { name: '自选股' }))
    await user.type(screen.getByLabelText('股票代码'), '600000')
    await user.click(screen.getByRole('button', { name: '加入自选' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/watchlist/items', expect.objectContaining({ method: 'POST' }))

    await user.click(screen.getByRole('link', { name: '分析报告' }))
    await user.type(screen.getByLabelText('股票代码'), '600000')
    await user.click(screen.getByRole('button', { name: '生成报告' }))
    expect(await screen.findByText('不构成投资建议')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '导出 Markdown' })).toHaveAttribute('href', '/api/v1/reports/2/export')
  })

  it('confirms a dashboard alert without losing the active context', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/alerts') && !init?.method) return Response.json({ ...dashboard, items: [{ id: 5, market: 'SH', stock_code: '600000', trade_date: '2025-03-31', rule_code: 'MACD_GOLDEN_CROSS', payload: {}, status: 'TRIGGERED' }] })
      if (url.endsWith('/alerts/5/confirm')) return Response.json({ id: 5, status: 'CONFIRMED', confirmed_at: '2025-04-01T00:00:00Z' })
      return Response.json(dashboard)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: '确认提醒' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/5/confirm', expect.objectContaining({ method: 'POST' }))
    expect(screen.getByText('交易日 2025-03-31')).toBeInTheDocument()
  })
})
