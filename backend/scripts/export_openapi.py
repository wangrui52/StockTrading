"""生成协议文件；使用内存数据库，不触碰本地业务数据。"""

import json
import os
from pathlib import Path

from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import Base


def main() -> None:
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    from app.main import create_app

    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])
    schema = create_app(session_factory=factory).openapi()
    Path("openapi.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
