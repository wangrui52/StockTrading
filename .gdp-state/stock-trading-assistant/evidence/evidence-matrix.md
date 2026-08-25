# Evidence Matrix

| Claim | Evidence | Status | Freshness | Risk |
|---|---|---|---|---|
| PRD V1.1 已存在 | `A股交易辅助决策工具_PRD_V1.1.md` | pass | fresh | low |
| 技术方案已确认 | ADR-001 与用户确认 | pass | fresh | low |
| 本机具备基础运行时 | Python/Node/uv/pnpm/Docker 版本输出 | pass | fresh | low |
| 前后端测试与构建基线可执行 | `evidence/T001-engineering-baseline.md` | pass | fresh | medium |
| PRD 指标公式已有统一实现 | `evidence/T002-indicator-engine.md` | pass | fresh | medium |
| PRD 内置信号与风险规则已有统一实现 | `evidence/T003-signal-engine.md` | pass | fresh | medium |
| 核心数据模型、迁移和事务仓储可用 | `evidence/T004-database-repositories.md` | pass | fresh | medium |
| 同步流水线与 AkShare 标准化可用 | `evidence/T005-sync-pipeline.md` | pass | fresh | medium |
| P0 REST 契约与 OpenAPI 快照稳定 | `evidence/T006-api-v1.md` | pass | fresh | medium |
| 前端 P0 工作台与 API 类型消费可用 | `evidence/T007-frontend-p0.md` | pass | fresh | medium |
| P1 版本化规则、方案、笔记与设置可用 | `evidence/T008-p1-features.md` | pass | fresh | medium |
| 前后端真实进程可启动 | `evidence/T009-e2e-prd-audit.md` | pass | fresh | low |
| PRD P0/P1 代码功能已实现 | PRD 逐项审计与 `evidence/T009-e2e-prd-audit.md` | pass | fresh | medium |
| 测试覆盖率达标 | 后端 91.67%，前端四项均高于 70% | pass | fresh | low |
| 本地 E2E 通过 | Chromium 桌面与 Pixel 7，共 2 passed | pass | fresh | low |
| 容器部署可用 | `evidence/T010-deployment.md` | pass | fresh | medium |
| 6,000 只全市场 AkShare 压测 | 尚未执行 | unknown | unknown | high |
| 连续 20 个交易日自动同步成功率 | 需真实运行周期观测 | unknown | unknown | medium |
