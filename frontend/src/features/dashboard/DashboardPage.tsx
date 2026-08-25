import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { APIError, request, type AlertList, type Dashboard } from '../../shared/api/client'

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
  const alerts = useQuery({
    queryKey: ['alerts'],
    queryFn: () => request<AlertList>('/alerts'),
    enabled: dashboard.isSuccess,
  })
  const sync = useMutation({
    mutationFn: () =>
      request('/sync-jobs', {
        method: 'POST',
        body: JSON.stringify({ target_trade_date: new Date().toISOString().slice(0, 10) }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  })
  const confirmAlert = useMutation({
    mutationFn: (id: number) => request(`/alerts/${id}/confirm`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })

  if (dashboard.isPending) {
    return <section className="state-card" role="status">正在读取有效数据批次…</section>
  }
  if (dashboard.error instanceof APIError && dashboard.error.code === 'NO_ACTIVE_BATCH') {
    return (
      <section className="state-card empty-state">
        <p className="eyebrow">数据状态</p>
        <h2>尚无有效数据批次</h2>
        <p>同步完成后，这里会展示市场概览、候选股和自选异动。</p>
        <button type="button" disabled={sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? '同步中…' : '同步数据'}
        </button>
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
  return (
    <div className="page-stack">
      <section className="context-strip">
        <strong>交易日 {data.trade_date}</strong>
        {isStale(data.trade_date) && <span className="status-tag warning">历史数据</span>}
        <span>批次 #{data.batch_id}</span>
        <span>规则 {data.rule_version}</span>
        <span>完整率 {(data.completeness_rate * 100).toFixed(1)}%</span>
      </section>
      <section className="metric-grid" aria-label="市场概览">
        <article><span>上涨</span><strong className="rise">{data.market_summary.up}</strong></article>
        <article><span>下跌</span><strong className="fall">{data.market_summary.down}</strong></article>
        <article><span>平盘</span><strong>{data.market_summary.flat}</strong></article>
        <article><span>成交额</span><strong>{(data.market_summary.amount / 100000000).toFixed(1)} 亿</strong></article>
      </section>
      <section className="panel">
        <div className="panel-title"><div><p className="eyebrow">默认策略</p><h2>交易日候选股</h2></div><Link to="/screener">调整筛选</Link></div>
        {data.candidates.length === 0 ? <p className="muted">当前批次没有命中默认条件的股票。</p> : (
          <div className="table-wrap"><table><thead><tr><th>股票</th><th>得分</th><th>命中原因</th></tr></thead><tbody>
            {data.candidates.map((item) => <tr key={`${item.market}${item.stock_code}`}><td><Link to={`/stocks/${item.market}/${item.stock_code}`}>{item.stock_code}</Link><small>{item.market}</small></td><td>{item.score.toFixed(1)}</td><td>{item.reasons.join(' / ')}</td></tr>)}
          </tbody></table></div>
        )}
      </section>
      <section className="panel">
        <div className="panel-title"><div><p className="eyebrow">待处理</p><h2>未确认异动</h2></div></div>
        {alerts.isPending ? <p className="muted">正在读取提醒…</p> : alerts.data?.items.length ? alerts.data.items.map((item) => <div className="alert-row" key={item.id}><span>{item.stock_code} · {item.rule_code}</span>{item.status !== 'CONFIRMED' && <button type="button" onClick={() => confirmAlert.mutate(item.id)}>确认提醒</button>}</div>) : <p className="muted">暂无未确认异动。</p>}
      </section>
    </div>
  )
}
