import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useState } from 'react'

import { request, type Watchlist } from '../../shared/api/client'

export function WatchlistPage() {
  const client = useQueryClient(); const [code, setCode] = useState('')
  const items = useQuery({ queryKey: ['watchlist'], queryFn: () => request<Watchlist>('/watchlist/items') })
  const add = useMutation({ mutationFn: () => request('/watchlist/items', { method: 'POST', body: JSON.stringify({ group_id: 1, market: code.startsWith('6') ? 'SH' : 'SZ', stock_code: code }) }), onSuccess: () => { setCode(''); client.invalidateQueries({ queryKey: ['watchlist'] }) } })
  const remove = useMutation({ mutationFn: (id: number) => request(`/watchlist/items/${id}`, { method: 'DELETE' }), onSuccess: () => client.invalidateQueries({ queryKey: ['watchlist'] }) })
  function submit(event: FormEvent) { event.preventDefault(); if (code) add.mutate() }
  return <div className="page-stack"><section className="panel"><p className="eyebrow">持续跟踪</p><h2>自选股</h2><form className="inline-form" onSubmit={submit}><label>股票代码<input pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value)} placeholder="600000" /></label><button type="submit">加入自选</button></form></section><section className="panel">{items.isPending ? <p role="status">正在读取自选…</p> : items.isError ? <p role="alert">自选读取失败。</p> : items.data?.items?.length ? items.data.items.map((item) => <div className="alert-row" key={item.id}><span>{item.market} {item.stock_code}</span><button type="button" onClick={() => remove.mutate(item.id)}>移除</button></div>) : <p className="muted">尚未添加自选股。</p>}</section></div>
}
