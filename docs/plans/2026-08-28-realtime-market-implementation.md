# 全市场实时行情实现计划

**目标：** 点击按钮获取并查看沪深京全市场实时报价快照。

**架构：** 新浪股票池 + 腾讯分组报价；独立刷新任务和最新快照表；异步 API + React Query 轮询及分页列表。收盘日线链路保持独立。

**技术栈：** FastAPI、SQLAlchemy/SQLite、Alembic、requests、React/TypeScript、Vitest。

## 任务 1：报价适配与后端服务

- 扩展 `backend/tests/contract/test_tencent_market_data.py`，新增实时报价的字段、单位、异常和旧时间测试，先运行确认失败。
- 新建 `backend/app/ports/realtime.py`、`backend/app/adapters/tencent_realtime.py`，复用股票池并按最多 100 只分组取报价，网络请求有超时，组内坏数据不阻塞其他股票。
- 新建 `backend/tests/integration/test_realtime.py`，测试成功快照、低完整率保留、重复提交、重启恢复、日线表不被改动，先运行确认失败。
- 在 `backend/app/infrastructure/models.py` 增加独立任务/最新快照表；新增 Alembic 迁移。
- 新建 `backend/app/application/realtime.py` 实现任务状态和原子快照发布，在 `backend/app/main.py` 注入服务与恢复中断任务。
- 新建 `backend/app/api/v1/realtime_router.py` 暴露刷新、状态、分页快照接口及 Pydantic 类型。
- 运行 `uv run --no-sync pytest tests/contract/test_tencent_market_data.py tests/integration/test_realtime.py tests/integration/test_migrations.py`，期望通过。

## 任务 2：页面和契约

- 扩展 `frontend/src/app/App.test.tsx` 覆盖点击、失败旧快照、报价时间及日线隔离。
- 新建 `frontend/src/features/dashboard/RealtimePanel.tsx`，嵌入看板，空日线库也能显示按钮；搜索分页；请求失败保留旧数据；仅轮询状态，不自动发起采集。
- 更新全局说明，区分实时报价与日线分析。
- 导出 OpenAPI 并生成 TypeScript：`uv run --no-sync python -m scripts.export_openapi`、`pnpm generate:api`。
- 运行 `pnpm test --run`、`pnpm typecheck`、`pnpm build`。

## 任务 3：回归和真实验证

- 运行后端完整非 live 测试和 Ruff，迁移测试验证不改变已有日线数据。
- 构建并更新本地 backend/frontend，保留数据库卷，不停止 scheduler。
- 点击新增按钮，验证全市场采集、报价时间、翻页及搜索；对比日线批次保持不变。
- 补 README 使用说明与限制，记录真实请求结果及未验证边界。不执行 commit/push。

## 执行结果（2026-08-28）

- 后端非 live 回归：117 passed，1 deselected；保留 1 个第三方 Starlette/httpx 弃用提醒。Ruff 通过。
- 前端：23 passed；TypeScript 类型检查、生产构建通过。
- 报价适配器与服务组合定向覆盖率：96%（含分支）。
- 本机直接调用真实源：5550/5550 只成功，约 14 秒，无旧日期或无效价格项；内存数据库验证，不影响业务库。
- 本地 Docker 后端和前端已重建更新，保留原数据卷；新增表迁移成功。
- 浏览器实际点击：11:19:33 发起，11:19:52 完成，5550/5550 只；搜索 600000 显示浦发银行及 11:19:43 来源报价时间；翻页到 2/111 页成功，页面重新加载后快照仍可读取，无控制台错误。
- 日线激活批次仍为 #2、2026-08-27；实时报价未覆盖日线。最终页面恢复全市场列表。
- 未实际验证休市时段及真实停牌股票；旧报价、无有效价格和失败保留快照已由自动化测试覆盖。未承诺数据源零延迟或交易所级行情服务保障。
- 未执行 commit/push。
