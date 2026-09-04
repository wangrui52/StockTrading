import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { request, type RealtimeJob, type RealtimeStatus, type Watchlist, type WatchlistGroups } from '../../shared/api/client'

const timeFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const displayTime = (value: string | number) => timeFormat.format(new Date(value))

const aiRecommendationLabels: Record<string, { label: string; tone: string }> = {
  FOCUS: { label: '重点关注', tone: 'success' },
  WATCH: { label: '继续观察', tone: 'warning' },
  AVOID: { label: '暂时回避', tone: 'danger' },
}

function AIAnalysis({ item }: { item: Watchlist['items'][number]['ai_analysis'] }) {
  if (!item) return <span className="muted">尚未生成</span>
  const presentation = aiRecommendationLabels[item.recommendation] ?? {
    label: item.recommendation,
    tone: 'warning',
  }
  return <div className="ai-review">
    <span className={`status-tag ${presentation.tone}`.trim()}>{presentation.label} · {item.ai_score}分</span>
    <small>{item.reasons.join(' / ')}</small>
    <small className="warning">风险：{item.risks.join(' / ')}</small>
    <small className="muted">失效：{item.invalidation}</small>
  </div>
}

export function WatchlistPage() {
  const client = useQueryClient()
  const [code, setCode] = useState('')
  const [market, setMarket] = useState('SH')
  const [groupId, setGroupId] = useState(1)
  const items = useQuery({ queryKey: ['watchlist'], queryFn: () => request<Watchlist>('/watchlist/items') })
  const groups = useQuery({ queryKey: ['watchlist-groups'], queryFn: () => request<WatchlistGroups>('/watchlist/groups') })
  const [now, setNow] = useState(Date.now)
  const previousSnapshot = useRef<number | undefined>(undefined)
  const realtimeStatus = useQuery({
    queryKey: ['watchlist-realtime-status'],
    queryFn: () => request<RealtimeStatus>('/realtime/status?scope=watchlist'),
    refetchInterval: (query) => query.state.data?.job?.status === 'FETCHING' ? 2000 : 10000,
  })
  const snapshot = realtimeStatus.data?.snapshot
  const job = realtimeStatus.data?.job
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    if (snapshot?.refresh_id && snapshot.refresh_id !== previousSnapshot.current) {
      previousSnapshot.current = snapshot.refresh_id
      client.invalidateQueries({ queryKey: ['watchlist'] })
    }
  }, [client, snapshot?.refresh_id])
  const refresh = useMutation({
    mutationFn: () => request<RealtimeJob>('/realtime/refresh?scope=watchlist', { method: 'POST' }),
    onSuccess: (value) => {
      client.setQueryData<RealtimeStatus>(['watchlist-realtime-status'], (old) => ({
        job: value, snapshot: old?.snapshot ?? null, cooldown_until: old?.cooldown_until ?? null,
      }))
      client.invalidateQueries({ queryKey: ['watchlist-realtime-status'] })
    },
  })
  const refreshing = refresh.isPending || job?.status === 'FETCHING'
  const cooldown = Math.max(0, Math.ceil((Date.parse(realtimeStatus.data?.cooldown_until ?? '') - now) / 1000)) || 0
  const today = displayTime(now).slice(0, 10)
  useEffect(() => { if (!groupId && groups.data?.items[0]) setGroupId(groups.data.items[0].id) }, [groupId, groups.data])
  const add = useMutation({ mutationFn: () => request('/watchlist/items', { method: 'POST', body: JSON.stringify({ group_id: groupId, market, stock_code: code }) }), onSuccess: () => { setCode(''); client.invalidateQueries({ queryKey: ['watchlist'] }) } })
  const remove = useMutation({ mutationFn: (id: number) => request(`/watchlist/items/${id}`, { method: 'DELETE' }), onSuccess: () => client.invalidateQueries({ queryKey: ['watchlist'] }) })
  function submit(event: FormEvent) { event.preventDefault(); if (code && groupId) add.mutate() }
  return <div className="page-stack">
    <section className="panel">
      <div className="panel-title">
        <div><p className="eyebrow">持续跟踪</p><h2>自选股</h2></div>
        <button type="button"
          disabled={refreshing || cooldown > 0 || realtimeStatus.isPending || items.isError || !items.data?.items?.length}
          onClick={() => refresh.mutate()}>
          {refreshing ? '正在刷新自选行情…' : cooldown > 0 ? `${cooldown} 秒后可刷新自选` : '刷新自选股行情'}
        </button>
      </div>
      <p className="muted">仅刷新点击时的自选股票报价；信号、风险和提醒仍基于收盘日线。未取得实时报价时显示日线参考，并标明时间。报价可能延迟。</p>
      {realtimeStatus.isError && <p role="alert">行情刷新状态读取失败：{realtimeStatus.error.message} <button onClick={() => realtimeStatus.refetch()}>重试自选行情状态</button></p>}
      {refresh.isError && <p role="alert">{refresh.error.message}，已显示的报价保持不变。</p>}
      {job?.status === 'FAILED' && <p role="alert">{job.error_summary ?? '自选行情刷新失败'}。保留上次报价，请稍后重试。</p>}
      {refreshing && <p aria-live="polite">{job?.stage === 'QUOTES'
        ? `正在采集自选报价 ${job.completed_count}/${job.total_count}，暂缺 ${job.failed_count} 只。`
        : '正在准备自选股票报价…'} 完成前保留上次数据。</p>}
      {snapshot && <div className="context-strip">
        <strong>上次自选刷新完成：{displayTime(snapshot.finished_at)}（北京时间）</strong>
        <span>获取 {snapshot.received_count}/{snapshot.total_count} 只 · 来源 {snapshot.source}</span>
        {snapshot.quote_date !== today && <span className="status-tag warning">历史快照，请刷新</span>}
        {snapshot.missing_count > 0 && <span className="status-tag warning">缺失 {snapshot.missing_count} 只</span>}
      </div>}
      <form className="inline-form" onSubmit={submit}>
        <label>市场<select value={market} onChange={(event) => setMarket(event.target.value)}><option value="SH">沪市</option><option value="SZ">深市</option><option value="BJ">北交所</option></select></label>
        <label>股票代码<input pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value)} placeholder="600000" /></label>
        <label>关注分组<select value={groupId} onChange={(event) => setGroupId(Number(event.target.value))}>{groups.data?.items?.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label>
        <button type="submit" disabled={!groupId}>加入自选</button>
      </form>{add.isError && <p role="alert">加入失败，请检查股票代码和分组。</p>}
    </section>
    <section className="panel">
      {items.isPending ? <p role="status">正在读取自选…</p> : items.isError ? <p role="alert">自选读取失败。</p> : items.data?.items?.length ? <div className="table-wrap">
        <table aria-label="自选股行情"><thead><tr>
          <th>股票</th><th>分组</th><th>最新价 / 参考价</th><th>涨跌幅</th><th>行情时间（北京）</th>
          <th>主要信号（日线）</th><th>风险（日线）</th><th>AI分析</th><th>提醒</th><th />
        </tr></thead><tbody>{items.data.items.map((item) => {
          const quote = item.realtime
          const price = quote ? quote.latest_price : item.close
          const change = quote ? quote.pct_change : item.pct_change
          const quotedAt = quote ? displayTime(quote.quoted_at) : null
          return <tr key={item.id}>
            <td><Link to={`/stocks/${item.market}/${item.stock_code}?source=watchlist`}>{item.market} {item.stock_code}</Link><small>{item.stock_name || quote?.stock_name}</small></td>
            <td>{item.group_name}</td>
            <td>{price == null ? '--' : price.toFixed(2)}<small>{quote ? price == null ? '暂无有效报价' : '实时报价快照' : '日线参考'}</small></td>
            <td className={change == null || change === 0 ? 'muted' : change > 0 ? 'rise' : 'fall'}>{change == null ? '--' : `${change.toFixed(2)}%`}</td>
            <td>{quotedAt ?? item.trade_date ?? '--'}{quotedAt && quotedAt.slice(0, 10) !== today && <small className="realtime-warning">非今日报价</small>}{!quote && <small>尚无实时报价，请刷新</small>}</td>
            <td>{item.signal_codes.join(' / ') || '--'}<small>日线 {item.trade_date ?? '--'}</small></td>
            <td><span className={`status-tag ${item.risk_level === 'high' ? 'warning' : ''}`}>{item.risk_level ?? '--'}</span></td>
            <td><AIAnalysis item={item.ai_analysis} /></td>
            <td>{item.alert_status}</td>
            <td><button type="button" onClick={() => remove.mutate(item.id)}>移除</button></td>
          </tr>
        })}</tbody></table>
      </div> : <p className="muted">尚未添加自选股。</p>}
    </section>
  </div>
}
