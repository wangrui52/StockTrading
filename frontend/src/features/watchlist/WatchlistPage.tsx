import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { request, type Watchlist, type WatchlistGroups } from '../../shared/api/client'

export function WatchlistPage() {
  const client = useQueryClient()
  const [code, setCode] = useState('')
  const [market, setMarket] = useState('SH')
  const [groupId, setGroupId] = useState(1)
  const items = useQuery({ queryKey: ['watchlist'], queryFn: () => request<Watchlist>('/watchlist/items') })
  const groups = useQuery({ queryKey: ['watchlist-groups'], queryFn: () => request<WatchlistGroups>('/watchlist/groups') })
  useEffect(() => { if (!groupId && groups.data?.items[0]) setGroupId(groups.data.items[0].id) }, [groupId, groups.data])
  const add = useMutation({ mutationFn: () => request('/watchlist/items', { method: 'POST', body: JSON.stringify({ group_id: groupId, market, stock_code: code }) }), onSuccess: () => { setCode(''); client.invalidateQueries({ queryKey: ['watchlist'] }) } })
  const remove = useMutation({ mutationFn: (id: number) => request(`/watchlist/items/${id}`, { method: 'DELETE' }), onSuccess: () => client.invalidateQueries({ queryKey: ['watchlist'] }) })
  function submit(event: FormEvent) { event.preventDefault(); if (code && groupId) add.mutate() }
  return <div className="page-stack">
    <section className="panel"><p className="eyebrow">持续跟踪</p><h2>自选股</h2>
      <form className="inline-form" onSubmit={submit}>
        <label>市场<select value={market} onChange={(event) => setMarket(event.target.value)}><option value="SH">沪市</option><option value="SZ">深市</option><option value="BJ">北交所</option></select></label>
        <label>股票代码<input pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value)} placeholder="600000" /></label>
        <label>关注分组<select value={groupId} onChange={(event) => setGroupId(Number(event.target.value))}>{groups.data?.items?.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label>
        <button type="submit" disabled={!groupId}>加入自选</button>
      </form>{add.isError && <p role="alert">加入失败，请检查股票代码和分组。</p>}
    </section>
    <section className="panel">{items.isPending ? <p role="status">正在读取自选…</p> : items.isError ? <p role="alert">自选读取失败。</p> : items.data?.items?.length ? <div className="table-wrap"><table><thead><tr><th>股票</th><th>分组</th><th>收盘</th><th>涨跌幅</th><th>主要信号</th><th>风险</th><th>提醒</th><th /></tr></thead><tbody>{items.data.items.map((item) => <tr key={item.id}><td><Link to={`/stocks/${item.market}/${item.stock_code}?source=watchlist`}>{item.market} {item.stock_code}</Link><small>{item.stock_name}</small></td><td>{item.group_name}</td><td>{item.close ?? '--'}</td><td>{item.pct_change == null ? '--' : `${item.pct_change.toFixed(2)}%`}</td><td>{item.signal_codes.join(' / ') || '--'}</td><td><span className={`status-tag ${item.risk_level === 'high' ? 'warning' : ''}`}>{item.risk_level ?? '--'}</span></td><td>{item.alert_status}</td><td><button type="button" onClick={() => remove.mutate(item.id)}>移除</button></td></tr>)}</tbody></table></div> : <p className="muted">尚未添加自选股。</p>}</section>
  </div>
}
