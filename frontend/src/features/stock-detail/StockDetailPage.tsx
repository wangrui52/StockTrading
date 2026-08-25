import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { useLocation, useParams } from 'react-router-dom'

import { request, type DecisionNotes, type IndicatorSeries, type PriceSeries, type SignalSeries, type StockDetail, type Watchlist, type WatchlistGroups } from '../../shared/api/client'

type IndicatorPoint = {
  trade_date?: string
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  ma60?: number | null
  dif?: number | null
  dea?: number | null
  macd_hist?: number | null
  rsi14?: number | null
  boll_upper?: number | null
  boll_lower?: number | null
}

type DecisionNote = DecisionNotes['items'][number]

function NoteRow({ item, onUpdate, onDelete }: { item: DecisionNote; onUpdate: (id: number, content: string) => void; onDelete: (id: number) => void }) {
  const [content, setContent] = useState(item.content)
  return <div className="alert-row"><label>笔记内容<input value={content} onChange={(event) => setContent(event.target.value)} /></label><div className="inline-form"><button type="button" disabled={!content} onClick={() => onUpdate(item.id, content)}>更新</button><button type="button" onClick={() => onDelete(item.id)}>删除</button></div></div>
}

function TechnicalChart({ prices, indicators }: { prices: PriceSeries['items']; indicators: IndicatorSeries['items'] }) {
  const container = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!container.current || prices.length === 0) return
    const points = indicators as IndicatorPoint[]
    const indicatorByDate = new Map(points.map((item) => [item.trade_date, item]))
    const aligned = prices.map((price) => indicatorByDate.get(price.trade_date) ?? {})
    const categoryAxes = [0, 1, 2, 3]
    let disposed = false
    let chart: { setOption: (option: object) => void; dispose: () => void } | undefined
    void import('./chart').then(({ initPriceChart }) => {
      if (disposed || !container.current) return
      chart = initPriceChart(container.current)
      chart.setOption({
        animation: false,
        legend: { top: 0, type: 'scroll' },
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
        axisPointer: { link: [{ xAxisIndex: 'all' }] },
        grid: [
          { left: 58, right: 24, top: 48, height: '38%' },
          { left: 58, right: 24, top: '48%', height: '10%' },
          { left: 58, right: 24, top: '62%', height: '13%' },
          { left: 58, right: 24, top: '79%', height: '12%' },
        ],
        xAxis: categoryAxes.map((gridIndex) => ({
          type: 'category',
          gridIndex,
          data: prices.map((item) => item.trade_date),
          boundaryGap: false,
          axisLabel: { show: gridIndex === 3 },
        })),
        yAxis: [
          { scale: true, gridIndex: 0 },
          { scale: true, gridIndex: 1, splitNumber: 2 },
          { scale: true, gridIndex: 2, splitNumber: 3 },
          { min: 0, max: 100, gridIndex: 3, splitNumber: 2 },
        ],
        dataZoom: [
          { type: 'inside', xAxisIndex: categoryAxes, start: 55, end: 100 },
          { type: 'slider', xAxisIndex: categoryAxes, bottom: 0, start: 55, end: 100 },
        ],
        series: [
          { name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: prices.map((item) => [item.open, item.close, item.low, item.high]) },
          ...(['ma5', 'ma10', 'ma20', 'ma60', 'boll_upper', 'boll_lower'] as const).map((key) => ({ name: key.toUpperCase(), type: 'line', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, connectNulls: false, data: aligned.map((item) => item[key] ?? null) })),
          { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: prices.map((item) => item.volume) },
          { name: 'MACD', type: 'bar', xAxisIndex: 2, yAxisIndex: 2, data: aligned.map((item) => item.macd_hist ?? null) },
          { name: 'DIF', type: 'line', xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, data: aligned.map((item) => item.dif ?? null) },
          { name: 'DEA', type: 'line', xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, data: aligned.map((item) => item.dea ?? null) },
          { name: 'RSI14', type: 'line', xAxisIndex: 3, yAxisIndex: 3, showSymbol: false, data: aligned.map((item) => item.rsi14 ?? null) },
        ],
      })
    })
    return () => { disposed = true; chart?.dispose() }
  }, [indicators, prices])
  return <div className="price-chart" ref={container} aria-label="K线与技术指标图" />
}

export function StockDetailPage() {
  const { market = '', code = '' } = useParams()
  const source = new URLSearchParams(useLocation().search).get('source')
  const client = useQueryClient()
  const [note, setNote] = useState('')
  const [chartDays, setChartDays] = useState(250)
  const detail = useQuery({ queryKey: ['stock', market, code, source], queryFn: () => request<StockDetail>(`/stocks/${market}/${code}${source ? `?source=${encodeURIComponent(source)}` : ''}`) })
  const prices = useQuery({ queryKey: ['prices', market, code], queryFn: () => request<PriceSeries>(`/stocks/${market}/${code}/prices`) })
  const indicators = useQuery({ queryKey: ['indicators', market, code], queryFn: () => request<IndicatorSeries>(`/stocks/${market}/${code}/indicators`) })
  const signals = useQuery({ queryKey: ['signals', market, code], queryFn: () => request<SignalSeries>(`/stocks/${market}/${code}/signals`) })
  const notes = useQuery({ queryKey: ['notes', market, code], queryFn: () => request<DecisionNotes>('/decision-notes') })
  const watchlist = useQuery({ queryKey: ['watchlist'], queryFn: () => request<Watchlist>('/watchlist/items') })
  const groups = useQuery({ queryKey: ['watchlist-groups'], queryFn: () => request<WatchlistGroups>('/watchlist/groups') })
  const createNote = useMutation({ mutationFn: () => request('/decision-notes', { method: 'POST', body: JSON.stringify({ market, stock_code: code, trade_date: detail.data!.trade_date, content: note }) }), onSuccess: () => { setNote(''); client.invalidateQueries({ queryKey: ['notes', market, code] }) } })
  const updateNote = useMutation({ mutationFn: ({ id, content }: { id: number; content: string }) => request(`/decision-notes/${id}`, { method: 'PATCH', body: JSON.stringify({ content }) }), onSuccess: () => client.invalidateQueries({ queryKey: ['notes', market, code] }) })
  const deleteNote = useMutation({ mutationFn: (id: number) => request(`/decision-notes/${id}`, { method: 'DELETE' }), onSuccess: () => client.invalidateQueries({ queryKey: ['notes', market, code] }) })
  const addWatch = useMutation({ mutationFn: () => request('/watchlist/items', { method: 'POST', body: JSON.stringify({ group_id: groups.data?.items?.[0]?.id ?? 1, market, stock_code: code }) }), onSuccess: () => client.invalidateQueries({ queryKey: ['watchlist'] }) })
  const removeWatch = useMutation({ mutationFn: (id: number) => request(`/watchlist/items/${id}`, { method: 'DELETE' }), onSuccess: () => client.invalidateQueries({ queryKey: ['watchlist'] }) })
  function submitNote(event: FormEvent) { event.preventDefault(); if (note) createNote.mutate() }
  if ([detail, prices, indicators, signals].some((query) => query.isPending)) return <section className="state-card" role="status">正在读取股票详情…</section>
  if ([detail, prices, indicators, signals].some((query) => query.isError)) return <section className="state-card" role="alert">股票详情读取失败。</section>
  const latestIndicator = indicators.data!.items.at(-1)
  const stockNotes = notes.data?.items?.filter((item) => item.market === market && item.stock_code === code) ?? []
  const watched = watchlist.data?.items?.find((item) => item.market === market && item.stock_code === code)
  const chartPrices = prices.data!.items.filter((item) => item.adjustment === 'qfq').slice(-chartDays)
  return <div className="page-stack"><section className="panel detail-hero"><div><p className="eyebrow">{market} · {code} · {detail.data!.industry ?? '行业未知'}</p><h2>{detail.data!.stock_name}</h2><p className="muted">交易日 {detail.data!.trade_date} · 页面价格为不复权，图表与指标采用前复权</p><div className="inline-form">{detail.data!.price?.is_suspended && <span className="status-tag warning">停牌</span>}{watched ? <button type="button" onClick={() => removeWatch.mutate(watched.id)}>移出自选</button> : <button type="button" onClick={() => addWatch.mutate()}>加入自选</button>}</div></div><strong>{detail.data!.price?.close?.toFixed(2) ?? '--'} 元</strong></section><section className="metric-grid"><article><span>趋势摘要</span><strong>{detail.data!.trend}</strong></article><article><span>信号风险</span><strong>{detail.data!.risk_level}</strong><small>{detail.data!.risk_reasons.join(' / ') || '无高风险事件'}</small></article><article><span>涨跌幅</span><strong>{detail.data!.price?.pct_change == null ? '--' : `${detail.data!.price.pct_change.toFixed(2)}%`}</strong></article><article><span>换手率</span><strong>{detail.data!.price?.turnover_rate == null ? '--' : `${detail.data!.price.turnover_rate.toFixed(2)}%`}</strong></article><article><span>成交额</span><strong>{detail.data!.price ? `${(detail.data!.price.amount / 100000000).toFixed(2)} 亿` : '--'}</strong></article></section><section className="panel"><div className="panel-title"><h3>K线、成交量与技术指标</h3><div className="inline-form">{[60, 120, 250].map((days) => <button type="button" key={days} disabled={chartDays === days} onClick={() => setChartDays(days)}>{days} 日</button>)}</div></div><TechnicalChart prices={chartPrices.length ? chartPrices : prices.data!.items.filter((item) => item.adjustment === 'raw').slice(-chartDays)} indicators={indicators.data!.items} />{Boolean(latestIndicator?.unavailable) && <p className="muted">样本不足：{String(latestIndicator?.unavailable)}</p>}</section><section className="metric-grid"><article><span>MA5</span><strong>{String(latestIndicator?.ma5 ?? '--')}</strong></article><article><span>MA20</span><strong>{String(latestIndicator?.ma20 ?? '--')}</strong></article><article><span>RSI14</span><strong>{String(latestIndicator?.rsi14 ?? '--')}</strong></article><article><span>事件数</span><strong>{signals.data!.items.length}</strong></article></section><section className="panel"><h3>最近事件</h3>{signals.data!.items.length ? signals.data!.items.slice(0, 10).map((item) => <p key={item.id}>{item.trade_date} · {item.rule_code}</p>) : <p className="muted">暂无事件。</p>}</section><section className="panel"><h3>关注笔记</h3><form className="inline-form" onSubmit={submitNote}><label>观察结论<input value={note} onChange={(event) => setNote(event.target.value)} /></label><button type="submit">保存笔记</button></form>{stockNotes.map((item) => <NoteRow key={item.id} item={item} onUpdate={(id, content) => updateNote.mutate({ id, content })} onDelete={(id) => deleteNote.mutate(id)} />)}</section></div>
}
