from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.v1.router import APIError, SessionDep
from app.infrastructure.models import (
    AlertRuleVersion,
    DataBatch,
    DecisionNote,
    RuleVersion,
    ScreenerPreset,
    SyncJob,
    SystemSetting,
)

router = APIRouter(prefix="/api/v1")


class PresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    conditions: dict[str, Any]
    is_default: bool = False


class PresetResponse(PresetRequest):
    id: int


class PresetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    conditions: dict[str, Any] | None = None
    is_default: bool | None = None


class PresetList(BaseModel):
    items: list[PresetResponse]


class NoteRequest(BaseModel):
    market: str
    stock_code: str
    trade_date: date
    content: str = Field(min_length=1)


class NoteUpdate(BaseModel):
    content: str = Field(min_length=1)


class NoteResponse(NoteRequest):
    id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class NoteList(BaseModel):
    items: list[NoteResponse]


class RuleVersionRequest(BaseModel):
    parameters: dict[str, Any]
    confirm_recalculate: bool


class RuleVersionResponse(BaseModel):
    id: int
    version: str
    parameters: dict[str, Any]
    requires_recalculation: bool


class AlertRuleRequest(BaseModel):
    name: str
    rule_code: str
    threshold: float
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    threshold: float
    enabled: bool


class AlertRuleResponse(AlertRuleRequest):
    id: int
    logical_id: int
    version: int


class AlertRuleList(BaseModel):
    items: list[AlertRuleResponse]


class SettingsUpdate(BaseModel):
    auto_sync_enabled: bool
    auto_sync_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class SettingsResponse(SettingsUpdate):
    adapter_version: str
    current_rule_version: str
    indicator_parameters: dict[str, int]
    last_successful_batch: date | None
    completeness_rate: float | None
    failed_jobs: list[dict[str, Any]]


@router.get("/screener-presets", response_model=PresetList)
def list_presets(db: SessionDep) -> dict[str, Any]:
    rows = db.scalars(select(ScreenerPreset).order_by(ScreenerPreset.id)).all()
    return {"items": [_preset(item) for item in rows]}


@router.post(
    "/screener-presets", status_code=status.HTTP_201_CREATED, response_model=PresetResponse
)
def create_preset(payload: PresetRequest, db: SessionDep) -> dict[str, Any]:
    if db.scalar(select(ScreenerPreset.id).where(ScreenerPreset.name == payload.name)):
        raise APIError(409, "PRESET_NAME_EXISTS", "筛选方案名称已存在")
    if payload.is_default:
        for item in db.scalars(select(ScreenerPreset).where(ScreenerPreset.is_default.is_(True))):
            item.is_default = False
    item = ScreenerPreset(**payload.model_dump())
    db.add(item)
    db.commit()
    return _preset(item)


@router.patch("/screener-presets/{preset_id}", response_model=PresetResponse)
def update_preset(preset_id: int, payload: PresetUpdate, db: SessionDep) -> dict[str, Any]:
    item = _require_preset(db, preset_id)
    if (
        payload.name
        and payload.name != item.name
        and db.scalar(select(ScreenerPreset.id).where(ScreenerPreset.name == payload.name))
    ):
        raise APIError(409, "PRESET_NAME_EXISTS", "筛选方案名称已存在")
    if payload.is_default:
        for other in db.scalars(select(ScreenerPreset).where(ScreenerPreset.is_default.is_(True))):
            other.is_default = False
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    return _preset(item)


@router.post("/screener-presets/{preset_id}/default", response_model=PresetResponse)
def default_preset(preset_id: int, db: SessionDep) -> dict[str, Any]:
    return update_preset(preset_id, PresetUpdate(is_default=True), db)


@router.delete("/screener-presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preset(preset_id: int, db: SessionDep) -> Response:
    db.delete(_require_preset(db, preset_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/decision-notes", response_model=NoteList)
def list_notes(db: SessionDep, include_deleted: bool = False) -> dict[str, Any]:
    query = select(DecisionNote).order_by(DecisionNote.id)
    if not include_deleted:
        query = query.where(DecisionNote.deleted_at.is_(None))
    return {"items": [_note(item) for item in db.scalars(query).all()]}


@router.post("/decision-notes", status_code=status.HTTP_201_CREATED, response_model=NoteResponse)
def create_note(payload: NoteRequest, db: SessionDep) -> dict[str, Any]:
    existing = db.scalar(
        select(DecisionNote.id).where(
            DecisionNote.market == payload.market,
            DecisionNote.stock_code == payload.stock_code,
            DecisionNote.trade_date == payload.trade_date,
            DecisionNote.deleted_at.is_(None),
        )
    )
    if existing:
        raise APIError(409, "ACTIVE_NOTE_EXISTS", "该股票交易日已有关注笔记")
    item = DecisionNote(**payload.model_dump())
    db.add(item)
    db.commit()
    return _note(item)


@router.patch("/decision-notes/{note_id}", response_model=NoteResponse)
def update_note(note_id: int, payload: NoteUpdate, db: SessionDep) -> dict[str, Any]:
    item = _require_note(db, note_id)
    item.content = payload.content
    item.updated_at = datetime.now(UTC)
    db.commit()
    return _note(item)


@router.delete("/decision-notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: int, db: SessionDep) -> Response:
    item = _require_note(db, note_id)
    item.deleted_at = datetime.now(UTC)
    item.updated_at = item.deleted_at
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/decision-notes/{note_id}/restore", response_model=NoteResponse)
def restore_note(note_id: int, db: SessionDep) -> dict[str, Any]:
    item = _require_note(db, note_id)
    item.deleted_at = None
    item.updated_at = datetime.now(UTC)
    db.commit()
    return _note(item)


@router.post(
    "/rule-versions", status_code=status.HTTP_201_CREATED, response_model=RuleVersionResponse
)
def create_rule_version(payload: RuleVersionRequest, db: SessionDep) -> dict[str, Any]:
    if not payload.confirm_recalculate:
        raise APIError(409, "RECALC_CONFIRMATION_REQUIRED", "修改指标参数必须确认重新计算")
    count = db.scalar(select(func.count(RuleVersion.id))) or 0
    item = RuleVersion(
        version=f"v{count + 2}",
        parameters=payload.parameters,
        requires_recalculation=True,
    )
    db.add(item)
    db.commit()
    return _rule_version(item)


@router.get("/alert-rules", response_model=AlertRuleList)
def list_alert_rules(db: SessionDep, include_history: bool = False) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(AlertRuleVersion).order_by(AlertRuleVersion.logical_id, AlertRuleVersion.version)
        )
    )
    if not include_history:
        latest: dict[int, AlertRuleVersion] = {}
        for item in rows:
            latest[item.logical_id] = item
        rows = list(latest.values())
    return {"items": [_alert_rule(item) for item in rows]}


@router.post("/alert-rules", status_code=status.HTTP_201_CREATED, response_model=AlertRuleResponse)
def create_alert_rule(payload: AlertRuleRequest, db: SessionDep) -> dict[str, Any]:
    logical_id = (db.scalar(select(func.max(AlertRuleVersion.logical_id))) or 0) + 1
    item = AlertRuleVersion(logical_id=logical_id, version=1, **payload.model_dump())
    db.add(item)
    db.commit()
    return _alert_rule(item)


@router.patch("/alert-rules/{logical_id}", response_model=AlertRuleResponse)
def update_alert_rule(logical_id: int, payload: AlertRuleUpdate, db: SessionDep) -> dict[str, Any]:
    previous = db.scalar(
        select(AlertRuleVersion)
        .where(AlertRuleVersion.logical_id == logical_id)
        .order_by(AlertRuleVersion.version.desc())
    )
    if previous is None:
        raise APIError(404, "ALERT_RULE_NOT_FOUND", "提醒规则不存在")
    item = AlertRuleVersion(
        logical_id=logical_id,
        version=previous.version + 1,
        name=previous.name,
        rule_code=previous.rule_code,
        threshold=payload.threshold,
        enabled=payload.enabled,
    )
    db.add(item)
    db.commit()
    return _alert_rule(item)


@router.delete("/alert-rules/{logical_id}", response_model=AlertRuleResponse)
def delete_alert_rule(logical_id: int, db: SessionDep) -> dict[str, Any]:
    previous = _latest_alert_rule(db, logical_id)
    item = AlertRuleVersion(
        logical_id=logical_id,
        version=previous.version + 1,
        name=previous.name,
        rule_code=previous.rule_code,
        threshold=previous.threshold,
        enabled=False,
    )
    db.add(item)
    db.commit()
    return _alert_rule(item)


@router.get("/settings", response_model=SettingsResponse)
def get_settings(db: SessionDep) -> dict[str, Any]:
    return _settings(db)


@router.patch("/settings", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdate, db: SessionDep) -> dict[str, Any]:
    item = db.get(SystemSetting, "application")
    value = payload.model_dump()
    if item is None:
        item = SystemSetting(key="application", value=value)
        db.add(item)
    else:
        item.value = {**item.value, **value}
    db.commit()
    return _settings(db)


def _settings(db: SessionDep) -> dict[str, Any]:
    item = db.get(SystemSetting, "application")
    batch = db.scalar(select(DataBatch).where(DataBatch.is_active.is_(True)))
    failed = db.scalars(
        select(SyncJob).where(SyncJob.status == "FAILED").order_by(SyncJob.id.desc()).limit(5)
    )
    defaults = {
        "auto_sync_enabled": True,
        "auto_sync_time": "18:30",
        "adapter_version": "akshare-1.18.94",
        "current_rule_version": batch.rule_version if batch else "v1",
        "indicator_parameters": {"rsi_period": 14, "boll_period": 20},
        "last_successful_batch": batch.trade_date if batch else None,
        "completeness_rate": batch.completeness_rate if batch else None,
        "failed_jobs": [
            {
                "id": job.id,
                "target_trade_date": job.target_trade_date,
                "stage": job.stage,
                "error_summary": job.error_summary,
                "retry_count": job.retry_count,
            }
            for job in failed
        ],
    }
    return {**defaults, **(item.value if item else {})}


def _require_note(db: SessionDep, note_id: int) -> DecisionNote:
    item = db.get(DecisionNote, note_id)
    if item is None:
        raise APIError(404, "NOTE_NOT_FOUND", "关注笔记不存在")
    return item


def _require_preset(db: SessionDep, preset_id: int) -> ScreenerPreset:
    item = db.get(ScreenerPreset, preset_id)
    if item is None:
        raise APIError(404, "PRESET_NOT_FOUND", "筛选方案不存在")
    return item


def _latest_alert_rule(db: SessionDep, logical_id: int) -> AlertRuleVersion:
    item = db.scalar(
        select(AlertRuleVersion)
        .where(AlertRuleVersion.logical_id == logical_id)
        .order_by(AlertRuleVersion.version.desc())
    )
    if item is None:
        raise APIError(404, "ALERT_RULE_NOT_FOUND", "提醒规则不存在")
    return item


def _preset(item: ScreenerPreset) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "conditions": item.conditions,
        "is_default": item.is_default,
    }


def _note(item: DecisionNote) -> dict[str, Any]:
    return {
        "id": item.id,
        "market": item.market,
        "stock_code": item.stock_code,
        "trade_date": item.trade_date,
        "content": item.content,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "deleted_at": item.deleted_at,
    }


def _rule_version(item: RuleVersion) -> dict[str, Any]:
    return {
        "id": item.id,
        "version": item.version,
        "parameters": item.parameters,
        "requires_recalculation": item.requires_recalculation,
    }


def _alert_rule(item: AlertRuleVersion) -> dict[str, Any]:
    return {
        "id": item.id,
        "logical_id": item.logical_id,
        "version": item.version,
        "name": item.name,
        "rule_code": item.rule_code,
        "threshold": item.threshold,
        "enabled": item.enabled,
    }
