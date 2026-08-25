import type { components } from './schema'

export type Dashboard = components['schemas']['DashboardResponse']
export type Screening = components['schemas']['ScreeningResponse']
export type Watchlist = components['schemas']['WatchlistResponse']
export type Report = components['schemas']['ReportResponse']
export type StockDetail = components['schemas']['StockDetailResponse']
export type PriceSeries = components['schemas']['PriceSeriesResponse']
export type IndicatorSeries = components['schemas']['IndicatorSeriesResponse']
export type SignalSeries = components['schemas']['SignalSeriesResponse']
export type AlertList = components['schemas']['AlertListResponse']

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
