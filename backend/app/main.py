from collections.abc import Callable
from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.akshare_market_data import AkShareMarketDataGateway
from app.api.v1.p1_router import router as p1_router
from app.api.v1.router import APIError, router
from app.application.sync_pipeline import SyncPipeline, SyncResult
from app.infrastructure.database import create_sqlite_session_factory


def create_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
    sync_runner: Callable[[date], SyncResult] | None = None,
) -> FastAPI:
    application = FastAPI(title="A 股交易辅助决策 API", version="0.1.0")
    factory = session_factory or create_sqlite_session_factory()
    application.state.session_factory = factory
    application.state.sync_runner = sync_runner or (
        lambda target: SyncPipeline(factory, AkShareMarketDataGateway()).run(target)
    )
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

    return application


app = create_app()
