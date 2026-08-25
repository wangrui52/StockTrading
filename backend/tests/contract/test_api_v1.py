import json
from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.application.sync_pipeline import SyncResult
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    Base,
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    SignalEvent,
    StockBasic,
    WatchlistGroup,
)
from app.main import create_app


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session]]:
    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        batch = DataBatch(
            trade_date=date(2025, 3, 31),
            status="READY",
            completeness_rate=1.0,
            rule_version="v1",
            is_active=True,
        )
        session.add(batch)
        session.flush()
        session.add(StockBasic(market="SH", stock_code="600000", stock_name="浦发银行"))
        session.add(
            DailyPrice(
                batch_id=batch.id,
                market="SH",
                stock_code="600000",
                trade_date=batch.trade_date,
                adjustment="raw",
                open=10,
                high=10.5,
                low=9.8,
                close=10.2,
                volume=100_000,
                amount=1_020_000,
                pct_change=2.0,
                turnover_rate=1.5,
            )
        )
        session.add(
            DailyIndicator(
                batch_id=batch.id,
                market="SH",
                stock_code="600000",
                trade_date=batch.trade_date,
                rule_version="v1",
                values={"ma5": 10.1, "ma20": 9.9, "rsi14": 60.0},
            )
        )
        session.add(
            SignalEvent(
                batch_id=batch.id,
                market="SH",
                stock_code="600000",
                trade_date=batch.trade_date,
                rule_code="MACD_GOLDEN_CROSS",
                rule_version="v1",
                payload={"risk_level": "low"},
            )
        )
        session.add(
            CandidateResult(
                batch_id=batch.id,
                market="SH",
                stock_code="600000",
                score=3,
                reasons=["MACD_GOLDEN_CROSS"],
            )
        )
        session.add(WatchlistGroup(name="默认", sort_order=0))
        session.commit()
    yield factory
    factory.kw["bind"].dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(create_app(session_factory=session_factory))


def assert_context(payload: dict[str, object]) -> None:
    assert payload["trade_date"] == "2025-03-31"
    assert payload["batch_id"] == 1
    assert payload["rule_version"] == "v1"


def test_status_dashboard_and_stock_queries_share_active_context(client: TestClient) -> None:
    assert client.get("/api/v1/health").json()["status"] == "ok"
    status = client.get("/api/v1/system/status")
    assert status.status_code == 200
    assert_context(status.json()["active_batch"])

    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert_context(dashboard.json())
    assert dashboard.json()["candidates"][0]["stock_code"] == "600000"

    detail = client.get("/api/v1/stocks/SH/600000")
    assert detail.status_code == 200
    assert_context(detail.json())
    assert detail.json()["stock_name"] == "浦发银行"

    for suffix in ("prices", "indicators", "signals"):
        response = client.get(f"/api/v1/stocks/SH/600000/{suffix}")
        assert response.status_code == 200
        assert_context(response.json())
        assert response.json()["items"]


def test_screening_watchlist_alert_and_report_commands(client: TestClient) -> None:
    screened = client.post("/api/v1/screenings", json={"minimum_score": 0})
    assert screened.status_code == 200
    assert_context(screened.json())
    assert screened.json()["items"][0]["stock_code"] == "600000"

    created = client.post(
        "/api/v1/watchlist/items",
        json={"group_id": 1, "market": "SH", "stock_code": "600000"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]
    assert client.get("/api/v1/watchlist/items").json()["items"][0]["id"] == item_id
    assert client.delete(f"/api/v1/watchlist/items/{item_id}").status_code == 204

    alerts = client.get("/api/v1/alerts")
    assert alerts.status_code == 200
    alert_id = alerts.json()["items"][0]["id"]
    confirmed = client.post(f"/api/v1/alerts/{alert_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    report = client.post("/api/v1/reports", json={"market": "SH", "stock_code": "600000"})
    assert report.status_code == 201
    report_id = report.json()["id"]
    assert_context(report.json())
    assert client.get(f"/api/v1/reports/{report_id}").status_code == 200
    exported = client.get(f"/api/v1/reports/{report_id}/export")
    assert exported.status_code == 200
    assert "不构成投资建议" in exported.text


def test_not_found_uses_unified_error_shape(client: TestClient) -> None:
    response = client.get("/api/v1/stocks/SH/999999")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "STOCK_NOT_FOUND", "message": "股票不存在", "details": None}
    }


def test_sync_command_uses_injected_runner(session_factory: sessionmaker[Session]) -> None:
    captured: list[date] = []

    def run(target: date) -> SyncResult:
        captured.append(target)
        return SyncResult(job_id=9, batch_id=8)

    client = TestClient(create_app(session_factory=session_factory, sync_runner=run))
    response = client.post("/api/v1/sync-jobs", json={"target_trade_date": "2025-04-01"})

    assert response.status_code == 201
    assert response.json() == {"job_id": 9, "batch_id": 8}
    assert captured == [date(2025, 4, 1)]


def test_openapi_exposes_all_p0_paths_and_context_schema(
    session_factory: sessionmaker[Session],
) -> None:
    schema = create_app(session_factory=session_factory).openapi()
    expected_paths = {
        "/api/v1/health",
        "/api/v1/system/status",
        "/api/v1/sync-jobs",
        "/api/v1/sync-jobs/{job_id}",
        "/api/v1/dashboard",
        "/api/v1/stocks/{market}/{stock_code}",
        "/api/v1/stocks/{market}/{stock_code}/prices",
        "/api/v1/stocks/{market}/{stock_code}/indicators",
        "/api/v1/stocks/{market}/{stock_code}/signals",
        "/api/v1/screenings",
        "/api/v1/watchlist/items",
        "/api/v1/watchlist/items/{item_id}",
        "/api/v1/alerts",
        "/api/v1/alerts/{signal_id}/confirm",
        "/api/v1/reports",
        "/api/v1/reports/{report_id}",
        "/api/v1/reports/{report_id}/export",
    }
    assert expected_paths <= set(schema["paths"])
    assert set(schema["components"]["schemas"]["BatchContext"]["required"]) == {
        "trade_date",
        "batch_id",
        "rule_version",
    }


def test_openapi_snapshot_matches_application(session_factory: sessionmaker[Session]) -> None:
    expected = json.loads(Path("openapi.json").read_text(encoding="utf-8"))

    assert create_app(session_factory=session_factory).openapi() == expected
