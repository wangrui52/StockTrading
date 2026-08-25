from collections.abc import Generator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import Base, DataBatch, SignalEvent, StockBasic
from app.main import create_app


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session]]:
    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        batch = DataBatch(
            trade_date=date(2025, 3, 31),
            status="READY",
            completeness_rate=1,
            rule_version="v1",
            is_active=True,
        )
        session.add(batch)
        session.flush()
        session.add(StockBasic(market="SH", stock_code="600000", stock_name="浦发银行"))
        session.add(
            SignalEvent(
                batch_id=batch.id,
                market="SH",
                stock_code="600000",
                trade_date=batch.trade_date,
                rule_code="MACD_GOLDEN_CROSS",
                rule_version="v1",
                payload={},
            )
        )
        session.commit()
    yield factory
    factory.kw["bind"].dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient]:
    with TestClient(create_app(session_factory=session_factory)) as value:
        yield value


def test_screener_preset_name_is_unique(client: TestClient) -> None:
    payload = {"name": "我的强势方案", "conditions": {"minimum_score": 3}, "is_default": True}
    first = client.post("/api/v1/screener-presets", json=payload)
    repeated = client.post("/api/v1/screener-presets", json=payload)

    assert first.status_code == 201
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "PRESET_NAME_EXISTS"
    assert client.get("/api/v1/screener-presets").json()["items"][0]["name"] == "我的强势方案"
    preset_id = first.json()["id"]
    renamed = client.patch(
        f"/api/v1/screener-presets/{preset_id}",
        json={"name": "更新后的方案", "conditions": {"rsi_min": 50}},
    )
    assert renamed.json()["conditions"] == {"rsi_min": 50}
    assert client.post(f"/api/v1/screener-presets/{preset_id}/default").json()["is_default"]
    assert client.delete(f"/api/v1/screener-presets/{preset_id}").status_code == 204
    assert client.get("/api/v1/screener-presets").json()["items"] == []


def test_decision_note_update_soft_delete_and_restore(client: TestClient) -> None:
    created = client.post(
        "/api/v1/decision-notes",
        json={
            "market": "SH",
            "stock_code": "600000",
            "trade_date": "2025-03-31",
            "content": "观察放量是否延续",
        },
    )
    note_id = created.json()["id"]
    updated = client.patch(f"/api/v1/decision-notes/{note_id}", json={"content": "等待回踩确认"})
    deleted = client.delete(f"/api/v1/decision-notes/{note_id}")

    assert created.status_code == 201
    assert updated.json()["content"] == "等待回踩确认"
    assert deleted.status_code == 204
    assert client.get("/api/v1/decision-notes").json()["items"] == []
    historical = client.get("/api/v1/decision-notes?include_deleted=true").json()["items"]
    assert historical[0]["deleted_at"] is not None
    restored = client.post(f"/api/v1/decision-notes/{note_id}/restore")
    assert restored.json()["deleted_at"] is None


def test_rule_change_requires_confirmation_and_preserves_historical_signal(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    rejected = client.post(
        "/api/v1/rule-versions",
        json={"parameters": {"rsi_period": 12}, "confirm_recalculate": False},
    )
    created = client.post(
        "/api/v1/rule-versions",
        json={"parameters": {"rsi_period": 12}, "confirm_recalculate": True},
    )

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "RECALC_CONFIRMATION_REQUIRED"
    assert created.status_code == 201
    assert created.json()["version"] == "v2"
    with session_factory() as session:
        assert session.scalar(select(SignalEvent.rule_version)) == "v1"


def test_alert_rule_update_creates_new_version(client: TestClient) -> None:
    first = client.post(
        "/api/v1/alert-rules",
        json={"name": "RSI 过热", "rule_code": "CUSTOM_RSI", "threshold": 80, "enabled": True},
    )
    second = client.patch(
        f"/api/v1/alert-rules/{first.json()['logical_id']}",
        json={"threshold": 75, "enabled": False},
    )

    assert (first.json()["version"], second.json()["version"]) == (1, 2)
    rules = client.get("/api/v1/alert-rules?include_history=true").json()["items"]
    assert [(item["version"], item["threshold"]) for item in rules] == [(1, 80), (2, 75)]
    deleted = client.delete(f"/api/v1/alert-rules/{first.json()['logical_id']}")
    assert deleted.json()["version"] == 3
    assert deleted.json()["enabled"] is False


def test_settings_expose_defaults_and_can_update_schedule(client: TestClient) -> None:
    defaults = client.get("/api/v1/settings").json()
    updated = client.patch(
        "/api/v1/settings",
        json={"auto_sync_enabled": False, "auto_sync_time": "19:00"},
    ).json()

    assert defaults["auto_sync_time"] == "18:30"
    assert defaults["last_successful_batch"] == "2025-03-31"
    assert defaults["completeness_rate"] == 1
    assert defaults["failed_jobs"] == []
    assert updated["auto_sync_enabled"] is False
    assert updated["auto_sync_time"] == "19:00"
