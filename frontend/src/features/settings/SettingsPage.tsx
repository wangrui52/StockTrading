import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useEffect, useState } from 'react'

import { request, type AlertRules, type Settings } from '../../shared/api/client'

export function SettingsPage() {
  const client = useQueryClient()
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => request<Settings>('/settings') })
  const rules = useQuery({ queryKey: ['alert-rules'], queryFn: () => request<AlertRules>('/alert-rules') })
  const [enabled, setEnabled] = useState(true)
  const [time, setTime] = useState('18:30')
  const [rsiPeriod, setRsiPeriod] = useState(14)
  const [confirm, setConfirm] = useState(false)
  const [threshold, setThreshold] = useState(80)
  useEffect(() => {
    if (settings.data) { setEnabled(settings.data.auto_sync_enabled); setTime(settings.data.auto_sync_time) }
  }, [settings.data])
  const saveSchedule = useMutation({ mutationFn: () => request<Settings>('/settings', { method: 'PATCH', body: JSON.stringify({ auto_sync_enabled: enabled, auto_sync_time: time }) }), onSuccess: (value) => client.setQueryData(['settings'], value) })
  const createVersion = useMutation({ mutationFn: () => request('/rule-versions', { method: 'POST', body: JSON.stringify({ parameters: { rsi_period: rsiPeriod }, confirm_recalculate: confirm }) }), onSuccess: () => client.invalidateQueries({ queryKey: ['settings'] }) })
  const createRule = useMutation({ mutationFn: () => request('/alert-rules', { method: 'POST', body: JSON.stringify({ name: '自定义 RSI 过热', rule_code: 'CUSTOM_RSI', threshold, enabled: true }) }), onSuccess: () => client.invalidateQueries({ queryKey: ['alert-rules'] }) })
  function scheduleSubmit(event: FormEvent) { event.preventDefault(); saveSchedule.mutate() }
  function versionSubmit(event: FormEvent) { event.preventDefault(); createVersion.mutate() }
  function alertSubmit(event: FormEvent) { event.preventDefault(); createRule.mutate() }
  if (settings.isPending) return <section className="state-card" role="status">正在读取系统设置…</section>
  if (settings.isError) return <section className="state-card" role="alert">系统设置读取失败。</section>
  return <div className="page-stack"><section className="panel"><p className="eyebrow">运行状态</p><h2>系统设置</h2><div className="context-strip"><span>数据源 {settings.data.adapter_version}</span><span>当前规则 {settings.data.current_rule_version}</span></div></section><section className="panel"><h3>自动同步</h3><form className="inline-form" onSubmit={scheduleSubmit}><label><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> 启用交易日自动同步</label><label>执行时间<input type="time" value={time} onChange={(event) => setTime(event.target.value)} /></label><button type="submit">保存同步设置</button></form>{saveSchedule.isSuccess && <p>同步设置已保存。</p>}</section><section className="panel"><h3>指标规则版本</h3><p className="muted">修改参数会创建新版本，不改写历史信号。</p><form className="inline-form" onSubmit={versionSubmit}><label>RSI 周期<input type="number" min="2" value={rsiPeriod} onChange={(event) => setRsiPeriod(Number(event.target.value))} /></label><label><input type="checkbox" checked={confirm} onChange={(event) => setConfirm(event.target.checked)} /> 确认触发重算</label><button type="submit">创建规则版本</button></form>{createVersion.isError && <p role="alert">必须确认重算后才能修改。</p>}{createVersion.isSuccess && <p>新规则版本已创建，等待重算。</p>}</section><section className="panel"><h3>自定义提醒规则</h3><form className="inline-form" onSubmit={alertSubmit}><label>RSI 阈值<input type="number" value={threshold} onChange={(event) => setThreshold(Number(event.target.value))} /></label><button type="submit">新增规则</button></form>{rules.data?.items.map((item) => <p key={item.id}>{item.name} · 阈值 {item.threshold} · v{item.version}</p>)}</section></div>
}
