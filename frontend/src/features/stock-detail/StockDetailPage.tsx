import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'

import { request, type IndicatorSeries, type PriceSeries, type SignalSeries, type StockDetail } from '../../shared/api/client'

function PriceChart({ prices }: { prices: PriceSeries['items'] }) {
  const container = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!container.current || prices.length === 0) return
    let disposed = false
    let chart: { setOption: (option: object) => void; dispose: () => void } | undefined
    void import('./chart').then(({ initPriceChart }) => {
      if (disposed || !container.current) return
      chart = initPriceChart(container.current)
      chart.setOption({
        animation: false,
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: prices.map((item) => item.trade_date) },
        yAxis: { scale: true },
        series: [{ type: 'line', showSymbol: false, data: prices.map((item) => item.close), name: '收盘价' }],
      })
    })
    return () => { disposed = true; chart?.dispose() }
  }, [prices])
  return <div className="price-chart" ref={container} aria-label="日线收盘价图" />
}

export function StockDetailPage() {
  const { market = '', code = '' } = useParams()
  const detail = useQuery({ queryKey: ['stock', market, code], queryFn: () => request<StockDetail>(`/stocks/${market}/${code}`) })
  const prices = useQuery({ queryKey: ['prices', market, code], queryFn: () => request<PriceSeries>(`/stocks/${market}/${code}/prices`) })
  const indicators = useQuery({ queryKey: ['indicators', market, code], queryFn: () => request<IndicatorSeries>(`/stocks/${market}/${code}/indicators`) })
  const signals = useQuery({ queryKey: ['signals', market, code], queryFn: () => request<SignalSeries>(`/stocks/${market}/${code}/signals`) })
  if ([detail, prices, indicators, signals].some((query) => query.isPending)) return <section className="state-card" role="status">正在读取股票详情…</section>
  if ([detail, prices, indicators, signals].some((query) => query.isError)) return <section className="state-card" role="alert">股票详情读取失败。</section>
  const latestIndicator = indicators.data!.items.at(-1)
  return <div className="page-stack"><section className="panel detail-hero"><div><p className="eyebrow">{market} · {code}</p><h2>{detail.data!.stock_name}</h2><p className="muted">交易日 {detail.data!.trade_date} · 指标采用前复权</p></div><strong>{detail.data!.price?.close?.toFixed(2) ?? '--'} 元</strong></section><section className="panel"><h3>日线趋势</h3><PriceChart prices={prices.data!.items.filter((item) => item.adjustment === 'raw').slice(-250)} /></section><section className="metric-grid"><article><span>MA5</span><strong>{String(latestIndicator?.ma5 ?? '--')}</strong></article><article><span>MA20</span><strong>{String(latestIndicator?.ma20 ?? '--')}</strong></article><article><span>RSI14</span><strong>{String(latestIndicator?.rsi14 ?? '--')}</strong></article><article><span>事件数</span><strong>{signals.data!.items.length}</strong></article></section><section className="panel"><h3>最近事件</h3>{signals.data!.items.length ? signals.data!.items.slice(0, 10).map((item) => <p key={item.id}>{item.trade_date} · {item.rule_code}</p>) : <p className="muted">暂无事件。</p>}</section></div>
}
