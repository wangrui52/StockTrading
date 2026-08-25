.PHONY: install migrate test test-backend test-frontend test-e2e dev-backend dev-frontend build-frontend

install:
	cd backend && uv sync --dev
	cd frontend && pnpm install

migrate:
	cd backend && uv run alembic upgrade head

test: test-backend test-frontend

test-backend:
	cd backend && uv run pytest --cov=app --cov-report=term-missing

test-frontend:
	cd frontend && pnpm test --run && pnpm typecheck

test-e2e:
	cd frontend && pnpm exec playwright test

dev-backend:
	cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

dev-frontend:
	cd frontend && pnpm dev

build-frontend:
	cd frontend && pnpm build
