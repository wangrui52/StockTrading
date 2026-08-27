from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.application.sync_pipeline import SyncInProgressError, SyncResult
from app.infrastructure.models import SyncJob, SystemSetting


class DailySyncScheduler:
    """供独立调度进程调用；多次 tick 对同一自然日保持幂等。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        is_trade_date: Callable[[object], bool],
        run_sync: Callable[..., SyncResult],
    ) -> None:
        self.session_factory = session_factory
        self.is_trade_date = is_trade_date
        self.run_sync = run_sync

    def tick(self, now: datetime) -> bool:
        local_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
        target = local_now.date()
        with self.session_factory() as session:
            setting = session.get(SystemSetting, "application")
            values = setting.value if setting else {}
            if not values.get("auto_sync_enabled", True):
                return False
            scheduled = values.get("auto_sync_time", "18:30")
            if local_now.strftime("%H:%M") < scheduled:
                return False
            existing = session.scalar(
                select(SyncJob)
                .where(
                    SyncJob.job_type == "AUTO",
                    SyncJob.target_trade_date == target,
                )
                .order_by(SyncJob.id.desc())
            )
            if existing is not None and (
                existing.status != "FAILED"
                or (
                    existing.finished_at
                    and local_now.replace(tzinfo=None)
                    - existing.finished_at.replace(tzinfo=UTC)
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .replace(tzinfo=None)
                    < timedelta(minutes=5)
                )
            ):
                return False
        try:
            if not self.is_trade_date(target):
                return False
            self.run_sync(target, job_type="AUTO")
            return True
        except SyncInProgressError:
            return False
        except Exception as error:
            with self.session_factory() as session:
                latest = session.scalar(
                    select(SyncJob)
                    .where(
                        SyncJob.job_type == "AUTO",
                        SyncJob.target_trade_date == target,
                    )
                    .order_by(SyncJob.id.desc())
                )
                if latest is None or latest.status != "FAILED":
                    session.add(
                        SyncJob(
                            job_type="AUTO",
                            target_trade_date=target,
                            status="FAILED",
                            stage="CALENDAR",
                            error_summary=str(error)[:300],
                            finished_at=now,
                        )
                    )
                    session.commit()
            return False
