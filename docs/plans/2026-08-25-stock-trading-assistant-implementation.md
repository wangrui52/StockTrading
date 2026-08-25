# A 股交易辅助决策工具实现计划

> **致 Claude：** 必须使用子技能 dev-executing-plans 逐任务执行此计划。

**目标：** 按 PRD V1.1 交付本地可运行、前后端隔离、具备测试和远程部署基础的 A 股日线辅助决策系统。

**架构：** FastAPI 后端集中封装数据、指标、信号和业务状态；React/TypeScript 前端通过 `/api/v1` REST interface 获取数据。SQLAlchemy 允许 SQLite 本地运行并为 PostgreSQL 预留，OpenAPI 是前后端契约事实源。

**技术栈：** Python 3.13、FastAPI、SQLAlchemy 2、Alembic、Pydantic 2、Pandas、AkShare、Pytest；React 19、TypeScript、Vite、TanStack Query、ECharts、Vitest、Testing Library、Playwright；Docker Compose、Nginx。

---

## 执行规则

每项生产行为均按“失败测试 → 确认失败 → 最小实现 → 全量测试 → 重构”的顺序执行。当前目录初始化为 Git 仓库后，每个可独立回滚的 task 单独提交中文 Conventional Commit。

### 任务 1：工程与测试基线

**文件：**
- 创建：`.gitignore`、`README.md`、`Makefile`
- 创建：`backend/pyproject.toml`、`backend/app/main.py`、`backend/tests/test_health.py`
- 创建：`frontend/package.json`、`frontend/vite.config.ts`、`frontend/src/app/App.tsx`、`frontend/src/app/App.test.tsx`

**步骤：**
1. 先创建后端 health 测试，运行 `uv run pytest tests/test_health.py -v`，确认因应用缺失失败。
2. 实现最小 FastAPI 应用，使 `/api/v1/health` 返回 `status=ok`。
3. 创建前端首屏测试，运行 `pnpm test --run`，确认因 App 缺失失败。
4. 实现带路由和 QueryClient 的最小应用壳，使测试通过。
5. 添加覆盖率配置，执行后端和前端全量测试。

### 任务 2：领域模型与指标引擎

**文件：**
- 创建：`backend/app/domain/market.py`
- 创建：`backend/app/domain/indicators.py`
- 创建：`backend/tests/unit/test_indicator_engine.py`

**步骤：**
1. 为 MA、EMA/MACD、Wilder RSI、BOLL、量比和样本不足逐个编写失败测试。
2. 每写一个公式都单独运行目标测试确认红灯。
3. 实现纯函数 `IndicatorEngine.calculate(series, params)`。
4. 使用固定金样本断言数值容差和初始化区间。
5. 运行 `uv run pytest --cov=app --cov-fail-under=70`。

### 任务 3：信号引擎

**文件：**
- 创建：`backend/app/domain/signals.py`
- 创建：`backend/tests/unit/test_signal_engine.py`

**步骤：**
1. 为状态型条件、跨日事件、连续满足不重复、趋势摘要、风险等级和冲突信号写失败测试。
2. 实现 `SignalEngine.evaluate(prices, indicators, rule_version)`。
3. 保证所有价格规则默认使用前复权数据，单日涨跌使用 raw 数据。
4. 运行单元测试和覆盖率门槛。

### 任务 4：数据库、迁移与 repository adapters

**文件：**
- 创建：`backend/app/infrastructure/database.py`
- 创建：`backend/app/infrastructure/models/*.py`
- 创建：`backend/app/ports/repositories.py`
- 创建：`backend/app/adapters/sqlalchemy_repositories.py`
- 创建：`backend/alembic.ini`、`backend/migrations/*`
- 创建：`backend/tests/integration/test_repositories.py`

**步骤：**
1. 先测试行情唯一键、批次激活、提醒确认保留和报告版本化。
2. 实现 PRD 第九章表模型和 repository interfaces。
3. 用事务实现当前批次原子切换。
4. 对 SQLite 运行迁移 up/down/up 和集成测试。

### 任务 5：数据源 adapter 与同步 pipeline

**文件：**
- 创建：`backend/app/ports/market_data.py`
- 创建：`backend/app/adapters/akshare_market_data.py`
- 创建：`backend/app/adapters/fake_market_data.py`
- 创建：`backend/app/application/sync_pipeline.py`
- 创建：`backend/tests/contract/test_market_data_contract.py`
- 创建：`backend/tests/integration/test_sync_pipeline.py`

**步骤：**
1. 以 fake adapter 为输入测试状态机、幂等、部分失败和不激活失败批次。
2. 定义小型 `MarketDataGateway` interface。
3. 实现 fake adapter 和同步 pipeline，让集成测试通过。
4. 使用冻结响应测试 AkShare 字段、单位和错误标准化。
5. 单独添加 `live` 标记测试真实接口，不让网络测试阻塞默认测试集。

### 任务 6：P0 查询与命令 REST interface

**文件：**
- 创建：`backend/app/api/v1/*.py`
- 创建：`backend/app/application/dashboard.py`
- 创建：`backend/app/application/screening.py`
- 创建：`backend/app/application/watchlist.py`
- 创建：`backend/app/application/reports.py`
- 创建：`backend/tests/contract/test_api_v1.py`

**步骤：**
1. 按 OpenAPI 响应模型为 health、状态、同步、看板、详情、筛选、自选、提醒、报告写失败契约测试。
2. 实现 application modules 和路由；统一错误结构。
3. 断言每个行情响应包含 `trade_date`、`batch_id`、`rule_version`。
4. 生成并保存 OpenAPI JSON，运行契约回归测试。

### 任务 7：前端 API client 与页面

**文件：**
- 创建：`frontend/src/shared/api/*`
- 创建：`frontend/src/features/dashboard/*`
- 创建：`frontend/src/features/stock-detail/*`
- 创建：`frontend/src/features/screener/*`
- 创建：`frontend/src/features/watchlist/*`
- 创建：`frontend/src/features/reports/*`
- 创建：对应 `*.test.tsx`

**步骤：**
1. 从 OpenAPI 生成 TypeScript 类型和 client。
2. 逐页先写 loading、empty、error、stale 和 success 状态测试。
3. 实现工具型首屏、导航、表格、筛选表单、K 线/指标图、自选和报告页面。
4. 验证窄屏无溢出、键盘可操作、无控制台错误。
5. 运行 `pnpm test --run --coverage`，门槛不低于 70%。

### 任务 8：P1 功能

**文件：**
- 创建：后端 `alert_rules.py`、`screening_presets.py`、`decision_notes.py`、`settings.py`
- 创建：前端对应 feature 目录和测试

**步骤：**
1. 分别为规则版本化、筛选方案唯一名、关注笔记软删除、设置重算确认写失败测试。
2. 实现后端 interface 和前端交互。
3. 验证修改规则不会改写历史事件。

### 任务 9：E2E 本地闭环

**文件：**
- 创建：`frontend/e2e/trading-flow.spec.ts`
- 创建：`backend/scripts/seed_demo.py`
- 创建：`deploy/docker-compose.dev.yml`

**步骤：**
1. 用固定数据启动 SQLite、后端和前端。
2. 编写同步 → 看板 → 筛选 → 详情 → 自选 → 提醒确认 → 报告导出的失败 E2E。
3. 修复真实工作流直至 Playwright 通过。
4. 保存桌面与窄屏截图、控制台和网络错误证据。

### 任务 10：远程部署基础与最终审计

**文件：**
- 创建：`deploy/backend.Dockerfile`、`deploy/frontend.Dockerfile`、`deploy/nginx.conf`、`deploy/docker-compose.yml`
- 创建：`docs/deployment.md`、`docs/operations.md`
- 更新：`README.md`

**步骤：**
1. 编写容器 healthcheck 和配置验证测试。
2. 构建并启动生产 Compose，验证静态前端、API 代理、持久卷和重启恢复。
3. 执行格式、类型、单元、集成、契约、E2E 和覆盖率全量 Gate。
4. 按 PRD 逐项更新 evidence matrix；任何 unknown 均不得声明完成。

