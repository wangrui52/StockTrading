from collections.abc import Callable
from datetime import UTC, date, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.akshare_market_data import AkShareMarketDataGateway
from app.api.v1.p1_router import router as p1_router
from app.api.v1.router import APIError, router
from app.application.sync_pipeline import NonTradingDayError, SyncPipeline, SyncResult
from app.infrastructure.database import create_sqlite_session_factory
from app.infrastructure.models import DataBatch, SyncJob


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
        # 首次安装尚未执行迁移时由启动命令负责建表，恢复检查不阻断迁移。
        return


def create_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
    sync_runner: Callable[[date], SyncResult] | None = None,
) -> FastAPI:
    application = FastAPI(title="A 股交易辅助决策 API", version="0.1.0")
    factory = session_factory or create_sqlite_session_factory()
    recover_interrupted_jobs(factory)
    application.state.session_factory = factory
    if sync_runner is None:
        pipeline = SyncPipeline(factory, AkShareMarketDataGateway())
        application.state.sync_runner = pipeline.run
        application.state.sync_prepare = pipeline.prepare
        application.state.sync_execute = pipeline.execute_prepared
    else:
        application.state.sync_runner = sync_runner
        application.state.sync_prepare = None
        application.state.sync_execute = None
    application.include_router(router)
    application.include_router(p1_router)

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
