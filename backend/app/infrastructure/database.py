import os
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _create_engine(url: str, *, use_static_pool: bool = False) -> Engine:
    engine_options: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if use_static_pool:
        engine_options["poolclass"] = StaticPool
    engine = create_engine(url, **engine_options)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def create_sqlite_memory_session_factory() -> sessionmaker[Session]:
    engine = _create_engine("sqlite+pysqlite:///:memory:", use_static_pool=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_sqlite_session_factory(
    url: str | None = None,
) -> sessionmaker[Session]:
    url = url or os.getenv("DATABASE_URL", "sqlite+pysqlite:///./stock_trading.db")
    engine = _create_engine(url)
    return sessionmaker(bind=engine, expire_on_commit=False)
