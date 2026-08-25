# A 股交易辅助决策工具

按照 `A股交易辅助决策工具_PRD_V1.1.md` 实现的个人日线研究工作台。系统完成“同步数据 → 查看市场 → 组合筛选 → 查看个股 → 加入自选 → 处理提醒 → 生成报告”闭环，不接实盘、不自动下单。

## 工程结构

- `backend/`：FastAPI、SQLAlchemy、Alembic、AkShare 适配器、指标/信号/候选引擎、自动调度与备份。
- `frontend/`：React、TypeScript、TanStack Query、ECharts，API 类型由 OpenAPI 生成。
- `deploy/`：Nginx 和 Docker Compose 部署配置。
- `docs/`：架构与部署运维说明。

## 本地启动

### 一键启动（推荐）

macOS 可在 Finder 中双击根目录的 `start_local.command`，也可以在终端运行：

```bash
./start_local.command
```

脚本会自动启动 Docker Desktop、构建服务、初始化演示数据并打开浏览器。已有数据库和配置不会被覆盖。启动成功后访问 `http://127.0.0.1:8080`。

### 源码开发方式

依赖 Python 3.13、`uv`、Node.js 22 和 `pnpm`。

```bash
make install
cd backend
uv run alembic upgrade head
uv run python scripts/seed_demo.py   # 可选：生成固定演示数据
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端：

```bash
cd frontend
pnpm dev
```

打开 `http://127.0.0.1:5173`。后端和前端默认都只监听本机回环地址。

## 验证

```bash
make test
cd frontend && pnpm typecheck && pnpm build
cd frontend && pnpm exec playwright test
```

## 容器运行

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
```

服务仅发布到 `127.0.0.1:8080`。远程服务器请通过 SSH 隧道访问；公网开放前必须另行增加认证、HTTPS、CSRF 防护和密钥管理。完整步骤见 [部署与运维](docs/deployment.md)。

本工具仅用于个人研究和信息整理，不构成投资建议。历史数据和技术指标不代表未来表现。
