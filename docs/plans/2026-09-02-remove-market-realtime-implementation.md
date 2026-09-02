# 取消全市场实时报价实现计划

> **致 Claude：** 必须使用子技能 dev-executing-plans 逐任务执行此计划。

**目标：** 删除首页及后端的全市场实时报价能力，仅保留自选股定向实时报价。

**架构：** 前端首页移除全市场实时面板；后端的实时报价 scope 收敛为 `watchlist`，只使用任务创建时固化的自选代码。旧全市场快照保留在数据库中但不再暴露或更新，历史日线链路完全不变。

**技术栈：** FastAPI、SQLAlchemy、Pytest、React、TypeScript、TanStack Query、Vitest

---

### 任务 1：用测试锁定仅自选股范围

**文件：**
- 修改：`backend/tests/integration/test_realtime.py`
- 修改：`frontend/src/app/App.test.tsx`

**步骤：** 先把默认实时报价 API 的预期改为自选股，增加 `scope=market` 返回 422 的断言；删除全市场面板行为测试，增加首页无全市场入口的断言。运行后端和前端定向测试确认现状不满足新约束。

### 任务 2：后端收敛为自选股实时报价

**文件：**
- 修改：`backend/app/application/realtime.py`
- 修改：`backend/app/api/v1/realtime_router.py`
- 修改：`backend/app/infrastructure/models.py`

**步骤：** 将 `RealtimeScope` 收敛为 `Literal["watchlist"]`，默认 scope 改为 `watchlist`，只保留快照 id 2。`prepare()` 始终固化自选代码；`execute()` 只使用 `requested_symbols`，不调用 `gateway.list_stocks()`。运行后端定向测试。

### 任务 3：移除首页全市场面板

**文件：**
- 修改：`frontend/src/features/dashboard/DashboardPage.tsx`
- 删除：`frontend/src/features/dashboard/RealtimePanel.tsx`

**步骤：** 移除导入和渲染，删除无用组件，运行前端定向测试。

### 任务 4：同步契约与文档

**文件：**
- 修改：`backend/openapi.json`
- 修改：`frontend/src/shared/api/schema.d.ts`
- 修改：`README.md`
- 修改：`docs/deployment.md`

**步骤：** 重新生成 OpenAPI 与 TypeScript 类型，删除全市场实时报价说明，保留全市场历史日线同步说明，并静态搜索残留运行入口。

### 任务 5：完整离线验证

运行后端非 live 测试与 Ruff；运行前端测试、类型检查和生产构建；执行 `git diff --check` 和差异审查。不提交、不推送、不发起真实行情刷新。
