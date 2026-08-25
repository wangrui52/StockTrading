# T006 P0 REST 接口证据

## 红灯

- API 契约测试首次因 `create_app` 不存在而失败。
- 首版仅有动态字典响应，补写 OpenAPI 必需字段回归测试后增加显式 Pydantic 响应模型。

## 绿灯

- health、系统状态、同步任务、看板、详情三类序列、筛选、自选、提醒、报告共 17 个路径进入 OpenAPI。
- 所有行情和分析 envelope 共享 `trade_date`、`batch_id`、`rule_version`。
- 自选增删、提醒确认、报告版本创建及 Markdown 导出契约通过。
- 业务 404 使用统一 `error.code/message/details` 结构。
- `openapi.json` 与运行时 schema 快照一致。
- 后端默认集 39 passed、1 skipped，覆盖率 94%，Ruff 通过。

## 未验证

- 尚未做浏览器端契约消费与真实前后端进程联调。
- 远程部署认证、HTTPS 和 CORS 仍属 T010 门禁。
