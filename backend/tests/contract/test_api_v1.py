import json
from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import app.api.v1.router as api_router_application
from app.application.sync_pipeline import SyncResult
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    Base,
    CandidateOutcome,
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    IndexDaily,
    OperationLog,
    OutcomeRun,
    SignalEvent,
    StockBasic,
    SyncJob,
    WatchlistGroup,
)
from app.main import create_app


def test_sync_without_date_uses_backend_latest_closed_trade_date(session_factory):
    calls = []
    application = create_app(
        session_factory=session_factory,
        sync_runner=lambda target: calls.append(target) or SyncResult(3, 4),
    )
    application.state.latest_trade_date = lambda: date(2026, 8, 27)
    response = TestClient(application).post("/api/v1/sync-jobs", json={})
    assert response.status_code == 201
    assert calls == [date(2026, 8, 27)]


def test_source_failure_returns_actionable_api_error(session_factory):
    from app.ports.market_data import MarketDataUnavailable

    def fail():
        raise MarketDataUnavailable("交易日历暂时不可用")

    application = create_app(session_factory=session_factory)
    application.state.latest_trade_date = fail
    response = TestClient(application).post("/api/v1/sync-jobs", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MARKET_DATA_UNAVAILABLE"


def test_sync_busy_is_actionable_and_failed_batch_signals_are_hidden(session_factory):
    from app.application.sync_pipeline import SyncInProgressError

    def busy(target):
        raise SyncInProgressError("已有同步任务")

    client = TestClient(create_app(session_factory=session_factory, sync_runner=busy))
    response = client.post("/api/v1/sync-jobs", json={"target_trade_date": "2026-08-27"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SYNC_IN_PROGRESS"
    with session_factory() as session:
        batch = DataBatch(
            trade_date=date(2025, 3, 31),
            status="FAILED",
            completeness_rate=0.5,
            rule_version="v1",
            is_active=False,
        )
        session.add(batch)
        session.flush()
        event = SignalEvent(
            batch_id=batch.id,
            market="SH",
            stock_code="600000",
            trade_date=batch.trade_date,
            rule_code="FAKE",
            rule_version="v1",
            payload={},
        )
        session.add(event)
        session.commit()
        event_id = event.id
    alerts = client.get("/api/v1/alerts").json()["items"]
    assert all(item["rule_code"] != "FAKE" for item in alerts)
    assert client.post(f"/api/v1/alerts/{event_id}/confirm").status_code == 404
    report = client.post("/api/v1/reports", json={"market": "SH", "stock_code": "600000"})
    assert "FAKE" not in report.json()["content"]
    screened = client.post("/api/v1/screenings", json={"macd_filters": ["FAKE"]})
    assert screened.status_code == 200
    assert screened.json()["items"] == []


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
                volume_ratio=1.3,
                pct_change=2.0,
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


def test_dashboard_candidate_exposes_factual_evidence_for_ai_review(client: TestClient) -> None:
    response = client.get("/api/v1/dashboard")

    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["close"] == 10.2
    assert candidate["pct_change"] == 2.0
    assert candidate["rsi14"] == 60.0
    assert candidate["volume_ratio"] == 1.3


def test_codex_cli_recommendations_can_be_imported_for_active_batch(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/ai-recommendations/import",
        json={
            "batch_id": 1,
            "provider": "codex_cli",
            "model": "codex-default",
            "prompt_version": "candidate-review-v1",
            "evidence_snapshot": {
                "batch_id": 1,
                "candidates": [
                    {
                        "market": "SH",
                        "stock_code": "600000",
                        "evidence": {
                            "rule_score": 3,
                            "rsi14": 60,
                        },
                    }
                ],
            },
            "items": [
                {
                    "market": "SH",
                    "stock_code": "600000",
                    "recommendation": "FOCUS",
                    "ai_score": 82,
                    "horizon_trading_days": 5,
                    "reasons": ["规则得分为3，且RSI处于候选区间"],
                    "risks": ["缺少基本面数据"],
                    "invalidation": "收盘价跌破MA20",
                    "confidence": 0.76,
                }
            ],
        },
    )

    assert response.status_code == 201
    assert response.json() == {"batch_id": 1, "imported_count": 1, "run_id": 1}


def test_dashboard_exposes_latest_codex_cli_recommendation(client: TestClient) -> None:
    payload = {
        "batch_id": 1,
        "provider": "codex_cli",
        "model": "codex-default",
        "prompt_version": "candidate-review-v1",
        "evidence_snapshot": {
            "batch_id": 1,
            "candidates": [{"market": "SH", "stock_code": "600000"}],
        },
        "items": [
            {
                "market": "SH",
                "stock_code": "600000",
                "recommendation": "WATCH",
                "ai_score": 68,
                "horizon_trading_days": 3,
                "reasons": ["趋势仍为正向"],
                "risks": ["成交量确认不足"],
                "invalidation": "MA5跌破MA20",
                "confidence": 0.64,
            }
        ],
    }
    assert client.post("/api/v1/ai-recommendations/import", json=payload).status_code == 201
    payload["items"][0]["recommendation"] = "FOCUS"
    payload["items"][0]["ai_score"] = 84
    assert client.post("/api/v1/ai-recommendations/import", json=payload).status_code == 201

    response = client.get("/api/v1/dashboard")

    assert response.status_code == 200
    recommendation = response.json()["candidates"][0]["ai_recommendation"]
    assert recommendation == {
        "recommendation": "FOCUS",
        "ai_score": 84,
        "horizon_trading_days": 3,
        "reasons": ["趋势仍为正向"],
        "risks": ["成交量确认不足"],
        "invalidation": "MA5跌破MA20",
        "confidence": 0.64,
        "provider": "codex_cli",
        "model": "codex-default",
        "run_version": 2,
    }


def test_ai_import_rejects_stock_missing_from_evidence_snapshot(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ai-recommendations/import",
        json={
            "batch_id": 1,
            "provider": "codex_cli",
            "model": "codex-default",
            "prompt_version": "candidate-review-v1",
            "evidence_snapshot": {"batch_id": 1, "candidates": []},
            "items": [
                {
                    "market": "SH",
                    "stock_code": "600000",
                    "recommendation": "WATCH",
                    "ai_score": 50,
                    "horizon_trading_days": 3,
                    "reasons": ["证据有限"],
                    "risks": ["证据有限"],
                    "invalidation": "趋势失效",
                    "confidence": 0.4,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AI_EVIDENCE_MISMATCH"


def test_watchlist_exposes_latest_codex_ai_analysis(client: TestClient) -> None:
    assert client.post(
        "/api/v1/watchlist/items",
        json={"group_id": 1, "market": "SH", "stock_code": "600000"},
    ).status_code == 201
    imported = client.post(
        "/api/v1/ai-recommendations/import",
        json={
            "batch_id": 1,
            "scope": "watchlist",
            "provider": "codex_cli",
            "model": "codex-default",
            "prompt_version": "watchlist-review-v1",
            "evidence_snapshot": {
                "batch_id": 1,
                "scope": "watchlist",
                "stocks": [{"market": "SH", "stock_code": "600000"}],
            },
            "items": [
                {
                    "market": "SH",
                    "stock_code": "600000",
                    "recommendation": "WATCH",
                    "ai_score": 66,
                    "horizon_trading_days": 3,
                    "reasons": ["MACD信号为正向"],
                    "risks": ["缺少基本面数据"],
                    "invalidation": "收盘价跌破MA20",
                    "confidence": 0.62,
                }
            ],
        },
    )
    assert imported.status_code == 201

    item = client.get("/api/v1/watchlist/items").json()["items"][0]

    assert item["ai_analysis"] == {
        "recommendation": "WATCH",
        "ai_score": 66,
        "horizon_trading_days": 3,
        "reasons": ["MACD信号为正向"],
        "risks": ["缺少基本面数据"],
        "invalidation": "收盘价跌破MA20",
        "confidence": 0.62,
        "provider": "codex_cli",
        "model": "codex-default",
        "run_version": 1,
    }


def test_dashboard_candidate_names_match_market_and_keep_missing_stocks(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        session.add(StockBasic(market="SZ", stock_code="600000", stock_name="同码测试股票"))
        session.add_all([
            CandidateResult(
                batch_id=1, market="SZ", stock_code="600000", score=4, reasons=[]
            ),
            CandidateResult(
                batch_id=1, market="SH", stock_code="999999", score=4, reasons=[]
            ),
        ])
        session.commit()

    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200
    items = response.json()["candidates"]
    assert len(items) == 3
    names = {(item["market"], item["stock_code"]): item["stock_name"] for item in items}
    assert names == {
        ("SH", "600000"): "浦发银行",
        ("SZ", "600000"): "同码测试股票",
        ("SH", "999999"): None,
    }


def test_dashboard_exposes_all_candidate_outcome_aggregate_states(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        batch = session.get(DataBatch, 1)
        published_run = OutcomeRun(
            evaluation_batch_id=batch.id,
            calculation_version="outcome-v1",
            rule_version=batch.rule_version,
            status="COMPLETED",
        )
        session.add(published_run)
        session.flush()
        candidates = []
        for code in ("600010", "600011", "600012"):
            candidate = CandidateResult(
                batch_id=batch.id,
                market="SH",
                stock_code=code,
                score=5,
                reasons=[],
            )
            session.add(candidate)
            session.flush()
            candidates.append(candidate)
        for candidate, statuses in zip(
            candidates,
            (
                ("COMPLETED", "COMPLETED", "COMPLETED"),
                ("UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE"),
                ("COMPLETED", "UNAVAILABLE", "PENDING"),
            ),
            strict=True,
        ):
            session.add_all(
                [
                    CandidateOutcome(
                        candidate_result_id=candidate.id,
                        source_batch_id=batch.id,
                        source_trade_date=batch.trade_date,
                        rule_version=batch.rule_version,
                        horizon_trading_days=horizon,
                        evaluation_batch_id=batch.id,
                        outcome_run_id=published_run.id,
                        status=status,
                        calculation_version="outcome-v1",
                    )
                    for horizon, status in zip((1, 3, 5), statuses, strict=True)
                ]
            )
        session.commit()

    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200
    states = {
        item["stock_code"]: item["outcome_status"]
        for item in response.json()["candidates"]
    }
    assert states["600000"] == "PENDING"
    assert states["600010"] == "COMPLETED"
    assert states["600011"] == "UNAVAILABLE"
    assert states["600012"] == "PARTIAL"


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
    assert dashboard.json()["candidates"][0]["outcome_status"] == "PENDING"
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


def test_stock_detail_returns_only_current_candidate_outcomes_sorted_and_complete(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        active = session.get(DataBatch, 1)
        published_run = OutcomeRun(
            evaluation_batch_id=active.id,
            calculation_version="outcome-v1",
            rule_version=active.rule_version,
            status="COMPLETED",
        )
        session.add(published_run)
        session.flush()
        current = session.scalar(
            select(CandidateResult).where(
                CandidateResult.batch_id == active.id,
                CandidateResult.market == "SH",
                CandidateResult.stock_code == "600000",
            )
        )
        historical = DataBatch(
            trade_date=date(2025, 3, 28),
            status="READY",
            completeness_rate=1,
            rule_version="v1",
            is_active=False,
        )
        session.add(historical)
        session.flush()
        historical_candidate = CandidateResult(
            batch_id=historical.id,
            market="SH",
            stock_code="600000",
            score=9,
            reasons=[],
        )
        other_market = CandidateResult(
            batch_id=active.id,
            market="SZ",
            stock_code="600000",
            score=8,
            reasons=[],
        )
        unscanned = CandidateResult(
            batch_id=active.id,
            market="SH",
            stock_code="600002",
            score=7,
            reasons=[],
        )
        session.add_all([historical_candidate, other_market, unscanned])
        session.add_all(
            [
                StockBasic(market="SZ", stock_code="600000", stock_name="同码股票"),
                StockBasic(market="SH", stock_code="600001", stock_name="非候选股票"),
                StockBasic(market="SH", stock_code="600002", stock_name="待扫描股票"),
            ]
        )
        session.flush()

        def outcome(candidate, horizon, *, version="outcome-v1", status="COMPLETED"):
            return CandidateOutcome(
                candidate_result_id=candidate.id,
                source_batch_id=candidate.batch_id,
                evaluation_batch_id=active.id,
                outcome_run_id=(published_run.id if version == "outcome-v1" else None),
                source_trade_date=active.trade_date,
                rule_version="v1",
                horizon_trading_days=horizon,
                reference_trade_date=date(2025, 4, 1) if status == "COMPLETED" else None,
                evaluation_trade_date=date(2025, 4, horizon) if status == "COMPLETED" else None,
                expected_evaluation_trade_date=date(2025, 4, horizon),
                reference_price=10 if status == "COMPLETED" else None,
                evaluation_price=10 + horizon if status == "COMPLETED" else None,
                return_rate=float(horizon) if status == "COMPLETED" else None,
                mfe=float(horizon + 1) if status == "COMPLETED" else None,
                mae=float(-horizon) if status == "COMPLETED" else None,
                status=status,
                unavailable_reason="停牌" if status == "UNAVAILABLE" else None,
                calculation_version=version,
            )

        session.add_all(
            [
                outcome(current, 5, status="UNAVAILABLE"),
                outcome(current, 1),
                outcome(current, 3),
                outcome(current, 1, version="outcome-v2"),
                outcome(historical_candidate, 1),
                outcome(other_market, 1),
            ]
        )
        session.commit()

    response = client.get("/api/v1/stocks/SH/600000")
    assert response.status_code == 200
    items = response.json()["candidate_outcomes"]
    assert [item["horizon_trading_days"] for item in items] == [1, 3, 5]
    assert items[0] == {
        "horizon_trading_days": 1,
        "status": "COMPLETED",
        "reference_trade_date": "2025-04-01",
        "evaluation_trade_date": "2025-04-01",
        "expected_evaluation_trade_date": "2025-04-01",
        "reference_price": 10.0,
        "evaluation_price": 11.0,
        "return_rate": 1.0,
        "mfe": 2.0,
        "mae": -1.0,
        "unavailable_reason": None,
        "calculation_version": "outcome-v1",
    }
    assert items[2]["status"] == "UNAVAILABLE"
    assert items[2]["unavailable_reason"] == "停牌"
    assert items[2]["return_rate"] is None
    assert client.get("/api/v1/stocks/SH/600001").json()["candidate_outcomes"] == []
    assert client.get("/api/v1/stocks/SH/600002").json()["candidate_outcomes"] == []


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


@pytest.mark.parametrize(
    ("initial_status", "request_payload", "expected_status"),
    [
        ("READY", {}, "READY"),
        ("FAILED", {"force": True}, "READY_WITH_GAPS"),
    ],
)
def test_manual_activation_schedules_outcomes_after_commit_and_isolates_failures(
    session_factory: sessionmaker[Session],
    initial_status: str,
    request_payload: dict[str, bool],
    expected_status: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        batch = DataBatch(
            trade_date=date(2025, 4, 1),
            status=initial_status,
            completeness_rate=0.98 if initial_status == "FAILED" else 1,
            rule_version="v1",
            is_active=False,
        )
        session.add(batch)
        session.flush()
        if initial_status == "FAILED":
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
    calls: list[int] = []
    sensitive_message = (
        "private-activation sqlite:////Users/private/manual.db "
        "SELECT * FROM outcome_run https://secret.example"
    )

    def fail_after_observing_commit(observed_batch_id: int) -> None:
        with session_factory() as session:
            observed = session.get(DataBatch, observed_batch_id)
            assert observed is not None and observed.is_active
            assert observed.status == expected_status
        calls.append(observed_batch_id)
        raise RuntimeError(sensitive_message)

    client = TestClient(
        create_app(
            session_factory=session_factory,
            outcome_runner=fail_after_observing_commit,
        )
    )
    monkeypatch.setattr(api_router_application.logger, "disabled", False)
    with caplog.at_level("ERROR", logger="app.api.v1.router"):
        response = client.post(
            f"/api/v1/data-batches/{batch_id}/activate",
            json=request_payload,
        )

    assert response.status_code == 200
    assert response.json()["batch_status"] == expected_status
    assert calls == [batch_id]
    logs = caplog.text
    assert f"batch_id={batch_id}" in logs
    assert "error_type=RuntimeError" in logs
    assert "private-activation" not in logs
    assert "SELECT" not in logs
    assert "/Users/private" not in logs
    assert "https://secret.example" not in logs
    with session_factory() as session:
        activated = session.get(DataBatch, batch_id)
        assert activated is not None and activated.is_active


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
    candidate_schema = schema["components"]["schemas"]["CandidateItem"]
    assert candidate_schema["properties"]["outcome_status"]["enum"] == [
        "PENDING",
        "PARTIAL",
        "COMPLETED",
        "UNAVAILABLE",
    ]
    detail_schema = schema["components"]["schemas"]["StockDetailResponse"]
    assert "candidate_outcomes" in detail_schema["required"]
    assert detail_schema["properties"]["candidate_outcomes"]["items"]["$ref"].endswith(
        "/StockCandidateOutcomeItem"
    )
    stock_outcome = schema["components"]["schemas"]["StockCandidateOutcomeItem"]
    assert "expected_evaluation_trade_date" in stock_outcome["properties"]


def test_openapi_snapshot_matches_application(session_factory: sessionmaker[Session]) -> None:
    expected = json.loads(Path("openapi.json").read_text(encoding="utf-8"))

    assert create_app(session_factory=session_factory).openapi() == expected
