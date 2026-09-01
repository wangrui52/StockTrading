import type { components } from './schema'

export type RealtimeStatus = components['schemas']['RealtimeStatusResponse']
export type RealtimeJob = components['schemas']['RealtimeJobResponse']
export type RealtimeQuotes = components['schemas']['RealtimeQuotesResponse']

export type StockCandidateOutcome = components['schemas']['StockCandidateOutcomeItem']

export type Dashboard = components['schemas']['DashboardResponse']
export type SystemStatus = components['schemas']['SystemStatusResponse']
export type Screening = components['schemas']['ScreeningResponse']
export type ScreenerPresets = components['schemas']['PresetList']
export type Watchlist = components['schemas']['WatchlistResponse']
export type WatchlistGroups = components['schemas']['WatchlistGroupResponse']
export type Report = components['schemas']['ReportResponse']
export type StockDetail = components['schemas']['StockDetailResponse']
export type PriceSeries = components['schemas']['PriceSeriesResponse']
export type IndicatorSeries = components['schemas']['IndicatorSeriesResponse']
export type SignalSeries = components['schemas']['SignalSeriesResponse']
export type AlertList = components['schemas']['AlertListResponse']
export type Settings = components['schemas']['SettingsResponse']
export type AlertRules = components['schemas']['AlertRuleList']
export type DecisionNotes = components['schemas']['NoteList']
export type StrategyOutcomePage = components['schemas']['StrategyOutcomePage']
export type StrategyOutcomeSummary = components['schemas']['StrategyOutcomeSummary']
export type CandidateOutcomes = components['schemas']['CandidateOutcomes']
export type OutcomeRun = components['schemas']['OutcomeRunResponse']

export class APIError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new APIError(
      response.status,
      body?.error?.code ?? 'HTTP_ERROR',
      body?.error?.message ?? `请求失败 (${response.status})`,
    )
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
