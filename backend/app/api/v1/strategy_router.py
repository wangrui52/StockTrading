import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi import status as http_status
from pydantic import BaseModel, Field, model_validator

from app.api.v1.router import APIError
from app.api.v1.schemas import (
    CandidateOutcomes,
    OutcomeRunCreateRequest,
    OutcomeRunResponse,
    StrategyOutcomePage,
    StrategyOutcomeSummary,
)
from app.application.candidate_outcomes import (
    CandidateOutcomeModule,
    CandidateOutcomeNotFoundError,
    OutcomeBatchNotFoundError,
    OutcomeBatchNotReadyError,
    OutcomeFilters,
    OutcomeRunInProgressError,
    OutcomeRunNotFoundError,
    OutcomeRunStateError,
)

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)
OutcomeStatus = Literal["PENDING", "COMPLETED", "UNAVAILABLE"]


class StrategyOutcomeFilterQuery(BaseModel):
    rule_version: str | None = None
    latest_trading_days: int | None = Field(default=None, ge=1, le=250)
    horizon: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    status: OutcomeStatus | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> "StrategyOutcomeFilterQuery":
        if self.horizon is not None and self.horizon not in {1, 3, 5}:
            raise ValueError("horizon must be one of 1, 3, 5")
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("date_from must not be after date_to")
        return self


class StrategyOutcomeListQuery(StrategyOutcomeFilterQuery):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)


def _module(request: Request) -> CandidateOutcomeModule:
    return request.app.state.candidate_outcomes


def _filters(query: StrategyOutcomeFilterQuery) -> OutcomeFilters:
    values = query.model_dump()
    return OutcomeFilters(**values)


@router.get("/strategy/outcomes", response_model=StrategyOutcomePage)
def list_outcomes(
    request: Request,
    query: Annotated[StrategyOutcomeListQuery, Query()],
) -> StrategyOutcomePage:
    result = _module(request).query_outcomes(_filters(query))
    return StrategyOutcomePage.model_validate(result)


@router.get("/strategy/outcomes/summary", response_model=StrategyOutcomeSummary)
def summarize_outcomes(
    request: Request,
    query: Annotated[StrategyOutcomeFilterQuery, Query()],
) -> StrategyOutcomeSummary:
    result = _module(request).summarize_outcomes(_filters(query))
    return StrategyOutcomeSummary.model_validate(result)


@router.get(
    "/strategy/outcomes/{candidate_result_id}",
    response_model=CandidateOutcomes,
)
def candidate_outcomes(candidate_result_id: int, request: Request) -> CandidateOutcomes:
    module = _module(request)
    try:
        items = module.get_candidate_outcomes(candidate_result_id)
    except CandidateOutcomeNotFoundError as error:
        raise APIError(404, "CANDIDATE_OUTCOME_NOT_FOUND", "候选评价不存在") from error
    return CandidateOutcomes(
        items=items,
        calculation_version=module.calculation_version,
    )


@router.post(
    "/strategy/outcome-runs",
    response_model=OutcomeRunResponse,
    status_code=http_status.HTTP_201_CREATED,
)
def create_outcome_run(
    payload: OutcomeRunCreateRequest,
    request: Request,
) -> OutcomeRunResponse:
    try:
        result = _module(request).evaluate_due_outcomes(payload.evaluation_batch_id)
    except OutcomeBatchNotFoundError as error:
        raise APIError(404, "OUTCOME_BATCH_NOT_FOUND", "评价批次不存在") from error
    except OutcomeBatchNotReadyError as error:
        raise APIError(409, "OUTCOME_BATCH_NOT_READY", "评价批次尚未就绪") from error
    except OutcomeRunInProgressError as error:
        raise APIError(409, "OUTCOME_RUN_IN_PROGRESS", "评价任务正在执行") from error
    except OutcomeRunStateError as error:
        raise APIError(409, "OUTCOME_RUN_STATE_CONFLICT", "评价任务状态冲突") from error
    except Exception as error:
        logger.error(
            "候选评价同步执行失败 batch_id=%s error_type=%s",
            payload.evaluation_batch_id,
            type(error).__name__,
        )
        raise APIError(
            500,
            "OUTCOME_RUN_INTERNAL_ERROR",
            "候选评价执行失败，请稍后重试",
        ) from None
    return OutcomeRunResponse.model_validate(result)


@router.get("/strategy/outcome-runs/{run_id}", response_model=OutcomeRunResponse)
def get_outcome_run(run_id: int, request: Request) -> OutcomeRunResponse:
    try:
        result = _module(request).get_run(run_id)
    except OutcomeRunNotFoundError as error:
        raise APIError(404, "OUTCOME_RUN_NOT_FOUND", "评价任务不存在") from error
    return OutcomeRunResponse.model_validate(result)
