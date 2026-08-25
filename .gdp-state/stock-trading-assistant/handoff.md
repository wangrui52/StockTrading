# Handoff

## 当前目标
按 PRD V1.1 实现完整前后端、测试和部署基础。

## 当前 task
T007 前端 API client 与 P0 页面。

## 最近证据
T006 的 17 个 P0 API 路径、统一错误、活动批次上下文和 OpenAPI 快照回归测试通过；后端 39 passed、覆盖率 94%。

## 未完成事项
全部生产实现、测试和运行验证。

## 下一步建议
从 `backend/openapi.json` 生成前端类型，按 loading/empty/error/stale/success 状态实现看板、详情、筛选、自选、提醒和报告。

## 已知风险
真实 AkShare 单股小样本已通过，但全市场吞吐、限流和字段漂移仍需上线前压测；SQLite 不代表 PostgreSQL 并发行为。
