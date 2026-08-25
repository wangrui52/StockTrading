# T004 数据库与 repository

## 目标
建立可迁移的数据模型，并实现批次、信号、提醒状态和报告的关键事务语义。

## 对应 S/F
S2、S4、S5；F3。

## 约束
C001、C003、C005。

## 涉及文件
`backend/app/infrastructure/`、`backend/app/ports/`、`backend/app/adapters/sqlalchemy_repositories.py`、`backend/migrations/`、`backend/tests/integration/`。

## 执行记录
先写仓储集成测试确认缺失模块红灯；实现模型与仓储后由错误的活动索引再次得到真实红灯并修复；再写迁移测试确认缺失 Alembic 红灯，补初始迁移。

## 验证命令
`uv run pytest --cov=app --cov-report=term-missing`；`uv run ruff check app tests migrations`。

## 验证结果
27 passed；后端总覆盖率 97%；Ruff 通过。

## 遗留风险
PostgreSQL 并发事务与报告版本分配需部署阶段专项验证。

## 下一步
执行 T005 数据 adapter 与同步流水线。
