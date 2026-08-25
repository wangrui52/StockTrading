from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def create_sqlite_memory_session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_sqlite_session_factory(
    url: str = "sqlite+pysqlite:///./stock_trading.db",
) -> sessionmaker[Session]:
    engine = create_engine(url, connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine, expire_on_commit=False)
