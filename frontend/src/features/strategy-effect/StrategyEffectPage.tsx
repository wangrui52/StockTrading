import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import {
  request,
  type StrategyOutcomePage,
  type StrategyOutcomeSummary,
} from '../../shared/api/client'
import { evaluationDateLabel } from '../../shared/outcomes/presentation'
import { useSystemStatusQuery } from '../../shared/queries/systemStatus'

const PAGE_SIZE = 20
const LATEST_TRADING_DAYS = 60

function percent(value: number | null | undefined, ratio = false) {
  if (value == null) return '--'
  return `${(ratio ? value * 100 : value).toFixed(2)}%`
}

function price(value: number | null | undefined) {
  return value == null ? '--' : value.toFixed(2)
}

function queryString(filters: Filters, includePage: boolean) {
  const values = new URLSearchParams({
    rule_version: filters.ruleVersion,
    horizon: String(filters.horizon),
    latest_trading_days: String(filters.latestTradingDays),
  })
  if (filters.dateFrom) values.set('date_from', filters.dateFrom)
  if (filters.dateTo) values.set('date_to', filters.dateTo)
  if (filters.status) values.set('status', filters.status)
  if (includePage) {
    values.set('page', String(filters.page))
    values.set('page_size', String(PAGE_SIZE))
  }
  return values.toString()
}

type OutcomeScope = {
  ruleVersion: string
  horizon: number
  dateFrom: string
  dateTo: string
  status: string
  latestTradingDays: number
}

type Filters = OutcomeScope & {
  page: number
}

export function StrategyEffectPage() {
  const statusQuery = useSystemStatusQuery()
  const activeRule = statusQuery.data?.active_batch?.rule_version ?? ''
  const activeBatchId = statusQuery.data?.active_batch?.batch_id ?? null
  const [ruleOverride, setRuleOverride] = useState<string | null>(null)
  const [ruleDraft, setRuleDraft] = useState('')
  const [horizon, setHorizon] = useState(5)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [outcomeStatus, setOutcomeStatus] = useState('')
  const [page, setPage] = useState(1)
  const ruleVersion = ruleOverride ?? activeRule
  useEffect(() => {
    if (ruleOverride === null) setRuleDraft(activeRule)
  }, [activeRule, ruleOverride])
  const scope = {
    ruleVersion,
    horizon,
    dateFrom,
    dateTo,
    status: outcomeStatus,
    latestTradingDays: LATEST_TRADING_DAYS,
  }
  const filters = { ...scope, page }
  const dateRangeError = Boolean(dateFrom && dateTo && dateFrom > dateTo)
  const enabled = Boolean(activeRule && ruleVersion && !dateRangeError)

  const summaryQuery = useQuery({
    queryKey: ['strategy-outcomes-summary', activeBatchId, scope],
    queryFn: ({ signal }) => request<StrategyOutcomeSummary>(`/strategy/outcomes/summary?${queryString(filters, false)}`, { signal }),
    enabled,
  })
  const listQuery = useQuery({
    queryKey: ['strategy-outcomes', activeBatchId, scope, page, PAGE_SIZE],
    queryFn: ({ signal }) => request<StrategyOutcomePage>(`/strategy/outcomes?${queryString(filters, true)}`, { signal }),
    enabled,
  })

  const resetPage = () => setPage(1)
  const retry = () => {
    if (statusQuery.isError) {
      void statusQuery.refetch()
      return
    }
    void summaryQuery.refetch()
    void listQuery.refetch()
  }

  if (statusQuery.isPending) return <section className="state-card" role="status">正在读取策略效果…</section>
  if (statusQuery.isError) return <ErrorState retry={retry} />
  if (!statusQuery.data.active_batch) {
    return <section className="empty-state"><h2>策略效果</h2><p>当前没有可用的生产数据批次</p></section>
  }
  const initialLoading = Boolean(ruleVersion && (
    (summaryQuery.isPending && !summaryQuery.data) || (listQuery.isPending && !listQuery.data)
  ))

  const summary = summaryQuery.data
  const list = listQuery.data
  const refreshing = summaryQuery.isFetching || listQuery.isFetching

  return (
    <section className="page-stack strategy-effect-page">
      <div className="panel-title">
        <div><p className="eyebrow">候选反馈闭环</p><h2>策略效果</h2></div>
        <div className="muted">数据日期 {summary?.data_date ?? '--'} {refreshing && <span>· 正在刷新</span>}</div>
      </div>

      <div className="panel strategy-filters">
        <label>评价周期<select aria-label="评价周期" value={horizon} onChange={(event) => { setHorizon(Number(event.target.value)); resetPage() }}>
          <option value={1}>T+1</option><option value={3}>T+3</option><option value={5}>T+5</option>
        </select></label>
        <label>规则版本<input aria-label="规则版本" value={ruleDraft} onChange={(event) => setRuleDraft(event.target.value)} /></label>
        <label>开始日期<input aria-label="开始日期" type="date" value={dateFrom} onChange={(event) => { setDateFrom(event.target.value); resetPage() }} /></label>
        <label>结束日期<input aria-label="结束日期" type="date" value={dateTo} onChange={(event) => { setDateTo(event.target.value); resetPage() }} /></label>
        <label>评价状态<select aria-label="评价状态" value={outcomeStatus} onChange={(event) => { setOutcomeStatus(event.target.value); resetPage() }}>
          <option value="">全部</option><option value="PENDING">PENDING</option><option value="COMPLETED">COMPLETED</option><option value="UNAVAILABLE">UNAVAILABLE</option>
        </select></label>
        <button type="button" onClick={() => { setRuleOverride(ruleDraft.trim()); resetPage() }}>应用筛选</button>
      </div>

      {dateRangeError ? <section className="state-card" role="alert">开始日期不能晚于结束日期，请修改筛选条件</section>
        : ruleVersion && (summaryQuery.isError || listQuery.isError) ? <ErrorState retry={retry} />
        : initialLoading ? <section className="state-card" role="status">正在读取策略效果…</section> : <>
      {summary && <>
        <div className="metric-grid strategy-metrics">
          <article><span>候选评价数</span><strong>{summary.total}</strong></article>
          <article><span>完成进度</span><strong>{percent(summary.completion_rate, true)}</strong></article>
          <article><span>正收益样本占比</span><strong>{percent(summary.positive_return_ratio, true)}</strong></article>
          <article><span>收益表现</span><strong>平均 {percent(summary.mean_return_rate)} · 中位 {percent(summary.median_return_rate)}</strong></article>
          <article><span>平均 MFE</span><strong className="rise">{percent(summary.mean_mfe)}</strong></article>
          <article><span>平均 MAE</span><strong className="fall">{percent(summary.mean_mae)}</strong></article>
          <article aria-label="最大回撤近似值" title="COMPLETED 样本持有窗口中最差 MAE 的近似值，不是资金曲线最大回撤">
            <span>最大回撤近似值</span><strong className="fall">{percent(summary.max_drawdown_approx)}</strong>
            <small>样本最差 MAE 近似，非资金曲线回撤</small>
          </article>
        </div>
        {summary.unavailable > 0 && <p className="strategy-warning">部分候选因行情缺失或停牌无法评价</p>}
        {summary.insufficient_sample && <p className="strategy-warning">样本不足，不用于判断策略有效性</p>}
      </>}

      {!ruleVersion ? <section className="empty-state"><p>请输入规则版本</p></section> : !list?.items.length ? <section className="empty-state"><p>暂无候选评价数据</p></section> : <div className="panel">
        <div className="table-wrap"><table aria-label="策略效果明细"><thead><tr>
          <th>股票</th><th>源候选日</th><th>周期</th><th>状态</th><th>参考日 / 价</th><th>评价日或预计日 / 价</th><th>收益</th><th>MFE</th><th>MAE</th><th>缺失原因</th>
        </tr></thead><tbody>{list.items.map((outcome) => <tr key={outcome.id}>
          <td>{outcome.stock_name || '名称暂缺'}<small>{outcome.stock_code} · {outcome.market}</small></td>
          <td>{outcome.source_trade_date}</td><td>T+{outcome.horizon_trading_days}</td><td>{outcome.status}</td>
          <td>{outcome.reference_trade_date ?? '--'}<small>{price(outcome.reference_price)}</small></td>
          <td>{evaluationDateLabel(outcome)}<small>{price(outcome.evaluation_price)}</small></td>
          <td className={(outcome.return_rate ?? 0) >= 0 ? 'rise' : 'fall'}>{percent(outcome.return_rate)}</td>
          <td className="rise">{percent(outcome.mfe)}</td><td className="fall">{percent(outcome.mae)}</td><td>{outcome.unavailable_reason ?? '--'}</td>
        </tr>)}</tbody></table></div>
        <div className="strategy-pagination">
          <button type="button" disabled={page <= 1 || listQuery.isFetching} onClick={() => setPage(page - 1)}>上一页</button>
          <span>第 {page} 页 · 共 {list.total} 条</span>
          <button type="button" disabled={page * PAGE_SIZE >= list.total || listQuery.isFetching} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      </div>}
      </>}
      <p className="muted">不复权、T+1开盘参考、仅供研究</p>
    </section>
  )
}

function ErrorState({ retry }: { retry: () => void }) {
  return <section className="state-card" role="alert"><h2>策略效果读取失败</h2><button type="button" onClick={retry}>重试</button></section>
}
