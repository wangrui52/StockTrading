from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.models import RealtimeRefresh, RealtimeSnapshot, WatchlistItem
from app.ports.market_data import MarketDataUnavailable
from app.ports.realtime import RealtimeGateway, RealtimeQuote

SHANGHAI = ZoneInfo("Asia/Shanghai")
RealtimeScope = Literal["watchlist"]
SNAPSHOT_IDS = {"watchlist": 2}


class EmptyWatchlistError(ValueError):
    pass


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RealtimeService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        gateway: RealtimeGateway,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cooldown_seconds: int = 30,
    ):
        self.factory, self.gateway = factory, gateway
        self.clock, self.cooldown_seconds = clock, cooldown_seconds

    def prepare(self, *, scope: RealtimeScope = "watchlist") -> tuple[RealtimeRefresh, bool]:
        if scope != "watchlist":
            raise ValueError("仅支持刷新自选股实时行情")
        now = utc(self.clock())
        with self.factory() as session:
            # SQLite 短事务抢占，网络请求均在事务外执行。
            session.execute(text("BEGIN IMMEDIATE"))
            latest = session.scalar(
                select(RealtimeRefresh)
                .where(RealtimeRefresh.scope == scope)
                .order_by(RealtimeRefresh.id.desc())
            )
            if latest and (
                latest.status == "FETCHING"
                or (
                    latest.finished_at
                    and now < utc(latest.finished_at) + timedelta(seconds=self.cooldown_seconds)
                )
            ):
                session.commit()
                return latest, False
            requested_symbols = sorted(
                {
                    item.market.lower() + item.stock_code
                    for item in session.scalars(select(WatchlistItem))
                }
            )
            if not requested_symbols:
                raise EmptyWatchlistError("自选股为空，请先加入自选股再刷新行情")
            job = RealtimeRefresh(started_at=now, scope=scope, requested_symbols=requested_symbols)
            session.add(job)
            session.commit()
            return job, True

    def recover_interrupted(self) -> None:
        with self.factory() as session:
            session.execute(
                update(RealtimeRefresh)
                .where(RealtimeRefresh.status == "FETCHING")
                .values(
                    status="FAILED",
                    stage="FAILED",
                    finished_at=utc(self.clock()),
                    error_summary="服务重启导致刷新中断，请重新刷新实时行情",
                )
            )
            session.commit()

    def _group(self, symbols: list[str]) -> list[RealtimeQuote]:
        # 请求失败或组内缺失最多补取一次；空响应也不能当成成功。
        rows: dict[str, RealtimeQuote] = {}
        remaining = symbols
        for _ in range(2):
            try:
                for row in self.gateway.quotes(remaining):
                    symbol = row.market.lower() + row.stock_code
                    if symbol in symbols and utc(row.quoted_at) <= utc(self.clock()) + timedelta(
                        minutes=5
                    ):
                        rows[symbol] = row
            except (MarketDataUnavailable, TimeoutError):
                pass
            remaining = [s for s in symbols if s not in rows]
            if not remaining:
                break
        return list(rows.values())

    def execute(self, job_id: int) -> None:
        with self.factory() as session:
            job = session.get(RealtimeRefresh, job_id)
            if not job or job.status != "FETCHING":
                return
            started_at = utc(job.started_at)
            scope = job.scope
            requested_symbols = job.requested_symbols
        try:
            if scope != "watchlist":
                raise MarketDataUnavailable("全市场实时行情已停用")
            # 名单在点击时固定，不读取全市场股票池，也不受采集中增删自选影响。
            symbols = requested_symbols
            if not symbols:
                raise MarketDataUnavailable("本次自选名单为空，未替换快照")
            self._progress(job_id, total_count=len(symbols), stage="QUOTES")
            groups = [symbols[i : i + 100] for i in range(0, len(symbols), 100)]
            quotes: list[RealtimeQuote] = []
            processed = 0
            with ThreadPoolExecutor(max_workers=4) as pool:
                for group, rows in zip(groups, pool.map(self._group, groups), strict=True):
                    quotes.extend(rows)
                    processed += len(group)
                    self._progress(
                        job_id, completed_count=processed, failed_count=processed - len(quotes)
                    )
            available = {q.market.lower() + q.stock_code for q in quotes}
            missing = [s for s in symbols if s not in available]
            if len(quotes) / len(symbols) < 0.99:
                raise MarketDataUnavailable(
                    f"实时报价仅获取 {len(quotes)}/{len(symbols)} 只，未达 99%，保留上次快照"
                )
            finished = utc(self.clock())
            today = finished.astimezone(SHANGHAI).date()
            current = [
                q
                for q in quotes
                if q.quoted_at.astimezone(SHANGHAI).date() == today
                and q.latest_price is not None
                and q.pct_change is not None
            ]
            summary = {
                "refresh_id": job_id,
                "scope": scope,
                "source": self.gateway.source,
                "started_at": started_at.isoformat(),
                "finished_at": finished.isoformat(),
                "quote_date": today.isoformat(),
                "total_count": len(symbols),
                "received_count": len(quotes),
                "missing_count": len(missing),
                "missing_symbols": missing,
                "stale_count": sum(
                    q.quoted_at.astimezone(SHANGHAI).date() != today for q in quotes
                ),
                "unavailable_count": sum(q.latest_price is None for q in quotes),
                "market_summary": {
                    "up": sum(q.pct_change > 0 for q in current),
                    "down": sum(q.pct_change < 0 for q in current),
                    "flat": sum(q.pct_change == 0 for q in current),
                    "amount": sum(q.amount for q in current),
                },
            }
            payload = [
                {**asdict(q), "quoted_at": q.quoted_at.isoformat()}
                for q in sorted(quotes, key=lambda q: (q.market, q.stock_code))
            ]
            with self.factory() as session:
                snapshot_id = SNAPSHOT_IDS[scope]
                snapshot = session.get(RealtimeSnapshot, snapshot_id)
                if snapshot:
                    snapshot.summary, snapshot.quotes = summary, payload
                else:
                    session.add(RealtimeSnapshot(id=snapshot_id, summary=summary, quotes=payload))
                job = session.get(RealtimeRefresh, job_id)
                job.status = "PARTIAL" if missing else "READY"
                job.stage, job.finished_at = job.status, finished
                session.commit()
        except Exception as error:
            # 不把网络请求详情/凭据带到前端；已知可操作错误只保留摘要。
            message = (
                str(error)
                if isinstance(error, MarketDataUnavailable)
                else "实时行情刷新失败，请重试"
            )
            self._progress(
                job_id,
                status="FAILED",
                stage="FAILED",
                finished_at=utc(self.clock()),
                error_summary=message[:300],
            )

    def _progress(self, job_id: int, **values: Any) -> None:
        with self.factory() as session:
            session.execute(
                update(RealtimeRefresh).where(RealtimeRefresh.id == job_id).values(**values)
            )
            session.commit()

    def status(self, *, scope: RealtimeScope = "watchlist") -> dict[str, Any]:
        with self.factory() as session:
            job = session.scalar(
                select(RealtimeRefresh)
                .where(RealtimeRefresh.scope == scope)
                .order_by(RealtimeRefresh.id.desc())
            )
            summary = session.scalar(
                select(RealtimeSnapshot.summary).where(RealtimeSnapshot.id == SNAPSHOT_IDS[scope])
            )
            cooldown = (
                utc(job.finished_at) + timedelta(seconds=self.cooldown_seconds)
                if job and job.finished_at
                else None
            )
            return {
                "job": self.job_payload(job) if job else None,
                "snapshot": summary,
                "cooldown_until": cooldown,
            }

    @staticmethod
    def job_payload(job: RealtimeRefresh) -> dict[str, Any]:
        return {
            "id": job.id,
            "scope": job.scope,
            "status": job.status,
            "stage": job.stage,
            "total_count": job.total_count,
            "completed_count": job.completed_count,
            "failed_count": job.failed_count,
            "error_summary": job.error_summary,
            "started_at": utc(job.started_at),
            "finished_at": utc(job.finished_at) if job.finished_at else None,
        }

    def quotes(
        self,
        *,
        q: str = "",
        page: int = 1,
        page_size: int = 50,
        scope: RealtimeScope = "watchlist",
    ) -> dict[str, Any]:
        with self.factory() as session:
            snapshot = session.get(RealtimeSnapshot, SNAPSHOT_IDS[scope])
            rows = snapshot.quotes if snapshot else []
            term = q.strip().casefold()
            if term:
                rows = [
                    r
                    for r in rows
                    if term in (r["market"] + r["stock_code"] + r["stock_name"]).casefold()
                ]
            start = (page - 1) * page_size
            return {
                "snapshot": snapshot.summary if snapshot else None,
                "items": rows[start : start + page_size],
                "total": len(rows),
                "page": page,
                "page_size": page_size,
            }
