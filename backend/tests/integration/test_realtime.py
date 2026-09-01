from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.application.realtime import RealtimeService
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    Base,
    DataBatch,
    RealtimeRefresh,
    RealtimeSnapshot,
    SyncJob,
    WatchlistGroup,
    WatchlistItem,
)
from app.main import create_app
from app.ports.market_data import MarketDataUnavailable, StockRecord
from app.ports.realtime import RealtimeQuote

NOW = datetime(2026, 8, 28, 11, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


class Source:
    source = "fake-realtime"

    def __init__(self, count=3):
        self.stocks = [
            StockRecord("SH", f"{600000 + i}", f"股票{i}", None, None, False) for i in range(count)
        ]
        self.rows = [
            RealtimeQuote("SH", s.stock_code, s.name, 10, 1, 100, 1000, NOW) for s in self.stocks
        ]
        self.fail = False

    def list_stocks(self):
        if self.fail:
            raise MarketDataUnavailable("股票池暂时不可用")
        return self.stocks

    def quotes(self, symbols):
        return [r for r in self.rows if r.market.lower() + r.stock_code in symbols]


@pytest.fixture
def factory():
    result = create_sqlite_memory_session_factory()
    Base.metadata.create_all(result.kw["bind"])
    with result() as session:
        session.add(
            DataBatch(
                source="daily",
                trade_date=date(2026, 8, 27),
                rule_version="v1",
                status="READY",
                is_active=True,
            )
        )
        session.commit()
    yield result
    result.kw["bind"].dispose()


def service(factory, source=None):
    return RealtimeService(factory, source or Source(), clock=lambda: NOW, cooldown_seconds=0)


def refresh(service):
    job, execute = service.prepare()
    assert execute
    service.execute(job.id)
    return job.id


def test_snapshot_is_separate_from_daily_and_supports_pagination_search(factory):
    app = create_app(session_factory=factory)
    app.state.realtime = service(factory)
    client = TestClient(app)
    assert client.get("/api/v1/realtime/status").json()["snapshot"] is None
    assert client.post("/api/v1/realtime/refresh").status_code == 202
    status = client.get("/api/v1/realtime/status").json()
    assert status["job"]["status"] == "READY"
    assert status["snapshot"]["received_count"] == 3
    result = client.get("/api/v1/realtime/quotes?page=2&page_size=2").json()
    assert result["total"] == 3
    assert [q["stock_code"] for q in result["items"]] == ["600002"]
    assert client.get("/api/v1/realtime/quotes?q=股票1").json()["total"] == 1
    assert client.get("/api/v1/realtime/quotes?q=600001").json()["total"] == 1
    assert client.get("/api/v1/realtime/quotes?page_size=501").status_code == 422
    with factory() as session:
        assert session.scalar(select(func.count(DataBatch.id))) == 1
        assert session.scalar(select(DataBatch.trade_date)) == date(2026, 8, 27)
        assert session.scalar(select(DataBatch.is_active))
        assert session.scalar(select(func.count(SyncJob.id))) == 0


def test_repeat_click_and_cooldown_do_not_start_duplicate_work(factory):
    svc = RealtimeService(factory, Source(), clock=lambda: NOW)
    first, execute = svc.prepare()
    assert execute
    repeated, execute = svc.prepare()
    assert not execute and first.id == repeated.id
    svc.execute(first.id)
    repeated, execute = svc.prepare()
    assert not execute and first.id == repeated.id
    assert svc.status()["cooldown_until"] > NOW


@pytest.mark.parametrize("failure", ["source", "incomplete", "empty_pool", "future"])
def test_failed_refresh_retains_previous_snapshot(factory, failure):
    source = Source()
    svc = service(factory, source)
    old_id = refresh(svc)
    if failure == "source":
        source.fail = True
    elif failure == "incomplete":
        source.rows = source.rows[:1]
    elif failure == "empty_pool":
        source.stocks = []
    else:
        source.rows = [replace(r, quoted_at=NOW + timedelta(days=1)) for r in source.rows]
    refresh(svc)
    status = svc.status()
    assert status["job"]["status"] == "FAILED"
    assert status["job"]["error_summary"]
    assert status["snapshot"]["refresh_id"] == old_id
    assert len(svc.quotes()["items"]) == 3


def test_partial_snapshot_lists_missing_and_excludes_old_and_zero_quotes_from_summary(factory):
    source = Source(100)
    source.rows.pop()
    source.rows[0] = replace(source.rows[0], quoted_at=NOW - timedelta(days=1))
    source.rows[1] = replace(source.rows[1], latest_price=None, pct_change=None, volume=0, amount=0)
    svc = service(factory, source)
    refresh(svc)
    status = svc.status()
    assert status["job"]["status"] == "PARTIAL"
    meta = status["snapshot"]
    assert meta["missing_symbols"] == ["sh600099"]
    assert meta["stale_count"] == 1
    assert meta["unavailable_count"] == 1
    assert meta["market_summary"]["up"] == 97
    assert meta["market_summary"]["amount"] == 97000


def test_restart_marks_interrupted_job_failed_without_losing_snapshot(factory):
    svc = service(factory)
    old = refresh(svc)
    pending, _ = svc.prepare()
    svc.recover_interrupted()
    status = svc.status()
    assert status["job"]["id"] == pending.id
    assert status["job"]["status"] == "FAILED"
    assert status["snapshot"]["refresh_id"] == old
    assert svc.prepare()[1]


def test_only_latest_snapshot_is_retained(factory):
    svc = service(factory)
    refresh(svc)
    refresh(svc)
    with factory() as session:
        assert session.scalar(select(func.count(RealtimeSnapshot.id))) == 1
        assert session.scalar(select(func.count(RealtimeRefresh.id))) == 2


def test_concurrent_connections_claim_one_refresh(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from app.infrastructure.database import create_sqlite_session_factory

    factory = create_sqlite_session_factory(f"sqlite+pysqlite:///{tmp_path / 'realtime.db'}")
    Base.metadata.create_all(factory.kw["bind"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service(factory).prepare(), range(8)))
    assert sum(execute for _, execute in results) == 1
    assert len({job.id for job, _ in results}) == 1
    factory.kw["bind"].dispose()


def test_missing_quotes_are_retried_without_requesting_successes_again(factory):
    class RetrySource(Source):
        def __init__(self):
            super().__init__()
            self.calls = []

        def quotes(self, symbols):
            self.calls.append(symbols)
            rows = super().quotes(symbols)
            return rows[:1] if len(self.calls) == 1 else rows

    source = RetrySource()
    svc = service(factory, source)
    refresh(svc)
    assert source.calls == [["sh600000", "sh600001", "sh600002"], ["sh600001", "sh600002"]]
    assert svc.status()["job"]["status"] == "READY"


def watch(factory, codes):
    with factory() as session:
        if not session.get(WatchlistGroup, 1):
            session.add(WatchlistGroup(id=1, name="默认"))
        session.add_all([WatchlistItem(group_id=1, market="SH", stock_code=c) for c in codes])
        session.commit()


def test_watchlist_refresh_only_fetches_captured_watchlist_and_keeps_market_snapshot(factory):
    source = Source()
    svc = service(factory, source)
    market_id = refresh(svc)
    watch(factory, ["600000"])
    job, execute = svc.prepare(scope="watchlist")
    assert execute
    watch(factory, ["600001"])
    source.fail = True  # 自选报价不能请求全市场股票池。
    requested = []
    original = source.quotes
    source.quotes = lambda symbols: requested.extend(symbols) or original(symbols)
    svc.execute(job.id)
    assert requested == ["sh600000"]
    assert svc.status(scope="watchlist")["snapshot"]["received_count"] == 1
    assert svc.status()["snapshot"]["refresh_id"] == market_id
    assert svc.status()["snapshot"]["received_count"] == 3


def test_watchlist_and_market_refresh_have_separate_locks_and_cooldowns(factory):
    watch(factory, ["600000"])
    svc = RealtimeService(factory, Source(), clock=lambda: NOW)
    market, _ = svc.prepare()
    watched, execute = svc.prepare(scope="watchlist")
    assert execute and market.id != watched.id
    repeated, execute = svc.prepare(scope="watchlist")
    assert not execute and repeated.id == watched.id
    svc.execute(watched.id)
    assert svc.status()["job"]["status"] == "FETCHING"
    assert svc.status(scope="watchlist")["job"]["status"] == "READY"


def test_watchlist_api_empty_error_and_quote_enrichment(factory):
    app = create_app(session_factory=factory)
    app.state.realtime = service(factory)
    client = TestClient(app)
    result = client.post("/api/v1/realtime/refresh?scope=watchlist")
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "EMPTY_WATCHLIST"
    with factory() as session:
        assert session.scalar(select(func.count(RealtimeRefresh.id))) == 0
    watch(factory, ["600000"])
    before = client.get("/api/v1/watchlist/items").json()["items"][0]
    assert before["realtime"] is None
    assert client.post("/api/v1/realtime/refresh?scope=watchlist").status_code == 202
    after = client.get("/api/v1/watchlist/items").json()["items"][0]
    assert after["realtime"]["latest_price"] == 10
    assert after["realtime"]["quoted_at"] == "2026-08-28T11:10:00+08:00"
    assert {k: v for k, v in after.items() if k != "realtime"} == {
        k: v for k, v in before.items() if k != "realtime"
    }
    watch(factory, ["600001"])
    assert client.get("/api/v1/watchlist/items").json()["items"][1]["realtime"] is None
    assert client.get("/api/v1/realtime/status").json()["snapshot"] is None
    assert client.get("/api/v1/realtime/status?scope=bad").status_code == 422


def test_failed_watchlist_refresh_preserves_prior_quote(factory):
    watch(factory, ["600000"])
    source = Source()
    svc = service(factory, source)
    job, _ = svc.prepare(scope="watchlist")
    svc.execute(job.id)
    source.rows = []
    later, _ = svc.prepare(scope="watchlist")
    svc.execute(later.id)
    assert svc.status(scope="watchlist")["job"]["status"] == "FAILED"
    assert svc.status(scope="watchlist")["snapshot"]["refresh_id"] == job.id
