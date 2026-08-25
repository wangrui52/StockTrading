# A 股交易辅助决策工具技术设计

> 日期：2026-08-25  
> 状态：已确认  
> 依据：[A股交易辅助决策工具_PRD_V1.1.md](../../A股交易辅助决策工具_PRD_V1.1.md)

## 1. 设计目标

交付一个本地可运行、未来可部署远程服务器的 A 股日线辅助决策系统。前后端在源码、依赖、构建和部署上隔离，通过版本化 REST interface 通信；后端集中处理数据同步、指标、信号、筛选、提醒和报告，前端只负责交互与呈现。

## 2. 方案比较与结论

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| FastAPI + React + SQLite | 与 AkShare/Pandas 生态匹配，前后端清晰，易迁移 PostgreSQL | 两套语言和工具链 | 采用 |
| Django + React | ORM 和后台管理成熟 | 对单用户工具偏重 | 不采用 |
| Node.js + React | 全栈 TypeScript | AkShare 和指标计算需要额外 Python seam | 不采用 |

用户已确认采用第一种方案，并授权后续可逆技术决策默认采用推荐项。

## 3. 总体架构

```text
Browser
  │
  │ HTTP / JSON (/api/v1)
  ▼
React + TypeScript (frontend)
  │
  ▼
FastAPI (backend/api)
  │
  ├── Application modules
  │     ├── SyncPipeline
  │     ├── ScreeningModule
  │     ├── WatchlistModule
  │     ├── AlertModule
  │     └── ReportModule
  │
  ├── Domain modules
  │     ├── IndicatorEngine
  │     └── SignalEngine
  │
  └── Adapters
        ├── AkShareMarketDataAdapter
        ├── SQLAlchemy repositories
        └── FakeMarketDataAdapter (tests)

SQLite (local) / PostgreSQL (future)
```

## 4. 仓库结构

```text
StockTrading/
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   ├── application/
│   │   ├── domain/
│   │   ├── ports/
│   │   ├── adapters/
│   │   ├── infrastructure/
│   │   └── main.py
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── contract/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── features/
│   │   ├── shared/
│   │   └── test/
│   ├── e2e/
│   └── package.json
├── deploy/
│   ├── backend.Dockerfile
│   ├── frontend.Dockerfile
│   ├── nginx.conf
│   └── docker-compose.yml
├── docs/
└── Makefile
```

前端不能导入后端源码，后端不能依赖前端构建产物。共享契约以 OpenAPI 为事实源，由前端生成 TypeScript 类型。

## 5. Deep modules 与 seam

### 5.1 `MarketDataGateway`

Interface 只暴露股票列表、交易日历、个股日线和指数日线。AkShare 字段、重试、单位转换和异常藏在 adapter 内。测试使用同一 interface 的 fake adapter。

### 5.2 `IndicatorEngine`

纯函数 interface：输入按交易日升序的标准行情和规则参数，输出指标序列及样本不足原因。它不访问数据库、不调用 AkShare、不产生提醒。

### 5.3 `SignalEngine`

输入单只股票的行情和指标序列，输出状态型信号、事件型信号、趋势摘要和风险等级。筛选、提醒、详情、报告统一读取其结果。

### 5.4 `SyncPipeline`

Interface 只接受目标交易日和触发方式，负责获取、标准化、校验、计算、生成信号和原子激活批次。并发、幂等和失败恢复留在 module 内。

### 5.5 存储 seam

Application modules 依赖 repository interfaces；SQLAlchemy 是 adapter。SQLite 和 PostgreSQL 通过相同模型与迁移管理，不在业务逻辑中分支判断数据库类型。

## 6. 数据流

```text
AkShare raw response
  → MarketDataGateway 标准化
  → staging batch
  → 数据质量校验
  → IndicatorEngine
  → SignalEngine
  → candidate / alert materialization
  → transactionally activate batch
  → REST queries
  → React Query cache
  → UI
```

失败批次永不覆盖当前有效批次。提醒确认、自选分组和报告版本与派生批次分离，重新同步不得丢失用户状态。

## 7. REST interface

统一前缀 `/api/v1`：

- `GET /health`
- `GET /system/status`
- `POST /sync-jobs`
- `GET /sync-jobs/{id}`
- `GET /dashboard`
- `GET /stocks/{market}/{code}`
- `GET /stocks/{market}/{code}/prices`
- `GET /stocks/{market}/{code}/indicators`
- `GET /stocks/{market}/{code}/signals`
- `POST /screenings`
- `GET/POST/DELETE /watchlist/items`
- `GET /alerts`、`POST /alerts/{id}/confirm`
- `POST /reports`、`GET /reports/{id}`、`GET /reports/{id}/export`
- P1：`/alert-rules`、`/screening-presets`、`/decision-notes`、`/settings`

错误响应统一为 `code`、`message`、`details`、`request_id`。所有行情响应包含 `trade_date`、`batch_id` 和 `rule_version`。

## 8. 前端设计

页面为 `/` 看板、`/stocks/:market/:code` 详情、`/screener` 筛选、`/watchlist` 自选、`/reports/:id` 报告、`/settings` 设置。

使用 React Router、TanStack Query、ECharts 和 CSS variables。首屏必须是可用看板，不做营销页。每个页面覆盖 loading、empty、error、stale、insufficient-data 状态。桌面优先，同时保证窄屏不溢出。

## 9. 错误处理与可观测性

- 外部数据错误转换为 typed domain errors，不直接把 AkShare 异常暴露给前端。
- 同步任务保存阶段、进度、失败股票和错误摘要。
- API 记录 request id、耗时和错误类型，不记录笔记或报告正文。
- 前端展示可恢复操作：重试、查看旧批次、查看失败清单。

## 10. 测试策略

- 后端单元测试：指标公式、信号边界、风险等级、排序与模板。
- 后端集成测试：SQLite repository、事务激活、幂等和 REST interface。
- 数据 adapter 契约测试：用冻结样本验证字段、单位和异常映射；真实 AkShare 测试单独标记为 live。
- 前端单元/组件测试：状态展示、筛选条件、表格排序和交互。
- E2E：同步 fake 数据 → 看板 → 筛选 → 详情 → 自选 → 确认提醒 → 报告导出。
- 覆盖率门槛：前后端各不低于 70%，并且 P0 状态机和错误路径必须有业务断言。

所有生产行为遵循红—绿—蓝循环；先看到目标测试失败，再写最小实现。

## 11. 本地与远程部署

本地开发分别启动 backend 和 frontend，Vite 代理 `/api` 到 FastAPI。生产使用 Docker Compose：Nginx 提供前端静态资源并反向代理 API，后端使用环境变量选择数据库。默认本地只监听 `127.0.0.1`；远程部署必须显式开启认证、HTTPS 和受控 CORS。

## 12. 非目标

V1 不实现自动交易、分钟级行情、AI 预测、回测、多用户和移动 App。不会为了提前展示页面而复制指标或信号逻辑到前端。

