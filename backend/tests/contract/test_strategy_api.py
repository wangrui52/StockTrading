from collections.abc import Generator
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.api.v1.strategy_router as strategy_router_application
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    Base,
    CandidateOutcome,
    CandidateResult,
    DailyPrice,
    DataBatch,
    OutcomeRun,
    StockBasic,
    TradeCalendar,
)
from app.main import create_app


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session]]:
    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        first_batch = _add_batch(session, date(2026, 8, 27), rule_version="rules-v1")
        second_batch = _add_batch(session, date(2026, 8, 28), rule_version="rules-v1")
        first = _add_candidate(session, first_batch, "SH", "600000")
        second = _add_candidate(session, second_batch, "SZ", "000001")
        _add_candidate(session, second_batch, "SH", "600001")
        first_run = OutcomeRun(
            evaluation_batch_id=first_batch.id,
            calculation_version="outcome-v1",
            rule_version=first_batch.rule_version,
            status="COMPLETED",
        )
        second_run = OutcomeRun(
            evaluation_batch_id=second_batch.id,
            calculation_version="outcome-v1",
            rule_version=second_batch.rule_version,
            status="COMPLETED",
        )
        session.add_all([first_run, second_run])
        session.flush()
        session.add_all(
            [
                TradeCalendar(
                    market="CN", trade_date=date(2026, 8, 27), is_open=True
                ),
                TradeCalendar(
                    market="CN", trade_date=date(2026, 8, 28), is_open=True
                ),
                StockBasic(market="SH", stock_code="600000", stock_name="甲公司"),
                StockBasic(market="SZ", stock_code="000001", stock_name="乙公司"),
                _outcome(
                    first,
                    first_batch,
                    1,
                    "COMPLETED",
                    evaluation_batch=second_batch,
                    outcome_run=second_run,
                    return_rate=10,
                    mfe=20,
                    mae=-5,
                ),
                _outcome(
                    first,
                    first_batch,
                    3,
                    "UNAVAILABLE",
                    evaluation_batch=second_batch,
                    outcome_run=second_run,
                ),
                _outcome(
                    second,
                    second_batch,
                    1,
                    "COMPLETED",
                    outcome_run=second_run,
                    return_rate=-5,
                    mfe=5,
                    mae=-10,
                ),
                _outcome(
                    second,
                    second_batch,
                    3,
                    "PENDING",
                    outcome_run=second_run,
                ),
                DailyPrice(
                    batch_id=second_batch.id,
                    market="SZ",
                    stock_code="000001",
                    trade_date=date(2026, 8, 27),
                    adjustment="raw",
                    open=10,
                    high=11,
                    low=9,
                    close=10,
                    volume=100,
                    amount=1000,
                ),
                DailyPrice(
                    batch_id=second_batch.id,
                    market="SZ",
                    stock_code="000001",
                    trade_date=date(2026, 8, 28),
                    adjustment="raw",
                    open=10,
                    high=11,
                    low=9,
                    close=10,
                    volume=100,
                    amount=1000,
                ),
            ]
        )
        session.commit()
    yield factory
    factory.kw["bind"].dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient]:
    with TestClient(create_app(session_factory=session_factory)) as value:
        yield value


def _add_batch(
    session: Session,
    trade_date: date,
    *,
    rule_version: str = "rules-v1",
    status: str = "READY",
) -> DataBatch:
    batch = DataBatch(
        source="test",
        trade_date=trade_date,
        status=status,
        completeness_rate=1,
        rule_version=rule_version,
        is_active=False,
    )
    session.add(batch)
    session.flush()
    return batch


def _add_candidate(
    session: Session,
    batch: DataBatch,
    market: str,
    stock_code: str,
) -> CandidateResult:
    candidate = CandidateResult(
        batch_id=batch.id,
        market=market,
        stock_code=stock_code,
        score=80,
        reasons=[],
        positive_event_count=1,
    )
    session.add(candidate)
    session.flush()
    return candidate


def _outcome(
    candidate: CandidateResult,
    batch: DataBatch,
    horizon: int,
    status: str,
    *,
    evaluation_batch: DataBatch | None = None,
    outcome_run: OutcomeRun | None = None,
    return_rate: float | None = None,
    mfe: float | None = None,
    mae: float | None = None,
) -> CandidateOutcome:
    return CandidateOutcome(
        candidate_result_id=candidate.id,
        source_batch_id=batch.id,
        evaluation_batch_id=(evaluation_batch or batch).id,
        outcome_run_id=outcome_run.id if outcome_run is not None else None,
        source_trade_date=batch.trade_date,
        rule_version=batch.rule_version,
        horizon_trading_days=horizon,
        reference_trade_date=batch.trade_date if status == "COMPLETED" else None,
        evaluation_trade_date=date(2026, 8, 31) if status == "COMPLETED" else None,
        expected_evaluation_trade_date=date(2026, 8, 31),
        reference_price=10 if status == "COMPLETED" else None,
        evaluation_price=11 if status == "COMPLETED" else None,
        return_rate=return_rate,
        mfe=mfe,
        mae=mae,
        status=status,
        unavailable_reason="PRICE_DATA_MISSING" if status == "UNAVAILABLE" else None,
        calculation_version="outcome-v1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_list_outcomes_filters_paginates_and_reports_data_scope(client: TestClient) -> None:
    response = client.get(
        "/api/v1/strategy/outcomes",
        params={
            "rule_version": "rules-v1",
            "horizon": 1,
            "date_from": "2026-08-27",
            "date_to": "2026-08-28",
            "status": "COMPLETED",
            "page": 2,
            "page_size": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert (payload["total"], payload["page"], payload["page_size"]) == (2, 2, 1)
    assert payload["calculation_version"] == "outcome-v1"
    assert payload["data_date"] == "2026-08-28"
    assert payload["filters"] == {
        "rule_version": "rules-v1",
        "latest_trading_days": None,
        "horizon": 1,
        "date_from": "2026-08-27",
        "date_to": "2026-08-28",
        "status": "COMPLETED",
    }
    item = payload["items"][0]
    assert item["candidate_result_id"]
    assert {
        "id",
        "market",
        "stock_code",
        "stock_name",
        "source_batch_id",
        "evaluation_batch_id",
        "source_trade_date",
        "rule_version",
        "horizon_trading_days",
        "reference_trade_date",
        "evaluation_trade_date",
        "expected_evaluation_trade_date",
        "reference_price",
        "evaluation_price",
        "return_rate",
        "mfe",
        "mae",
        "status",
        "unavailable_reason",
        "calculation_version",
        "updated_at",
    } <= item.keys()

    empty = client.get(
        "/api/v1/strategy/outcomes", params={"rule_version": "missing"}
    ).json()
    assert empty["items"] == []
    assert empty["total"] == 0
    assert empty["data_date"] is None
    assert empty["calculation_version"] == "outcome-v1"


@pytest.mark.parametrize(
    "query",
    [
        "horizon=2",
        "status=UNKNOWN",
        "page=0",
        "page_size=201",
        "date_from=2026-08-29&date_to=2026-08-27",
    ],
)
def test_list_outcomes_rejects_invalid_filters(client: TestClient, query: str) -> None:
    assert client.get(f"/api/v1/strategy/outcomes?{query}").status_code == 422


def test_latest_trading_days_is_distinct_date_scope_for_list_and_summary(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        latest_batch = session.query(DataBatch).order_by(DataBatch.trade_date.desc()).first()
        assert latest_batch is not None
        extra = session.query(CandidateResult).filter_by(
            batch_id=latest_batch.id,
            stock_code="600001",
        ).one()
        latest_run = session.query(OutcomeRun).filter_by(
            evaluation_batch_id=latest_batch.id,
            calculation_version="outcome-v1",
            rule_version=latest_batch.rule_version,
            status="COMPLETED",
        ).one()
        session.add_all(
            [
                _outcome(
                    extra,
                    latest_batch,
                    1,
                    "COMPLETED",
                    outcome_run=latest_run,
                    return_rate=3,
                ),
                _outcome(
                    extra,
                    latest_batch,
                    3,
                    "PENDING",
                    outcome_run=latest_run,
                ),
            ]
        )

    listed = client.get(
        "/api/v1/strategy/outcomes",
        params={"latest_trading_days": 1},
    )
    summarized = client.get(
        "/api/v1/strategy/outcomes/summary",
        params={"latest_trading_days": 1},
    )

    assert listed.status_code == summarized.status_code == 200
    list_payload = listed.json()
    summary_payload = summarized.json()
    assert list_payload["total"] == summary_payload["total"] == 4
    assert {item["source_trade_date"] for item in list_payload["items"]} == {
        "2026-08-28"
    }
    assert len(
        {item["candidate_result_id"] for item in list_payload["items"]}
    ) == 2
    assert list_payload["filters"]["latest_trading_days"] == 1
    assert summary_payload["filters"]["latest_trading_days"] == 1
    assert list_payload["data_date"] == summary_payload["data_date"] == "2026-08-28"


@pytest.mark.parametrize("value", [0, 251])
def test_latest_trading_days_query_rejects_values_outside_one_to_250(
    client: TestClient,
    value: int,
) -> None:
    assert client.get(
        "/api/v1/strategy/outcomes",
        params={"latest_trading_days": value},
    ).status_code == 422
    assert client.get(
        "/api/v1/strategy/outcomes/summary",
        params={"latest_trading_days": value},
    ).status_code == 422


def test_summary_reports_completed_sample_metrics_and_empty_sample(client: TestClient) -> None:
    response = client.get("/api/v1/strategy/outcomes/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "total": 4,
        "completed": 2,
        "unavailable": 1,
        "pending": 1,
        "sample_size": 2,
        "completion_rate": 0.75,
        "mean_return_rate": 2.5,
        "median_return_rate": 2.5,
        "positive_return_ratio": 0.5,
        "mean_mfe": 12.5,
        "mean_mae": -7.5,
        "max_drawdown_approx": -10.0,
        "insufficient_sample": True,
        "calculation_version": "outcome-v1",
        "filters": {
            "rule_version": None,
            "latest_trading_days": None,
            "horizon": None,
            "date_from": None,
            "date_to": None,
            "status": None,
        },
        "data_date": "2026-08-28",
    }
    assert "win_rate" not in payload

    empty = client.get(
        "/api/v1/strategy/outcomes/summary", params={"rule_version": "missing"}
    ).json()
    assert empty["total"] == 0
    assert empty["sample_size"] == 0
    assert empty["completion_rate"] == 0
    assert empty["data_date"] is None
    assert empty["mean_return_rate"] is None
    assert empty["max_drawdown_approx"] is None


def test_candidate_outcomes_distinguishes_missing_and_unscanned(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        candidate_ids = [
            item.id
            for item in session.query(CandidateResult).order_by(CandidateResult.id)
        ]

    found = client.get(f"/api/v1/strategy/outcomes/{candidate_ids[0]}")
    unscanned = client.get(f"/api/v1/strategy/outcomes/{candidate_ids[2]}")
    missing = client.get("/api/v1/strategy/outcomes/999")

    assert found.status_code == 200
    assert len(found.json()["items"]) == 2
    assert found.json()["calculation_version"] == "outcome-v1"
    assert unscanned.status_code == 200
    assert unscanned.json()["items"] == []
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CANDIDATE_OUTCOME_NOT_FOUND"


def test_create_and_get_outcome_run_maps_typed_errors(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        evaluation = _add_batch(session, date(2026, 9, 3))
        unready = _add_batch(session, date(2026, 9, 4), status="BUILDING")
        busy_batch = _add_batch(session, date(2026, 9, 5))
        conflict_batch = _add_batch(session, date(2026, 9, 6))
        busy = OutcomeRun(
            evaluation_batch_id=busy_batch.id,
            rule_version=busy_batch.rule_version,
            status="RUNNING",
        )
        conflict = OutcomeRun(
            evaluation_batch_id=conflict_batch.id,
            rule_version=conflict_batch.rule_version,
            status="UNKNOWN",
        )
        session.add_all([busy, conflict])
        session.commit()
        ids = evaluation.id, unready.id, busy_batch.id, conflict_batch.id

    created = client.post("/api/v1/strategy/outcome-runs", json={"evaluation_batch_id": ids[0]})
    assert created.status_code == 201
    assert created.json()["status"] == "COMPLETED"
    run_id = created.json()["id"]
    assert client.get(f"/api/v1/strategy/outcome-runs/{run_id}").json() == created.json()

    not_found = client.post(
        "/api/v1/strategy/outcome-runs", json={"evaluation_batch_id": 999}
    )
    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "OUTCOME_BATCH_NOT_FOUND"

    cases = [
        (ids[1], "OUTCOME_BATCH_NOT_READY"),
        (ids[2], "OUTCOME_RUN_IN_PROGRESS"),
        (ids[3], "OUTCOME_RUN_STATE_CONFLICT"),
    ]
    for batch_id, code in cases:
        response = client.post(
            "/api/v1/strategy/outcome-runs", json={"evaluation_batch_id": batch_id}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == code

    missing = client.get("/api/v1/strategy/outcome-runs/999")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "OUTCOME_RUN_NOT_FOUND"


def test_create_outcome_run_redacts_unexpected_internal_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_message = (
        "private-run sqlite:////Users/private/outcomes.db "
        "SELECT * FROM outcome_run https://secret.example"
    )

    def fail_evaluation(_batch_id: int) -> object:
        raise RuntimeError(sensitive_message)

    monkeypatch.setattr(
        client.app.state.candidate_outcomes,
        "evaluate_due_outcomes",
        fail_evaluation,
    )
    monkeypatch.setattr(strategy_router_application.logger, "disabled", False)
    with caplog.at_level("ERROR", logger="app.api.v1.strategy_router"):
        response = client.post(
            "/api/v1/strategy/outcome-runs",
            json={"evaluation_batch_id": 987},
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "OUTCOME_RUN_INTERNAL_ERROR",
            "message": "候选评价执行失败，请稍后重试",
            "details": None,
        }
    }
    logs = caplog.text
    assert "batch_id=987" in logs
    assert "error_type=RuntimeError" in logs
    exposed = response.text + logs
    assert "private-run" not in exposed
    assert "SELECT" not in exposed
    assert "/Users/private" not in exposed
    assert "https://secret.example" not in exposed


def test_strategy_openapi_contains_all_routes_and_response_schemas(client: TestClient) -> None:
    schema = client.app.openapi()
    paths = schema["paths"]
    assert {
        "/api/v1/strategy/outcomes",
        "/api/v1/strategy/outcomes/summary",
        "/api/v1/strategy/outcomes/{candidate_result_id}",
        "/api/v1/strategy/outcome-runs",
        "/api/v1/strategy/outcome-runs/{run_id}",
    } <= paths.keys()
    assert client.get("/api/v1/strategy/outcomes/summary").status_code == 200
    schemas = schema["components"]["schemas"]
    assert {
        "StrategyOutcomePage",
        "StrategyOutcomeSummary",
        "CandidateOutcomes",
        "OutcomeRunResponse",
    } <= schemas.keys()
    outcome_properties = schemas["StrategyOutcomeView"]["properties"]
    assert "expected_evaluation_trade_date" in outcome_properties
    assert "预计" in outcome_properties["expected_evaluation_trade_date"]["description"]
    drawdown = schemas["StrategyOutcomeSummary"]["properties"][
        "max_drawdown_approx"
    ]
    assert "MAE" in drawdown["description"]
    assert "资金曲线" in drawdown["description"]
