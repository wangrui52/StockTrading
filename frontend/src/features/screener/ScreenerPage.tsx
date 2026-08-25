import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { request, type ScreenerPresets, type Screening } from '../../shared/api/client'

type Conditions = {
  minimum_score: number; markets: string[]; pct_change_min: number | null; pct_change_max: number | null
  volume_ratio_min: number | null; close_above_ma20: boolean | null; ma5_above_ma20: boolean | null
  macd_filters: string[]; rsi_min: number | null; rsi_max: number | null; include_st: boolean
  include_suspended: boolean; minimum_listed_days: number | null; page_size: number
}

const initialConditions: Conditions = {
  minimum_score: 0, markets: [], pct_change_min: null, pct_change_max: null,
  volume_ratio_min: null, close_above_ma20: true, ma5_above_ma20: null,
  macd_filters: [], rsi_min: null, rsi_max: null, include_st: false,
  include_suspended: false, minimum_listed_days: null, page_size: 50,
}

function optionalNumber(value: string) { return value === '' ? null : Number(value) }

export function ScreenerPage() {
  const client = useQueryClient()
  const [conditions, setConditions] = useState(initialConditions)
  const [presetName, setPresetName] = useState('')
  const [selectedPresetId, setSelectedPresetId] = useState<number | null>(null)
  const activeRequest = useRef<AbortController | null>(null)
  const presets = useQuery({ queryKey: ['screener-presets'], queryFn: () => request<ScreenerPresets>('/screener-presets') })
  const screening = useMutation({ mutationFn: (page: number) => {
    activeRequest.current?.abort()
    activeRequest.current = new AbortController()
    return request<Screening>('/screenings', { method: 'POST', body: JSON.stringify({ ...conditions, page }), signal: activeRequest.current.signal })
  } })
  const preset = useMutation({ mutationFn: () => request('/screener-presets', { method: 'POST', body: JSON.stringify({ name: presetName, conditions, is_default: false }) }), onSuccess: () => client.invalidateQueries({ queryKey: ['screener-presets'] }) })
  const updatePreset = useMutation({ mutationFn: (mode: 'overwrite' | 'rename') => request(`/screener-presets/${selectedPresetId}`, { method: 'PATCH', body: JSON.stringify(mode === 'rename' ? { name: presetName } : { conditions }) }), onSuccess: () => client.invalidateQueries({ queryKey: ['screener-presets'] }) })
  const defaultPreset = useMutation({ mutationFn: (id: number) => request(`/screener-presets/${id}/default`, { method: 'POST' }), onSuccess: () => client.invalidateQueries({ queryKey: ['screener-presets'] }) })
  const deletePreset = useMutation({ mutationFn: (id: number) => request(`/screener-presets/${id}`, { method: 'DELETE' }), onSuccess: () => { setSelectedPresetId(null); client.invalidateQueries({ queryKey: ['screener-presets'] }) } })
  function submit(event: FormEvent) { event.preventDefault(); screening.mutate(1) }
  function toggleMarket(market: string) { setConditions((current) => ({ ...current, markets: current.markets.includes(market) ? current.markets.filter((item) => item !== market) : [...current.markets, market] })) }
  function toggleMacd(rule: string) { setConditions((current) => ({ ...current, macd_filters: current.macd_filters.includes(rule) ? current.macd_filters.filter((item) => item !== rule) : [...current.macd_filters, rule] })) }
  const total = screening.data?.total ?? screening.data?.items.length ?? 0
  const page = screening.data?.page ?? 1
  const pageSize = screening.data?.page_size ?? conditions.page_size
  return <div className="page-stack">
    <section className="panel"><p className="eyebrow">组合条件 · 条件间为 AND</p><h2>股票筛选</h2>
      <form className="filter-form" onSubmit={submit}>
        <fieldset><legend>市场（多选为 OR）</legend>{['SH', 'SZ', 'BJ'].map((market) => <label key={market}><input type="checkbox" checked={conditions.markets.includes(market)} onChange={() => toggleMarket(market)} /> {market}</label>)}</fieldset>
        <label>涨跌幅下限<input type="number" step="0.1" value={conditions.pct_change_min ?? ''} onChange={(event) => setConditions({ ...conditions, pct_change_min: optionalNumber(event.target.value) })} /></label>
        <label>涨跌幅上限<input type="number" step="0.1" value={conditions.pct_change_max ?? ''} onChange={(event) => setConditions({ ...conditions, pct_change_max: optionalNumber(event.target.value) })} /></label>
        <label>最低量比<input type="number" step="0.1" value={conditions.volume_ratio_min ?? ''} onChange={(event) => setConditions({ ...conditions, volume_ratio_min: optionalNumber(event.target.value) })} /></label>
        <label>RSI 下限<input type="number" min="0" max="100" value={conditions.rsi_min ?? ''} onChange={(event) => setConditions({ ...conditions, rsi_min: optionalNumber(event.target.value) })} /></label>
        <label>RSI 上限<input type="number" min="0" max="100" value={conditions.rsi_max ?? ''} onChange={(event) => setConditions({ ...conditions, rsi_max: optionalNumber(event.target.value) })} /></label>
        <label>最少上市交易日<input type="number" min="0" value={conditions.minimum_listed_days ?? ''} onChange={(event) => setConditions({ ...conditions, minimum_listed_days: optionalNumber(event.target.value) })} /></label>
        <label>最低得分<input type="number" value={conditions.minimum_score} onChange={(event) => setConditions({ ...conditions, minimum_score: Number(event.target.value) })} /></label>
        <label>收盘价与 MA20<select value={String(conditions.close_above_ma20)} onChange={(event) => setConditions({ ...conditions, close_above_ma20: event.target.value === 'null' ? null : event.target.value === 'true' })}><option value="null">不限</option><option value="true">收盘价高于 MA20</option><option value="false">收盘价不高于 MA20</option></select></label>
        <label>MA5 与 MA20<select value={String(conditions.ma5_above_ma20)} onChange={(event) => setConditions({ ...conditions, ma5_above_ma20: event.target.value === 'null' ? null : event.target.value === 'true' })}><option value="null">不限</option><option value="true">MA5 高于 MA20</option><option value="false">MA5 不高于 MA20</option></select></label>
        <fieldset><legend>MACD（多选为 OR）</legend>{[['MACD_BULLISH', '多头'], ['MACD_BEARISH', '空头'], ['MACD_GOLDEN_CROSS', '金叉'], ['MACD_DEATH_CROSS', '死叉']].map(([rule, label]) => <label key={rule}><input type="checkbox" checked={conditions.macd_filters.includes(rule)} onChange={() => toggleMacd(rule)} /> {label}</label>)}</fieldset>
        <fieldset><legend>默认排除</legend><label><input type="checkbox" checked={conditions.include_st} onChange={(event) => setConditions({ ...conditions, include_st: event.target.checked })} /> 包含 ST</label><label><input type="checkbox" checked={conditions.include_suspended} onChange={(event) => setConditions({ ...conditions, include_suspended: event.target.checked })} /> 包含停牌</label></fieldset>
        <button type="submit">执行筛选</button><button type="button" disabled={!screening.isPending} onClick={() => { activeRequest.current?.abort(); screening.reset() }}>取消查询</button>
      </form>
    </section>
    {screening.isPending && <section role="status" className="state-card">正在筛选…</section>}
    {screening.isError && <section role="alert" className="state-card">筛选失败，请重试。</section>}
    {screening.data && <section className="panel"><h3>筛选结果 · 交易日 {screening.data.trade_date}</h3><p className="muted">共 {total} 条，第 {page} 页</p>
      {screening.data.items.length ? <div className="table-wrap"><table><thead><tr><th>股票</th><th>收盘</th><th>涨跌幅</th><th>RSI</th><th>命中原因</th></tr></thead><tbody>{screening.data.items.map((item) => <tr key={`${item.market}${item.stock_code}`}><td><Link to={`/stocks/${item.market}/${item.stock_code}`}>{item.market} {item.stock_code}</Link></td><td>{item.close ?? '--'}</td><td>{item.pct_change == null ? '--' : `${item.pct_change.toFixed(2)}%`}</td><td>{item.rsi14 ?? '--'}</td><td>{item.reasons.join(' / ')}</td></tr>)}</tbody></table></div> : <p className="muted">没有股票命中当前条件。</p>}
      <div className="inline-form"><button type="button" disabled={page <= 1} onClick={() => screening.mutate(page - 1)}>上一页</button><button type="button" disabled={page * pageSize >= total} onClick={() => screening.mutate(page + 1)}>下一页</button></div>
      <div className="inline-form"><label>方案名称<input value={presetName} onChange={(event) => setPresetName(event.target.value)} /></label><button type="button" disabled={!presetName} onClick={() => preset.mutate()}>新建方案</button><button type="button" disabled={!selectedPresetId} onClick={() => updatePreset.mutate('overwrite')}>覆盖条件</button><button type="button" disabled={!selectedPresetId || !presetName} onClick={() => updatePreset.mutate('rename')}>重命名</button>{preset.isSuccess && <span>方案已保存。</span>}{preset.isError && <span role="alert">方案名称已存在。</span>}</div>
    </section>}
    <section className="panel"><h3>已保存方案</h3>{presets.data?.items?.length ? presets.data.items.map((item) => <div className="alert-row" key={item.id}><span>{item.name}{item.is_default ? ' · 默认' : ''}</span><div className="inline-form"><button type="button" onClick={() => { setSelectedPresetId(item.id); setPresetName(item.name); setConditions({ ...initialConditions, ...(item.conditions as Partial<Conditions>) }) }}>载入</button><button type="button" onClick={() => defaultPreset.mutate(item.id)}>设为默认</button><button type="button" onClick={() => deletePreset.mutate(item.id)}>删除</button></div></div>) : <p className="muted">暂无保存方案。</p>}</section>
  </div>
}
