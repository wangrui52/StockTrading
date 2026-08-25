# T004 数据库与仓储证据

## 红灯

- repository 测试首次因 `No module named 'app.adapters'` 失败。
- 首版活动批次唯一索引错误约束了 `false` 行，批次切换触发 `UNIQUE constraint failed`。
- 迁移测试首次因 Alembic 依赖与配置不存在而失败。

## 绿灯

- 16 张 PRD 核心表及复合唯一键已建模。
- SQLite 条件唯一索引只允许一个活动批次，失败批次不能替换旧活动批次。
- 同一信号重复写入保持幂等，已确认提醒状态不回退。
- 同一上下文重新生成报告递增版本且不覆盖旧内容。
- Alembic `upgrade head` 在空数据库创建核心表。
- 后端全量 27 passed，覆盖率 97%，Ruff 通过。

## 未验证

- 尚未在 PostgreSQL 验证并发激活与并发报告版本分配。
- 尚未实现生产数据库会话注入和 REST 接口。
