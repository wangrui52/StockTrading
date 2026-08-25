import json
from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.application.sync_pipeline import SyncResult
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    Base,
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    IndexDaily,
    OperationLog,
    SignalEvent,
    StockBasic,
    SyncJob,
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
        session.add(
            IndexDaily(
                batch_id=batch.id,
                index_code="000001",
                trade_date=batch.trade_date,
                open=3000,
                high=3050,
                low=2990,
                close=3040,
                pct_change=1.2,
            )
        )
        session.add(WatchlistGroup(name="默认", sort_order=0))
        session.commit()
    yield factory
    factory.kw["bind"].dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient]:
    with TestClient(create_app(session_factory=session_factory)) as value:
        yield value


def assert_context(payload: dict[str, object]) -> None:
    assert payload["trade_date"] == "2025-03-31"
    assert payload["batch_id"] == 1
    assert payload["rule_version"] == "v1"


def test_status_dashboard_and_stock_queries_share_active_context(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    assert client.get("/api/v1/health").json()["status"] == "ok"
    status = client.get("/api/v1/system/status")
    assert status.status_code == 200
    assert_context(status.json()["active_batch"])

    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert_context(dashboard.json())
    assert dashboard.json()["candidates"][0]["stock_code"] == "600000"
    assert dashboard.json()["indices"][0]["index_code"] == "000001"

    detail = client.get("/api/v1/stocks/SH/600000?source=watchlist")
    assert detail.status_code == 200
    assert_context(detail.json())
    assert detail.json()["stock_name"] == "浦发银行"
    assert detail.json()["trend"] == "偏强"
    assert detail.json()["risk_level"] == "low"
    with session_factory() as session:
        detail_log = session.scalar(
            select(OperationLog).where(OperationLog.event_name == "stock_detail_view")
        )
        assert detail_log is not None
        assert detail_log.details == {"source": "watchlist"}

    for suffix in ("prices", "indicators", "signals"):
        response = client.get(f"/api/v1/stocks/SH/600000/{suffix}")
        assert response.status_code == 200
        assert_context(response.json())
        assert response.json()["items"]


def test_screening_watchlist_alert_and_report_commands(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    assert client.post("/api/v1/screenings", json={}).status_code == 422
    screened = client.post("/api/v1/screenings", json={"close_above_ma20": True})
    assert screened.status_code == 200
    assert_context(screened.json())
    assert screened.json()["items"][0]["stock_code"] == "600000"
    assert screened.json()["total"] == 1
    with session_factory() as session:
        search_log = session.scalar(
            select(OperationLog).where(OperationLog.event_name == "screener_search")
        )
        assert search_log is not None
        assert search_log.details == {"result_count": 1}
    assert (
        client.post(
            "/api/v1/screenings",
            json={"markets": ["SZ"], "page_size": 200},
        ).json()["items"]
        == []
    )
    combined = client.post(
        "/api/v1/screenings",
        json={
            "markets": ["SH"],
            "pct_change_min": 1,
            "close_above_ma20": True,
            "rsi_min": 50,
            "rsi_max": 75,
            "macd_filters": ["MACD_GOLDEN_CROSS"],
        },
    )
    assert combined.status_code == 200
    assert combined.json()["items"][0]["reasons"] == [
        "CLOSE_ABOVE_MA20",
        "MACD_GOLDEN_CROSS",
        "RSI_RANGE",
    ]
    assert client.post("/api/v1/screenings", json={"page_size": 201}).status_code == 422

    created = client.post(
        "/api/v1/watchlist/items",
        json={"group_id": 1, "market": "SH", "stock_code": "600000"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]
    watched = client.get("/api/v1/watchlist/items").json()["items"][0]
    assert watched["id"] == item_id
    assert watched["group_name"] == "默认"
    assert watched["close"] == 10.2
    assert watched["signal_codes"] == ["MACD_GOLDEN_CROSS"]
    assert watched["risk_level"] == "low"
    assert watched["alert_status"] == "TRIGGERED"
    assert client.get("/api/v1/watchlist/groups").json()["items"][0]["name"] == "默认"
    assert len(client.get("/api/v1/alerts?watchlist_only=true").json()["items"]) == 1
    assert client.delete(f"/api/v1/watchlist/items/{item_id}").status_code == 204
    assert client.get("/api/v1/alerts?watchlist_only=true").json()["items"] == []

    alerts = client.get("/api/v1/alerts")
    assert alerts.status_code == 200
    alert_id = alerts.json()["items"][0]["id"]
    assert len(client.get("/api/v1/alerts?limit=1").json()["items"]) == 1
    assert client.get("/api/v1/alerts?limit=0").status_code == 422
    confirmed = client.post(f"/api/v1/alerts/{alert_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    report = client.post("/api/v1/reports", json={"market": "SH", "stock_code": "600000"})
    assert report.status_code == 201
    report_id = report.json()["id"]
    assert_context(report.json())
    content = report.json()["content"]
    for heading in (
        "数据口径与完整性",
        "趋势判断",
        "技术指标",
        "量能变化",
        "关注理由",
        "风险与冲突信号",
        "条件触发与失效条件",
        "结论摘要",
        "免责声明",
    ):
        assert f"## {heading}" in content
    assert "MACD_GOLDEN_CROSS" in content
    assert all(term not in content for term in ("买入", "卖出", "必涨", "目标价", "收益保证"))
    assert client.get(f"/api/v1/reports/{report_id}").status_code == 200
    exported = client.get(f"/api/v1/reports/{report_id}/export")
    assert exported.status_code == 200
    assert "不构成投资建议" in exported.text
    assert "2025-03-31-600000-1.md" in exported.headers["content-disposition"]


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


def test_failed_sync_job_can_be_retried_with_the_same_trade_date(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        failed = SyncJob(
            job_type="MANUAL",
            target_trade_date=date(2025, 4, 1),
            status="FAILED",
            stage="FETCHING",
            failed_items=["600001"],
        )
        session.add(failed)
        session.commit()
        failed_id = failed.id
    captured: list[date] = []

    def run(target: date) -> SyncResult:
        captured.append(target)
        return SyncResult(job_id=10, batch_id=9)

    client = TestClient(create_app(session_factory=session_factory, sync_runner=run))
    response = client.post(f"/api/v1/sync-jobs/{failed_id}/retry")

    assert response.status_code == 201
    assert response.json() == {"job_id": 10, "batch_id": 9}
    assert captured == [date(2025, 4, 1)]


def test_incomplete_batch_requires_explicit_risk_confirmation_before_activation(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        batch = DataBatch(
            trade_date=date(2025, 4, 1),
            status="FAILED",
            completeness_rate=0.98,
            rule_version="v1",
            is_active=False,
        )
        session.add(batch)
        session.flush()
        session.add(
            SyncJob(
                batch_id=batch.id,
                job_type="MANUAL",
                target_trade_date=batch.trade_date,
                status="FAILED",
                stage="FETCHING",
                error_summary="数据完整率 98.00% 低于阈值",
            )
        )
        session.commit()
        batch_id = batch.id

    rejected = client.post(f"/api/v1/data-batches/{batch_id}/activate", json={})
    activated = client.post(f"/api/v1/data-batches/{batch_id}/activate", json={"force": True})

    assert rejected.status_code == 409
    assert activated.status_code == 200
    assert activated.json()["batch_status"] == "READY_WITH_GAPS"
    assert activated.json()["risk_acknowledged"] is True


def test_openapi_exposes_all_p0_paths_and_context_schema(
    session_factory: sessionmaker[Session],
) -> None:
    schema = create_app(session_factory=session_factory).openapi()
    expected_paths = {
        "/api/v1/health",
        "/api/v1/system/status",
        "/api/v1/sync-jobs",
        "/api/v1/sync-jobs/{job_id}",
        "/api/v1/sync-jobs/{job_id}/retry",
        "/api/v1/data-batches/{batch_id}/activate",
        "/api/v1/dashboard",
        "/api/v1/stocks/{market}/{stock_code}",
        "/api/v1/stocks/{market}/{stock_code}/prices",
        "/api/v1/stocks/{market}/{stock_code}/indicators",
        "/api/v1/stocks/{market}/{stock_code}/signals",
        "/api/v1/screenings",
        "/api/v1/watchlist/items",
        "/api/v1/watchlist/groups",
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
        "batch_status",
        "risk_acknowledged",
    }


def test_openapi_snapshot_matches_application(session_factory: sessionmaker[Session]) -> None:
    expected = json.loads(Path("openapi.json").read_text(encoding="utf-8"))

    assert create_app(session_factory=session_factory).openapi() == expected
