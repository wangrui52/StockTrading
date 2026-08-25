import { useMutation } from '@tanstack/react-query'
import { FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'

import { request, type Screening } from '../../shared/api/client'

export function ScreenerPage() {
  const [minimumScore, setMinimumScore] = useState(0)
  const [presetName, setPresetName] = useState('')
  const screening = useMutation({
    mutationFn: () => request<Screening>('/screenings', { method: 'POST', body: JSON.stringify({ minimum_score: minimumScore }) }),
  })
  const preset = useMutation({
    mutationFn: () => request('/screener-presets', { method: 'POST', body: JSON.stringify({ name: presetName, conditions: { minimum_score: minimumScore }, is_default: false }) }),
  })
  function submit(event: FormEvent) { event.preventDefault(); screening.mutate() }
  return <div className="page-stack"><section className="panel"><p className="eyebrow">组合条件</p><h2>股票筛选</h2><form className="inline-form" onSubmit={submit}><label>最低得分<input type="number" value={minimumScore} onChange={(event) => setMinimumScore(Number(event.target.value))} /></label><button type="submit">执行筛选</button></form></section>{screening.isPending && <section role="status" className="state-card">正在筛选…</section>}{screening.isError && <section role="alert" className="state-card">筛选失败，请重试。</section>}{screening.data && <section className="panel"><h3>筛选结果 · 交易日 {screening.data.trade_date}</h3>{screening.data.items.length ? screening.data.items.map((item) => <p key={item.stock_code}><Link to={`/stocks/${item.market}/${item.stock_code}`}>{item.stock_code}</Link> · {item.score.toFixed(1)}</p>) : <p className="muted">没有股票命中当前条件。</p>}<div className="inline-form"><label>方案名称<input value={presetName} onChange={(event) => setPresetName(event.target.value)} /></label><button type="button" disabled={!presetName} onClick={() => preset.mutate()}>保存筛选方案</button>{preset.isSuccess && <span>方案已保存。</span>}{preset.isError && <span role="alert">方案名称已存在。</span>}</div></section>}</div>
}
