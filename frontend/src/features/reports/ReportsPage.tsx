import { useMutation } from '@tanstack/react-query'
import { FormEvent, useState } from 'react'

import { request, type Report } from '../../shared/api/client'

export function ReportsPage() {
  const [code, setCode] = useState('')
  const report = useMutation({ mutationFn: () => request<Report>('/reports', { method: 'POST', body: JSON.stringify({ market: code.startsWith('6') ? 'SH' : 'SZ', stock_code: code }) }) })
  function submit(event: FormEvent) { event.preventDefault(); if (code) report.mutate() }
  return <div className="page-stack"><section className="panel"><p className="eyebrow">可复现结论</p><h2>分析报告</h2><form className="inline-form" onSubmit={submit}><label>股票代码<input pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value)} placeholder="600000" /></label><button type="submit">生成报告</button></form></section>{report.isPending && <section role="status" className="state-card">正在生成…</section>}{report.isError && <section role="alert" className="state-card">报告生成失败。</section>}{report.data && <section className="panel report"><div className="context-strip"><span>交易日 {report.data.trade_date}</span><span>批次 #{report.data.batch_id}</span><span>版本 {report.data.report_version}</span></div><pre>{report.data.content}</pre><a className="button-link" href={`/api/v1/reports/${report.data.id}/export`}>导出 Markdown</a></section>}</div>
}
