# Handoff

## 当前目标
按 PRD V1.1 实现完整前后端、测试和部署基础。

## 当前 task
T005 数据 adapter 与同步流水线。

## 最近证据
T004 的 16 张核心表、Alembic 初始化迁移、批次原子激活、信号幂等与报告版本测试通过；后端 27 passed、覆盖率 97%。

## 未完成事项
全部生产实现、测试和运行验证。

## 下一步建议
先用可重复的假 MarketDataGateway 为同步批次的成功、失败、完整率与旧批次保护写失败测试，再接 AkShare adapter。

## 已知风险
AkShare 接口需后续 live Spike；SQLite 不代表 PostgreSQL 并发行为，默认测试必须可离线重复。
