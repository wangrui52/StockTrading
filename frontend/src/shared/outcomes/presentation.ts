import type { StockCandidateOutcome, StrategyOutcomePage } from '../api/client'

type PresentationFields = 'status' | 'evaluation_trade_date' | 'expected_evaluation_trade_date'
type ScheduledOutcome =
  | Pick<StockCandidateOutcome, PresentationFields>
  | Pick<StrategyOutcomePage['items'][number], PresentationFields>

export function evaluationDateLabel(outcome: ScheduledOutcome) {
  if (outcome.status !== 'PENDING') return outcome.evaluation_trade_date ?? '--'
  return outcome.expected_evaluation_trade_date
    ? `预计 ${outcome.expected_evaluation_trade_date}`
    : '等待交易日历更新'
}
