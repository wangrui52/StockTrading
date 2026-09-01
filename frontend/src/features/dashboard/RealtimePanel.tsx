import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { request, type RealtimeJob, type RealtimeQuotes, type RealtimeStatus } from '../../shared/api/client'

const timeFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const displayTime = (value: string | number) => timeFormat.format(new Date(value))

export function RealtimePanel() {
  const client = useQueryClient()
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const status = useQuery({
    queryKey: ['realtime-status'],
    queryFn: () => request<RealtimeStatus>('/realtime/status'),
    refetchInterval: (query) => query.state.data?.job?.status === 'FETCHING' ? 2000 : 10000,
  })
  const snapshotId = status.data?.snapshot?.refresh_id
  const quotes = useQuery({
    queryKey: ['realtime-quotes', snapshotId, page, keyword],
    queryFn: () => request<RealtimeQuotes>(`/realtime/quotes?page=${page}&page_size=50&q=${encodeURIComponent(keyword)}`),
    enabled: Boolean(snapshotId),
  })
  const refresh = useMutation({
    mutationFn: () => request<RealtimeJob>('/realtime/refresh', { method: 'POST' }),
    onSuccess: (job) => {
      setPage(1)
      client.setQueryData<RealtimeStatus>(['realtime-status'], (old) => ({
        job, snapshot: old?.snapshot ?? null, cooldown_until: old?.cooldown_until ?? null,
      }))
      client.invalidateQueries({ queryKey: ['realtime-status'] })
    },
  })
  const job = status.data?.job
  const fetching = refresh.isPending || job?.status === 'FETCHING'
  const cooldown = Math.max(0, Math.ceil((Date.parse(status.data?.cooldown_until ?? '') - now) / 1000)) || 0
  // 表格与概览使用同一响应的元数据，避免采集中途换快照导致错配。
  const snapshot = quotes.data?.snapshot ?? status.data?.snapshot
  const today = displayTime(now).slice(0, 10)
  const progress = job?.total_count ? Math.round(job.completed_count / job.total_count * 100) : 0

  return (
    <section className="panel realtime-panel" aria-label="全市场实时行情">
      <div className="panel-title">
        <div><p className="eyebrow">沪深京 A 股 · 手动刷新快照</p><h2>全市场实时行情</h2></div>
        <button type="button" disabled={fetching || cooldown > 0 || status.isPending} onClick={() => refresh.mutate()}>
          {fetching ? '正在刷新实时行情…' : cooldown > 0 ? `${cooldown} 秒后可刷新` : '刷新实时行情'}
        </button>
      </div>
      <p className="muted">指标、筛选和报告仍使用收盘日线。全市场分批抓取，报价可能延迟；不是持续推送，也不是同一时刻的成交快照。</p>
      {status.isError && <p role="alert">实时行情状态读取失败：{status.error.message} <button onClick={() => status.refetch()}>重试读取状态</button></p>}
      {refresh.isError && <p role="alert">{refresh.error.message}，上次快照保持不变。</p>}
      {job?.status === 'FAILED' && <p role="alert">{job.error_summary ?? '实时行情刷新失败'}。{snapshot ? '当前展示上次成功快照。' : '请稍后重试。'}</p>}
      {fetching && <div className="context-strip" aria-live="polite">
        <strong>{job?.stage === 'QUOTES' ? `采集报价 ${job.completed_count}/${job.total_count}（${progress}%）` : '正在获取沪深京完整股票池…'}</strong>
        {Boolean(job?.failed_count) && <span>暂缺 {job?.failed_count} 只</span>}
        {snapshot && <span>下方仍为上次快照，完成后更新</span>}
      </div>}
      {!snapshot && !fetching && <p className="muted">尚无实时快照，点击“刷新实时行情”获取全市场报价；无需先同步日线。</p>}
      {snapshot && <>
        <div className="context-strip">
          <strong>快照抓取完成：{displayTime(snapshot.finished_at)}（北京时间）</strong>
          <span>抓取开始：{displayTime(snapshot.started_at)}</span>
          <span>来源 {snapshot.source}</span>
          <span>覆盖 {snapshot.received_count}/{snapshot.total_count} 只</span>
          {snapshot.quote_date !== today && <strong className="status-tag warning">历史快照，请重新刷新</strong>}
        </div>
        {(snapshot.missing_count > 0 || snapshot.stale_count > 0 || snapshot.unavailable_count > 0) && <p className="realtime-warning">
          缺失 {snapshot.missing_count} 只 · 报价早于抓取日 {snapshot.stale_count} 只 · 无有效价格 {snapshot.unavailable_count} 只。
          {snapshot.missing_count > 0 && ` 缺失代码：${snapshot.missing_symbols.join('、')}。`}
          下方统计仅含 {snapshot.quote_date} 已获取的有效报价。
        </p>}
        <div className="metric-grid" aria-label="实时快照市场统计">
          <article><span>上涨 · {snapshot.quote_date}</span><strong className="rise">{snapshot.market_summary.up}</strong></article>
          <article><span>下跌</span><strong className="fall">{snapshot.market_summary.down}</strong></article>
          <article><span>平盘</span><strong>{snapshot.market_summary.flat}</strong></article>
          <article><span>已获取有效报价成交额</span><strong>{(snapshot.market_summary.amount / 100000000).toFixed(2)} 亿</strong></article>
        </div>
        <form className="inline-form realtime-search" onSubmit={(event) => { event.preventDefault(); setKeyword(search.trim()); setPage(1) }}>
          <label>实时行情搜索<input value={search} maxLength={64} onChange={(event) => setSearch(event.target.value)} placeholder="股票代码或名称" /></label>
          <button type="submit">查询报价</button>
        </form>
        {quotes.isError && <p role="alert">报价列表读取失败：{quotes.error.message} <button onClick={() => quotes.refetch()}>重试读取报价</button></p>}
        {quotes.isFetching && <p className="muted">正在读取报价列表…</p>}
        {quotes.data && <>
          <div className="table-wrap"><table aria-label="全市场报价列表"><thead><tr>
            <th>股票</th><th>最新价</th><th>涨跌幅</th><th>成交量（股）</th><th>成交额（万元）</th><th>来源报价时间（北京）</th>
          </tr></thead><tbody>{quotes.data.items.map((row) => {
            const time = displayTime(row.quoted_at)
            const old = time.slice(0, 10) !== today
            return <tr key={`${row.market}${row.stock_code}`}>
              <td>{row.stock_name}<small>{row.market} {row.stock_code}</small></td>
              <td>{row.latest_price == null ? '--' : row.latest_price.toFixed(2)}{row.latest_price == null ? <small>暂无有效报价</small> : row.volume === 0 && <small>暂无成交</small>}</td>
              <td className={row.pct_change == null || row.pct_change === 0 ? 'muted' : row.pct_change > 0 ? 'rise' : 'fall'}>{row.pct_change == null ? '--' : `${row.pct_change.toFixed(2)}%`}</td>
              <td>{row.volume.toLocaleString('zh-CN')}</td><td>{(row.amount / 10000).toFixed(2)}</td>
              <td>{time}{old && <small className="realtime-warning">非今日报价</small>}</td>
            </tr>
          })}</tbody></table></div>
          {quotes.data.items.length === 0 && <p className="muted">没有匹配的股票。</p>}
          <div className="realtime-pagination">
            <span>共 {quotes.data.total} 只 · 第 {page} / {Math.max(1, Math.ceil(quotes.data.total / 50))} 页</span>
            <button type="button" disabled={page <= 1 || quotes.isFetching} onClick={() => setPage(page - 1)}>上一页报价</button>
            <button type="button" disabled={page * 50 >= quotes.data.total || quotes.isFetching} onClick={() => setPage(page + 1)}>下一页报价</button>
          </div>
        </>}
      </>}
    </section>
  )
}
