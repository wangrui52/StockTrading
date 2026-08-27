import { cleanup, render, screen, waitFor } from '@testing-library/react'
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
  it.each([
    ['浦发银行', '浦发银行 · SH'],
    [null, '名称暂缺 · SH'],
    [undefined, '名称暂缺 · SH'],
    ['', '名称暂缺 · SH'],
  ])('候选股同时展示代码和名称，名称为 %s', async (stockName, expected) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      return Response.json({ ...dashboard, candidates: [{ ...dashboard.candidates[0], stock_name: stockName }] })
    }))

    render(<App />)

    expect(await screen.findByText(expected)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '600000' })).toHaveAttribute('href', '/stocks/SH/600000')
  })

  it('将候选股命中原因显示为中文，并保留未知原因', async () => {
    const reasons = ['BREAKOUT_MA20_WITH_VOLUME', 'MA5_ABOVE_MA20', 'MACD_GOLDEN_CROSS', 'PRICE_ABOVE_MA20', 'RSI_IN_CANDIDATE_RANGE', 'FUTURE_RULE']
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      return Response.json({ ...dashboard, candidates: [{ ...dashboard.candidates[0], reasons }] })
    }))

    render(<App />)

    expect(await screen.findByRole('cell', {
      name: '放量突破20日均线 / 5日均线高于20日均线 / MACD金叉 / 收盘价高于20日均线 / RSI处于45～75区间 / FUTURE_RULE',
    })).toBeInTheDocument()
    expect(screen.queryByText(/BREAKOUT_MA20_WITH_VOLUME/)).not.toBeInTheDocument()
  })

  it('keeps a sync action for demo data and asks the backend for the latest trade date', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/sync-jobs')) return Response.json({ job_id: 8, batch_id: 8 })
      if (String(input).includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      return Response.json({ ...dashboard, source: 'demo-v1' })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByText('演示数据（非真实行情）')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '同步最新交易日' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/sync-jobs', expect.objectContaining({
      method: 'POST', body: '{}',
    }))
  })

  it('refreshes dashboard automatically when the background job activates a new batch', async () => {
    let submitted = false
    let statusPolls = 0
    let ready = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/sync-jobs')) { submitted = true; return Response.json({ job_id: 8, batch_id: 8 }) }
      if (url.endsWith('/system/status')) {
        if (submitted) ready = ++statusPolls >= 2
        return Response.json({
          active_batch: { ...dashboard, batch_id: ready ? 8 : 7 },
          latest_sync: { id: 8, batch_id: 8, status: submitted && !ready ? 'FETCHING' : 'READY',
            completed_count: 0, failed_count: 0, failed_items: [] },
        })
      }
      if (url.includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      return Response.json({ ...dashboard, batch_id: ready ? 8 : 7,
        trade_date: ready ? '2026-08-27' : '2025-03-31' })
    }))
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: '同步最新交易日' }))
    await waitFor(() => expect(screen.getByText('交易日 2026-08-27')).toBeInTheDocument(), { timeout: 5000 })
  })

  it('shows sync failure details and enables retry without hiding the old batch', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      if (String(input).endsWith('/sync-jobs')) return Response.json({ error: {
        code: 'MARKET_DATA_UNAVAILABLE', message: '交易日历暂时不可用',
      } }, { status: 503 })
      return Response.json(dashboard)
    }))
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: '同步最新交易日' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('交易日历暂时不可用')
    expect(screen.getByText('交易日 2025-03-31')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '同步最新交易日' })).toBeEnabled()
  })

  it('renders loading then the active batch dashboard without calling it realtime data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/decision-notes')) return Response.json({ items: [] })
        if (url.includes('/alerts?')) {
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
        return Response.json({ trade_date: '2025-03-31', batch_id: 7, rule_version: 'v1', market: 'SH', stock_code: '600000', stock_name: '浦发银行', industry: '银行', trend: '偏强', risk_level: 'low', risk_reasons: [], price: { trade_date: '2025-03-31', adjustment: 'raw', open: 10, high: 11, low: 9, close: 10.2, volume: 100, amount: 1000, pct_change: 2, turnover_rate: 1 } })
      }),
    )

    render(<App />)

    expect(await screen.findByRole('heading', { name: '浦发银行' })).toBeInTheDocument()
    expect(screen.getByText(/MACD_GOLDEN_CROSS/)).toBeInTheDocument()
    expect(screen.getByLabelText('K线与技术指标图')).toBeInTheDocument()
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
      if (url.includes('/alerts?') && !init?.method) return Response.json({ ...dashboard, items: [{ id: 5, market: 'SH', stock_code: '600000', trade_date: '2025-03-31', rule_code: 'MACD_GOLDEN_CROSS', payload: {}, status: 'TRIGGERED' }] })
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

  it('updates P1 schedule and creates a confirmed rule version', async () => {
    const settings = { auto_sync_enabled: true, auto_sync_time: '18:30', adapter_version: 'akshare-1.18.94', current_rule_version: 'v1', indicator_parameters: { rsi_period: 14, boll_period: 20 }, last_successful_batch: '2025-03-31', completeness_rate: 1, failed_jobs: [] }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/settings')) return Response.json(init?.method === 'PATCH' ? { ...settings, auto_sync_enabled: false, auto_sync_time: '19:00' } : settings)
      if (url.endsWith('/alert-rules')) return Response.json({ items: [] })
      if (url.endsWith('/rule-versions')) return Response.json({ id: 1, version: 'v2', parameters: { rsi_period: 12 }, requires_recalculation: true })
      return Response.json(dashboard)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('link', { name: '系统设置' }))
    expect(await screen.findByText('数据源 akshare-1.18.94')).toBeInTheDocument()
    await user.click(screen.getByLabelText('启用交易日自动同步'))
    await user.clear(screen.getByLabelText('执行时间'))
    await user.type(screen.getByLabelText('执行时间'), '19:00')
    await user.click(screen.getByRole('button', { name: '保存同步设置' }))
    expect(await screen.findByText('同步设置已保存。')).toBeInTheDocument()
    await user.clear(screen.getByLabelText('RSI 周期'))
    await user.type(screen.getByLabelText('RSI 周期'), '12')
    await user.click(screen.getByLabelText('确认触发重算'))
    await user.click(screen.getByRole('button', { name: '创建规则版本' }))
    expect(await screen.findByText('新规则版本已创建，等待重算。')).toBeInTheDocument()
  })

  it('manages advanced screener conditions and saved presets', async () => {
    const presetItem = { id: 3, name: '强势方案', conditions: { close_above_ma20: true }, is_default: false }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/screener-presets') && !init?.method) return Response.json({ items: [presetItem] })
      if (url.includes('/screener-presets/')) return Response.json({ ...presetItem, is_default: true })
      if (url.endsWith('/screenings')) return Response.json({ ...dashboard, items: dashboard.candidates, total: 60, page: JSON.parse(String(init?.body)).page, page_size: 50 })
      return Response.json(dashboard)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('link', { name: '股票筛选' }))
    await user.click(screen.getByLabelText('SH'))
    await user.click(screen.getByLabelText('多头'))
    await user.click(screen.getByLabelText('包含 ST'))
    await user.click(screen.getByRole('button', { name: '执行筛选' }))
    await user.click(await screen.findByRole('button', { name: '下一页' }))
    await user.click(screen.getByRole('button', { name: '载入' }))
    await user.click(screen.getByRole('button', { name: '覆盖条件' }))
    await user.clear(screen.getByLabelText('方案名称'))
    await user.type(screen.getByLabelText('方案名称'), '重命名方案')
    await user.click(screen.getByRole('button', { name: '重命名' }))
    await user.click(screen.getByRole('button', { name: '设为默认' }))
    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/screener-presets/3/default', expect.objectContaining({ method: 'POST' }))
  })

  it('edits detail notes, chart range and watchlist state', async () => {
    window.history.replaceState({}, '', '/stocks/SH/600000')
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/prices')) return Response.json({ ...dashboard, items: [{ trade_date: '2025-03-31', adjustment: 'qfq', open: 10, high: 11, low: 9, close: 10.2, volume: 100, amount: 1000, pct_change: 2, turnover_rate: 1, is_suspended: false }] })
      if (url.endsWith('/indicators')) return Response.json({ ...dashboard, items: [{ trade_date: '2025-03-31', ma5: 10.1, ma20: 9.9, rsi14: 60, unavailable: [] }] })
      if (url.endsWith('/signals')) return Response.json({ ...dashboard, items: [] })
      if (url.endsWith('/decision-notes') && !init?.method) return Response.json({ items: [{ id: 9, market: 'SH', stock_code: '600000', trade_date: '2025-03-31', content: '原笔记', created_at: '2025-03-31T00:00:00Z', updated_at: '2025-03-31T00:00:00Z', deleted_at: null }] })
      if (url.endsWith('/watchlist/groups')) return Response.json({ items: [{ id: 1, name: '短线关注', sort_order: 0 }] })
      if (url.endsWith('/watchlist/items') && !init?.method) return Response.json({ items: [] })
      if (url.endsWith('/watchlist/items') && init?.method === 'POST') return Response.json({ id: 2 })
      if (url.includes('/decision-notes/')) return Response.json({})
      return Response.json({ ...dashboard, market: 'SH', stock_code: '600000', stock_name: '浦发银行', industry: '银行', trend: '偏强', risk_level: 'low', risk_reasons: [], price: { trade_date: '2025-03-31', adjustment: 'raw', open: 10, high: 11, low: 9, close: 10.2, volume: 100, amount: 1000, pct_change: 2, turnover_rate: 1, is_suspended: false } })
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)
    expect(await screen.findByRole('heading', { name: '浦发银行' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '60 日' }))
    await user.click(screen.getByRole('button', { name: '加入自选' }))
    await user.clear(screen.getByLabelText('笔记内容'))
    await user.type(screen.getByLabelText('笔记内容'), '更新后的笔记')
    await user.click(screen.getByRole('button', { name: '更新' }))
    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/decision-notes/9', expect.objectContaining({ method: 'DELETE' }))
  })

  it('creates, toggles and deletes a custom alert rule', async () => {
    const settings = { auto_sync_enabled: true, auto_sync_time: '18:30', adapter_version: 'akshare-1.18.94', current_rule_version: 'v1', indicator_parameters: { rsi_period: 14, boll_period: 20 }, last_successful_batch: '2025-03-31', completeness_rate: 1, failed_jobs: [] }
    const rule = { id: 4, logical_id: 2, version: 1, name: 'RSI 规则', rule_code: 'CUSTOM_RSI', threshold: 80, enabled: true }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/settings')) return Response.json(settings)
      if (url.endsWith('/alert-rules') && !init?.method) return Response.json({ items: [rule] })
      if (url.includes('/alert-rules')) return Response.json(rule)
      return Response.json(dashboard)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('link', { name: '系统设置' }))
    await user.clear(await screen.findByLabelText('规则名称'))
    await user.type(screen.getByLabelText('规则名称'), '量比规则')
    await user.clear(screen.getByLabelText('规则代码'))
    await user.type(screen.getByLabelText('规则代码'), 'CUSTOM_VOLUME_RATIO')
    await user.click(screen.getByRole('button', { name: '新增规则' }))
    await user.click(screen.getByRole('button', { name: '停用' }))
    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/alert-rules/2', expect.objectContaining({ method: 'DELETE' }))
  })
})
