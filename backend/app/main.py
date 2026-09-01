from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.tencent_market_data import TencentMarketDataGateway
from app.adapters.tencent_realtime import TencentRealtimeGateway
from app.api.v1.p1_router import router as p1_router
from app.api.v1.realtime_router import router as realtime_router
from app.api.v1.router import APIError, router
from app.api.v1.strategy_router import router as strategy_router
from app.application.candidate_outcomes import CandidateOutcomeModule
from app.application.realtime import RealtimeService
from app.application.sync_pipeline import (
    NonTradingDayError,
    SyncInProgressError,
    SyncPipeline,
    SyncResult,
)
from app.infrastructure.database import create_sqlite_session_factory
from app.infrastructure.models import DataBatch, SyncJob
from app.ports.market_data import MarketDataUnavailable


def recover_interrupted_jobs(factory: sessionmaker[Session]) -> None:
    try:
        with factory() as session:
            jobs = list(
                session.scalars(
                    select(SyncJob).where(
                        SyncJob.status.in_(
                            (
                                "PENDING",
                                "FETCHING",
                                "VALIDATING",
                                "CALCULATING",
                                "GENERATING_SIGNALS",
                            )
                        )
                    )
                )
            )
            for job in jobs:
                job.status = "FAILED"
                job.error_summary = f"{job.stage}: 应用进程中断，可手动重试"
                job.error_message = job.error_summary
                job.finished_at = datetime.now(UTC)
                if job.batch_id is not None:
                    batch = session.get(DataBatch, job.batch_id)
                    if batch is not None and batch.status == "BUILDING":
                        batch.status = "FAILED"
            session.commit()
    except SQLAlchemyError:
        # 同步表尚不可用时仍尝试恢复独立的评价任务。
        pass

    try:
        CandidateOutcomeModule(factory).recover_interrupted_runs()
    except SQLAlchemyError:
        # 旧数据库可能尚无 outcome_run；同步恢复已提交，不受评价恢复影响。
        return


def create_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
    sync_runner: Callable[[date], SyncResult] | None = None,
    outcome_runner: Callable[[int], object] | None = None,
) -> FastAPI:
    application = FastAPI(title="A 股交易辅助决策 API", version="0.1.0")
    factory = session_factory or create_sqlite_session_factory()
    recover_interrupted_jobs(factory)
    application.state.session_factory = factory
    application.state.realtime = RealtimeService(factory, TencentRealtimeGateway())
    # 首次安装的表由启动命令执行迁移后创建。
    with suppress(SQLAlchemyError):
        application.state.realtime.recover_interrupted()
    gateway = TencentMarketDataGateway()
    application.state.latest_trade_date = gateway.latest_trade_date
    candidate_outcomes = CandidateOutcomeModule(factory)
    application.state.candidate_outcomes = candidate_outcomes
    resolved_outcome_runner = outcome_runner or candidate_outcomes.evaluate_due_outcomes
    application.state.outcome_runner = resolved_outcome_runner
    if sync_runner is None:
        pipeline = SyncPipeline(
            factory,
            gateway,
            fetch_workers=4,
            outcome_runner=resolved_outcome_runner,
        )
        application.state.sync_runner = pipeline.run
        application.state.sync_prepare = pipeline.prepare
        application.state.sync_execute = pipeline.execute_prepared
    else:
        application.state.sync_runner = sync_runner
        application.state.sync_prepare = None
        application.state.sync_execute = None
    application.include_router(router)
    application.include_router(p1_router)
    application.include_router(realtime_router)
    application.include_router(strategy_router)

    @application.exception_handler(MarketDataUnavailable)
    def market_data_error(_request: Request, error: MarketDataUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "MARKET_DATA_UNAVAILABLE",
                    "message": str(error),
                    "details": None,
                }
            },
        )

    @application.exception_handler(SyncInProgressError)
    def sync_busy(_request: Request, error: SyncInProgressError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "SYNC_IN_PROGRESS", "message": str(error), "details": None}},
        )

    @application.exception_handler(APIError)
    def api_error(_request: Request, error: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            },
        )

    @application.exception_handler(NonTradingDayError)
    def non_trading_day(_request: Request, error: NonTradingDayError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "NON_TRADING_DAY",
                    "message": "目标日期不是交易日，未创建数据批次",
                    "details": str(error),
                }
            },
        )

    return application


app = create_app()
