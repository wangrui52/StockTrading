import { queryOptions, useQuery } from '@tanstack/react-query'

import { request, type SystemStatus } from '../api/client'

export const systemStatusQueryKey = ['system-status'] as const

export const systemStatusQueryOptions = queryOptions({
  queryKey: systemStatusQueryKey,
  queryFn: ({ signal }) => request<SystemStatus>('/system/status', { signal }),
  refetchInterval: (query) => {
    const value = query.state.data?.latest_sync?.status
    return value && !['READY', 'FAILED'].includes(value) ? 2000 : 10000
  },
})

export function useSystemStatusQuery() {
  return useQuery(systemStatusQueryOptions)
}

export function ActiveBatchMonitor() {
  useSystemStatusQuery()
  return null
}
