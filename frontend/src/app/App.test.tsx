import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
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

const realtimeSnapshot = {
  refresh_id: 1, source: 'tencent-realtime-v1', quote_date: '2026-08-28',
  started_at: '2026-08-28T11:09:45+08:00', finished_at: '2026-08-28T11:10:00+08:00',
  total_count: 5550, received_count: 5550, missing_count: 0, missing_symbols: [],
  stale_count: 0, unavailable_count: 0,
  market_summary: { up: 3000, down: 2000, flat: 550, amount: 123400000000 },
}
const realtimeRow = {
  market: 'SH', stock_code: '600000', stock_name: '浦发银行', latest_price: 9.01,
  pct_change: -0.66, volume: 39183100, amount: 352220162, quoted_at: '2026-08-28T11:09:52+08:00',
}
const realtimeJob = {
  id: 1, status: 'READY', stage: 'READY', total_count: 5550, completed_count: 5550,
  failed_count: 0, error_summary: null, started_at: realtimeSnapshot.started_at,
  finished_at: realtimeSnapshot.finished_at,
}

const watchedStock = {
  id: 1, market: 'SH', stock_code: '600000', stock_name: '浦发银行', group_id: 1,
  group_name: '默认', close: 10.2, pct_change: 2, trade_date: '2026-08-27',
  signal_codes: ['MACD_GOLDEN_CROSS'], risk_level: 'low', alert_status: 'UNTRIGGERED',
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.replaceState({}, '', '/')
})

describe('App', () => {
  it('主导航可进入策略效果页面', async () => {
    window.history.replaceState({}, '', '/strategy-effect')
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ active_batch: null, latest_sync: null })))
    render(<App />)

    expect(screen.getByRole('link', { name: '策略效果' })).toHaveClass('active')
    expect(await screen.findByText('当前没有可用的生产数据批次')).toBeInTheDocument()
  })

  it('自选刷新使用自选范围并更新最新价，日线信号保持不变', async () => {
    window.history.replaceState({}, '', '/watchlist')
    let updated = false
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/realtime/refresh?scope=watchlist')) {
        updated = true
        return Response.json({ ...realtimeJob, scope: 'watchlist', status: 'FETCHING' })
      }
      if (url.endsWith('/realtime/status?scope=watchlist')) return Response.json({
        job: updated ? realtimeJob : null, snapshot: updated ? { ...realtimeSnapshot, total_count: 1, received_count: 1 } : null,
        cooldown_until: null,
      })
      if (url.endsWith('/watchlist/items')) return Response.json({
        items: [{ ...watchedStock, realtime: updated ? realtimeRow : null }],
      })
      return Response.json({ items: [{ id: 1, name: '默认' }] })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByText('10.20')).toBeInTheDocument()
    expect(screen.getByText('日线参考')).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: '刷新自选股行情' }))
    expect(await screen.findByText('9.01')).toBeInTheDocument()
    expect(screen.getByText('-0.66%')).toBeInTheDocument()
    expect(screen.getByText('2026-08-28 11:09:52')).toBeInTheDocument()
    expect(screen.getByText('MACD_GOLDEN_CROSS')).toBeInTheDocument()
    expect(screen.getByText('日线 2026-08-27')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/realtime/refresh?scope=watchlist', expect.objectContaining({ method: 'POST' }))
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/realtime/refresh'))).toBe(false)
  })

  it('自选刷新失败保留旧报价，缺报价的新增股票仍标为日线参考', async () => {
    window.history.replaceState({}, '', '/watchlist')
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/realtime/status?scope=watchlist')) return Response.json({
        job: { ...realtimeJob, status: 'FAILED', error_summary: '自选行情获取失败' },
        snapshot: realtimeSnapshot, cooldown_until: null,
      })
      if (String(input).endsWith('/watchlist/items')) return Response.json({ items: [
        { ...watchedStock, realtime: { ...realtimeRow, quoted_at: '2025-03-31T15:00:00+08:00' } },
        { ...watchedStock, id: 2, stock_code: '600001', realtime: null },
      ] })
      return Response.json({ items: [] })
    }))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('自选行情获取失败')
    expect(await screen.findByText('9.01')).toBeInTheDocument()
    expect(screen.getByText('非今日报价')).toBeInTheDocument()
    expect(screen.getByText('10.20')).toBeInTheDocument()
    expect(screen.getByText('日线参考')).toBeInTheDocument()
  })

  it('空自选列表禁用行情刷新', async () => {
    window.history.replaceState({}, '', '/watchlist')
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ items: [] })))
    render(<App />)
    expect(await screen.findByText('尚未添加自选股。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新自选股行情' })).toBeDisabled()
  })

  it('刷新全市场并展示来源时间，搜索和翻页不触发日线同步', async () => {
    let submitted = false
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/realtime/refresh')) {
        submitted = true
        return Response.json({ ...realtimeJob, status: 'FETCHING', stage: 'STOCKS' })
      }
      if (url.endsWith('/realtime/status')) return Response.json({
        job: submitted ? realtimeJob : null, snapshot: submitted ? realtimeSnapshot : null, cooldown_until: null,
      })
      if (url.includes('/realtime/quotes?')) return Response.json({
        snapshot: realtimeSnapshot, items: [realtimeRow], total: 5550, page: 1, page_size: 50,
      })
      if (url.includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      return Response.json(dashboard)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    const button = await screen.findByRole('button', { name: '刷新实时行情' })
    await waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    expect(await screen.findByText('覆盖 5550/5550 只')).toBeInTheDocument()
    const table = await screen.findByRole('table', { name: '全市场报价列表' })
    expect(within(table).getByText('2026-08-28 11:09:52')).toBeInTheDocument()
    expect(within(table).getByText('9.01')).toBeInTheDocument()
    expect(screen.getByText('交易日 2025-03-31')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '下一页报价' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/realtime/quotes?page=2&page_size=50&q=', expect.anything()))
    await userEvent.type(screen.getByLabelText('实时行情搜索'), '600000')
    await userEvent.click(screen.getByRole('button', { name: '查询报价' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/realtime/quotes?page=1&page_size=50&q=600000', expect.anything()))
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/sync-jobs'))).toBe(false)
  })

  it('实时刷新失败保留旧快照，旧报价不会被标为当前报价', async () => {
    let failed = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/realtime/refresh')) {
        failed = true
        return Response.json({ ...realtimeJob, id: 2, status: 'FETCHING' })
      }
      if (url.endsWith('/realtime/status')) return Response.json({
        job: failed ? { ...realtimeJob, id: 2, status: 'FAILED', error_summary: '数据源暂不可用' } : realtimeJob,
        snapshot: realtimeSnapshot, cooldown_until: null,
      })
      if (url.includes('/realtime/quotes?')) return Response.json({
        snapshot: realtimeSnapshot, items: [{ ...realtimeRow, quoted_at: '2025-03-31T15:00:00+08:00' }],
        total: 1, page: 1, page_size: 50,
      })
      if (url.includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      return Response.json(dashboard)
    }))
    render(<App />)
    expect(await screen.findByText('非今日报价')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '刷新实时行情' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('数据源暂不可用。当前展示上次成功快照')
    expect(screen.getByText('9.01')).toBeInTheDocument()
    expect(screen.getByText('2025-03-31 15:00:00')).toBeInTheDocument()
  })

  it('没有日线数据也能刷新实时行情，采集中禁止重复点击', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/realtime/status')) return Response.json({
        job: { ...realtimeJob, status: 'FETCHING', stage: 'QUOTES', completed_count: 100 },
        snapshot: null, cooldown_until: null,
      })
      return Response.json({ error: { code: 'NO_ACTIVE_BATCH', message: '当前没有可用数据批次' } }, { status: 409 })
    }))
    render(<App />)
    expect(await screen.findByText('尚无有效数据批次')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '正在刷新实时行情…' })).toBeDisabled()
    expect(screen.getByText('采集报价 100/5550（2%）')).toBeInTheDocument()
  })

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

  it('候选股列表展示四种后续表现状态，未知状态安全回退', async () => {
    const candidates = [
      { ...dashboard.candidates[0], stock_code: '600000', outcome_status: 'PENDING' },
      { ...dashboard.candidates[0], stock_code: '600001', outcome_status: 'PARTIAL' },
      { ...dashboard.candidates[0], stock_code: '600002', outcome_status: 'COMPLETED' },
      { ...dashboard.candidates[0], stock_code: '600003', outcome_status: 'UNAVAILABLE' },
      { ...dashboard.candidates[0], stock_code: '600004', outcome_status: 'FUTURE_STATUS' },
    ]
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      return Response.json({ ...dashboard, candidates })
    }))

    render(<App />)

    const table = await screen.findByRole('table')
    expect(within(table).getByRole('columnheader', { name: '后续表现' })).toBeInTheDocument()
    expect(within(table).getByText('待评价')).toHaveClass('status-tag')
    expect(within(table).getByText('部分完成')).toHaveClass('status-tag', 'warning')
    expect(within(table).getByText('已评价')).toHaveClass('status-tag', 'success')
    expect(within(table).getByText('数据缺失')).toHaveClass('status-tag', 'danger')
    expect(within(table).getByText('FUTURE_STATUS')).toHaveClass('status-tag', 'warning')
    expect(within(table).queryByText('状态未知')).not.toBeInTheDocument()
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

  it('同步期间跨页导航仍持续检测新批次并刷新策略数据', async () => {
    let submitted = false
    let statusPolls = 0
    let activeBatch = 7
    const strategyCalls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/sync-jobs')) {
        submitted = true
        return Response.json({ job_id: 8, batch_id: 8 })
      }
      if (url.endsWith('/system/status')) {
        if (submitted && ++statusPolls >= 2) activeBatch = 8
        return Response.json({
          active_batch: { batch_id: activeBatch, trade_date: activeBatch === 8 ? '2026-09-01' : '2026-08-31', rule_version: 'v1' },
          latest_sync: submitted && activeBatch === 7
            ? { id: 8, batch_id: 8, status: 'FETCHING', stage: 'FETCHING', completed_count: 0, failed_count: 0, failed_items: [] }
            : { id: 8, batch_id: 8, status: 'READY', stage: 'READY', completed_count: 1, failed_count: 0, failed_items: [] },
        })
      }
      if (url.includes('/strategy/outcomes')) {
        strategyCalls.push(`${activeBatch}:${url}`)
        if (url.includes('/summary?')) return Response.json({
          total: activeBatch, completed: activeBatch, unavailable: 0, pending: 0,
          sample_size: activeBatch, completion_rate: 1, mean_return_rate: 1,
          median_return_rate: 1, positive_return_ratio: 1, mean_mfe: 1, mean_mae: -1,
          insufficient_sample: true, calculation_version: 'outcome-v1', filters: {},
          data_date: activeBatch === 8 ? '2026-09-01' : '2026-08-31',
        })
        return Response.json({
          items: [], total: activeBatch, page: 1, page_size: 20,
          calculation_version: 'outcome-v1', filters: {},
          data_date: activeBatch === 8 ? '2026-09-01' : '2026-08-31',
        })
      }
      if (url.includes('/alerts?')) return Response.json({ ...dashboard, items: [] })
      return Response.json({ ...dashboard, batch_id: activeBatch })
    }))
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: '同步最新交易日' }))
    await userEvent.click(screen.getByRole('link', { name: '策略效果' }))

    expect(await screen.findByText('数据日期 2026-09-01', {}, { timeout: 5000 })).toBeInTheDocument()
    expect(strategyCalls.some((call) => call.startsWith('7:'))).toBe(true)
    expect(strategyCalls.some((call) => call.startsWith('8:'))).toBe(true)
  })

  it('新批次刷新个股 PENDING 评价，分别展示预计日和等待日历', async () => {
    window.history.replaceState({}, '', '/stocks/SH/600000')
    let statusPolls = 0
    let activeBatch = 7
    const detailBatches: number[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/system/status')) {
        if (++statusPolls >= 2) activeBatch = 8
        return Response.json({
          active_batch: { batch_id: activeBatch, trade_date: activeBatch === 8 ? '2026-09-01' : '2026-08-31', rule_version: 'v1' },
          latest_sync: activeBatch === 7
            ? { id: 8, batch_id: 8, status: 'FETCHING', stage: 'FETCHING', completed_count: 0, failed_count: 0, failed_items: [] }
            : { id: 8, batch_id: 8, status: 'READY', stage: 'READY', completed_count: 1, failed_count: 0, failed_items: [] },
        })
      }
      if (url.endsWith('/prices')) return Response.json({ items: [{ trade_date: '2026-08-31', adjustment: 'raw', open: 10, high: 11, low: 9, close: 10, volume: 1, amount: 1 }] })
      if (url.endsWith('/indicators') || url.endsWith('/signals') || url.endsWith('/decision-notes') || url.endsWith('/watchlist/items') || url.endsWith('/watchlist/groups')) return Response.json({ items: [] })
      if (url.endsWith('/stocks/SH/600000')) {
        detailBatches.push(activeBatch)
        return Response.json({
          trade_date: activeBatch === 8 ? '2026-09-01' : '2026-08-31', batch_id: activeBatch,
          rule_version: 'v1', market: 'SH', stock_code: '600000', stock_name: '浦发银行',
          industry: '银行', trend: '偏强', risk_level: 'low', risk_reasons: [], price: null,
          candidate_outcomes: [{
            horizon_trading_days: 1, status: 'PENDING', reference_trade_date: '2026-09-01',
            evaluation_trade_date: null,
            expected_evaluation_trade_date: activeBatch === 7 ? '2026-09-02' : null,
            reference_price: 10, evaluation_price: null,
            return_rate: null, mfe: null, mae: null, unavailable_reason: null,
            calculation_version: 'outcome-v1',
          }],
        })
      }
      return Response.json({ items: [] })
    }))
    render(<App />)

    expect(await screen.findByText('预计 2026-09-02')).toBeInTheDocument()
    expect(await screen.findByText('等待交易日历更新', {}, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.queryByText('预计 2026-09-02')).not.toBeInTheDocument()
    expect(detailBatches).toEqual([7, 8])
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

  it('renders the daily batch separately from the realtime refresh action', async () => {
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
    expect(screen.getByRole('button', { name: '刷新实时行情' })).toBeInTheDocument()
    expect(screen.getByText(/指标、筛选和报告仍使用收盘日线/)).toBeInTheDocument()
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

    expect(await screen.findByRole('heading', { name: '数据读取失败' })).toBeInTheDocument()
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
        return Response.json({ trade_date: '2025-03-31', batch_id: 7, rule_version: 'v1', market: 'SH', stock_code: '600000', stock_name: '浦发银行', industry: '银行', trend: '偏强', risk_level: 'low', risk_reasons: [], candidate_outcomes: [], price: { trade_date: '2025-03-31', adjustment: 'raw', open: 10, high: 11, low: 9, close: 10.2, volume: 100, amount: 1000, pct_change: 2, turnover_rate: 1 } })
      }),
    )

    render(<App />)

    expect(await screen.findByRole('heading', { name: '浦发银行' })).toBeInTheDocument()
    expect(screen.getByText(/MACD_GOLDEN_CROSS/)).toBeInTheDocument()
    expect(screen.getByLabelText('K线与技术指标图')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '后续表现' })).toBeInTheDocument()
    expect(screen.getByText('当前批次未入选候选或尚未生成评价记录')).toBeInTheDocument()
  })

  it('个股详情按接口顺序展示三个周期的后续表现，未知状态保留原值', async () => {
    window.history.replaceState({}, '', '/stocks/SH/600000')
    const stockDetail = {
      trade_date: '2025-03-31', batch_id: 7, rule_version: 'v1', market: 'SH',
      stock_code: '600000', stock_name: '浦发银行', industry: '银行', trend: '偏强',
      risk_level: 'low', risk_reasons: [], price: null,
      candidate_outcomes: [
        { horizon_trading_days: 5, status: 'UNAVAILABLE', reference_trade_date: null,
          evaluation_trade_date: null, reference_price: null, evaluation_price: null,
          return_rate: null, mfe: null, mae: null, unavailable_reason: 'PRICE_DATA_MISSING',
          calculation_version: 'outcome-v1' },
        { horizon_trading_days: 1, status: 'COMPLETED', reference_trade_date: '2025-04-01',
          evaluation_trade_date: '2025-04-01', reference_price: 10, evaluation_price: 10.5,
          return_rate: 5.25, mfe: 6.2, mae: -1.3, unavailable_reason: null,
          calculation_version: 'outcome-v1' },
        { horizon_trading_days: 3, status: 'FUTURE_OUTCOME', reference_trade_date: '2025-04-01',
          evaluation_trade_date: null, reference_price: 10, evaluation_price: null,
          return_rate: null, mfe: null, mae: null, unavailable_reason: null,
          calculation_version: 'outcome-v1' },
      ],
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prices')) return Response.json({ items: [{ trade_date: '2025-03-31', adjustment: 'raw', open: 10, high: 11, low: 9, close: 10.2, volume: 100, amount: 1000 }] })
      if (url.endsWith('/indicators')) return Response.json({ items: [] })
      if (url.endsWith('/signals')) return Response.json({ items: [] })
      if (url.endsWith('/stocks/SH/600000')) return Response.json(stockDetail)
      return Response.json({ items: [] })
    }))

    render(<App />)

    expect(await screen.findByRole('heading', { name: '后续表现' })).toBeInTheDocument()
    const outcomeRows = within(screen.getByRole('table', { name: '后续表现明细' })).getAllByRole('row').slice(1)
    expect(outcomeRows).toHaveLength(3)
    expect(within(outcomeRows[0]).getByText('T+5')).toBeInTheDocument()
    expect(within(outcomeRows[1]).getByText('T+1')).toBeInTheDocument()
    expect(within(outcomeRows[2]).getByText('T+3')).toBeInTheDocument()
    expect(within(outcomeRows[0]).getAllByText('--').length).toBeGreaterThan(0)
    expect(within(outcomeRows[0]).getByText('PRICE_DATA_MISSING')).toBeInTheDocument()
    expect(within(outcomeRows[1]).getByText('2025-04-01 · 10.00')).toBeInTheDocument()
    expect(within(outcomeRows[1]).getByText('2025-04-01 · 10.50')).toBeInTheDocument()
    expect(within(outcomeRows[1]).getByText('5.25%')).toHaveClass('rise')
    expect(within(outcomeRows[1]).getByText('6.20%')).toHaveClass('rise')
    expect(within(outcomeRows[1]).getByText('-1.30%')).toHaveClass('fall')
    expect(within(outcomeRows[2]).getByText('FUTURE_OUTCOME')).toHaveClass('status-tag', 'warning')
    expect(screen.getByText('不复权价格，参考价为候选后首个有效交易日开盘价；仅供研究')).toBeInTheDocument()
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
