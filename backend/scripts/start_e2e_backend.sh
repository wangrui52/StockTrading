#!/bin/sh
set -eu

mkdir -p /tmp/mozi-test
E2E_RUNTIME_DIR="$(mktemp -d /tmp/mozi-test/stock-trading-e2e.XXXXXX)"
export DATABASE_URL="sqlite+pysqlite:///$E2E_RUNTIME_DIR/e2e.db"
uv run alembic upgrade head
uv run python scripts/seed_demo.py
exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
