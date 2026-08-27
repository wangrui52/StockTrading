import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'

import { APIError, request, type AlertList, type Dashboard, type SystemStatus } from '../../shared/api/client'

const candidateReasonLabels = new Map([
  ['BREAKOUT_MA20_WITH_VOLUME', '放量突破20日均线'],
  ['MA5_ABOVE_MA20', '5日均线高于20日均线'],
  ['MACD_GOLDEN_CROSS', 'MACD金叉'],
  ['PRICE_ABOVE_MA20', '收盘价高于20日均线'],
  ['RSI_IN_CANDIDATE_RANGE', 'RSI处于45～75区间'],
])

function isStale(tradeDate: string) {
  const elapsed = Date.now() - new Date(`${tradeDate}T15:00:00+08:00`).getTime()
  return elapsed > 4 * 24 * 60 * 60 * 1000
}

export function DashboardPage() {
  const queryClient = useQueryClient()
  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => request<Dashboard>('/dashboard'),
  })
  const systemStatus = useQuery({
    queryKey: ['system-status'],
    queryFn: () => request<SystemStatus>('/system/status'),
    refetchInterval: (query) => {
      const value = query.state.data?.latest_sync?.status
      return value && !['READY', 'FAILED'].includes(value) ? 2000 : 10000
    },
  })
  const previousBatch = useRef<number | null | undefined>(undefined)
  const activeBatchId = systemStatus.data?.active_batch?.batch_id ?? null
  useEffect(() => {
    if (!systemStatus.isSuccess) return
    if (previousBatch.current !== undefined && previousBatch.current !== activeBatchId) {
      queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'system-status' })
    }
    previousBatch.current = activeBatchId
  }, [activeBatchId, systemStatus.isSuccess, queryClient])
  const alerts = useQuery({
    queryKey: ['alerts'],
    queryFn: () => request<AlertList>('/alerts?limit=10&watchlist_only=true'),
    enabled: dashboard.isSuccess,
  })
  const sync = useMutation({
    mutationFn: () =>
      request('/sync-jobs', {
        method: 'POST',
        body: '{}',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['system-status'] })
    },
  })
  const confirmAlert = useMutation({
    mutationFn: (id: number) => request(`/alerts/${id}/confirm`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })
  const latestSync = systemStatus.data?.latest_sync
  const syncing = sync.isPending || Boolean(latestSync && !['READY', 'FAILED'].includes(latestSync.status))
  const syncFeedback = <>
    {sync.isError && <p role="alert">{sync.error.message}</p>}
    {latestSync && latestSync.status !== 'READY' && <section className={`context-strip ${latestSync.status === 'FAILED' ? 'warning' : ''}`}>
      <strong>{latestSync.status === 'FAILED' ? '同步失败，可重试' : '正在同步最新交易日'}</strong>
      <span>阶段 {latestSync.stage}</span>
      <span>完成 {latestSync.completed_count} / 失败 {latestSync.failed_count}</span>
      {latestSync.failed_items.length > 0 && <span>失败股票 {latestSync.failed_items.slice(0, 8).join('、')}</span>}
      {latestSync.error_summary && <span>{latestSync.error_summary}</span>}
    </section>}
  </>

  if (dashboard.isPending) {
    return <section className="state-card" role="status">正在读取有效数据批次…</section>
  }
  if (dashboard.error instanceof APIError && dashboard.error.code === 'NO_ACTIVE_BATCH') {
    return (
      <section className="state-card empty-state">
        <p className="eyebrow">数据状态</p>
        <h2>尚无有效数据批次</h2>
        <p>同步完成后，这里会展示市场概览、候选股和自选异动。</p>
        <button type="button" disabled={syncing} onClick={() => sync.mutate()}>
          {syncing ? '同步中…' : '同步数据'}
        </button>
        {syncFeedback}
      </section>
    )
  }
  if (dashboard.isError) {
    return (
      <section className="state-card" role="alert">
        <h2>数据读取失败</h2>
        <p>上次有效数据不会被覆盖，请检查后端服务后重试。</p>
        <button type="button" onClick={() => dashboard.refetch()}>重新加载</button>
      </section>
    )
  }

  const data = dashboard.data
  const indexNames: Record<string, string> = {
    '000001': '上证指数',
    '399001': '深证成指',
    '399006': '创业板指',
    '899050': '北证 50',
  }
  return (
    <div className="page-stack">
      <section className="context-strip">
        <strong>交易日 {data.trade_date}</strong>
        <span className={data.source?.startsWith('demo') ? 'status-tag warning' : 'muted'}>
          {data.source?.startsWith('demo') ? '演示数据（非真实行情）' : `行情来源 ${data.source ?? '未标记'}`}
        </span>
        {isStale(data.trade_date) && <span className="status-tag warning">历史数据</span>}
        {data.risk_acknowledged && <span className="status-tag warning">缺失数据风险已确认</span>}
        <span>批次 #{data.batch_id}</span>
        <span>规则 {data.rule_version}</span>
        <span>完整率 {(data.completeness_rate * 100).toFixed(1)}%</span>
        <button type="button" disabled={syncing} onClick={() => sync.mutate()}>
          {syncing ? '同步中…' : '同步最新交易日'}
        </button>
      </section>
      {syncFeedback}
      <section className="metric-grid" aria-label="主要指数">
        {(data.indices ?? []).map((item) => <article key={item.index_code}><span>{indexNames[item.index_code] ?? item.index_code}</span><strong>{item.close.toFixed(2)}</strong><small className={(item.pct_change ?? 0) >= 0 ? 'rise' : 'fall'}>{item.pct_change == null ? '--' : `${item.pct_change.toFixed(2)}%`}</small>{item.trade_date !== data.trade_date && <small className="muted">旧数据 {item.trade_date}</small>}</article>)}
      </section>
      {data.market_summary ? <section className="metric-grid" aria-label="市场概览">
        <article><span>上涨</span><strong className="rise">{data.market_summary.up}</strong></article>
        <article><span>下跌</span><strong className="fall">{data.market_summary.down}</strong></article>
        <article><span>平盘</span><strong>{data.market_summary.flat}</strong></article>
        <article><span>成交额</span><strong>{(data.market_summary.amount / 100000000).toFixed(1)} 亿</strong></article>
      </section> : <section className="state-card"><strong>市场概览已隐藏</strong><p>当前批次完整率低于 99%，避免用不完整样本展示聚合统计。</p></section>}
      <section className="panel">
        <div className="panel-title"><div><p className="eyebrow">默认策略</p><h2>交易日候选股</h2></div><Link to="/screener">调整筛选</Link></div>
        {data.candidates.length === 0 ? <p className="muted">当前批次没有命中默认条件的股票。</p> : (
          <div className="table-wrap"><table><thead><tr><th>股票</th><th>得分</th><th>命中原因</th></tr></thead><tbody>
            {data.candidates.map((item) => <tr key={`${item.market}${item.stock_code}`}><td><Link to={`/stocks/${item.market}/${item.stock_code}`}>{item.stock_code}</Link><small>{item.stock_name || '名称暂缺'} · {item.market}</small></td><td>{item.score.toFixed(1)}</td><td>{item.reasons.map((reason) => candidateReasonLabels.get(reason) ?? reason).join(' / ')}</td></tr>)}
          </tbody></table></div>
        )}
      </section>
      <section className="panel">
        <div className="panel-title"><div><p className="eyebrow">待处理</p><h2>最近未确认异动</h2></div><small className="muted">最多展示 10 条</small></div>
        {alerts.isPending ? <p className="muted">正在读取提醒…</p> : alerts.data?.items.length ? alerts.data.items.map((item) => <div className="alert-row" key={item.id}><span>{item.stock_code} · {item.rule_code}</span>{item.status !== 'CONFIRMED' && <button type="button" onClick={() => confirmAlert.mutate(item.id)}>确认提醒</button>}</div>) : <p className="muted">暂无未确认异动。</p>}
      </section>
    </div>
  )
}
