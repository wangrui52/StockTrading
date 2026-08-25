# Definition of Done

## planned
- 目标契约、S/F、架构设计、实施计划和任务索引完整。

## implemented
- PRD P0/P1 功能均有生产实现，不含占位接口。

## locally_verified
- 前后端本地启动成功。
- 单元、集成、契约和 E2E 测试通过。
- 前后端覆盖率均不低于 70%，核心状态机和错误路径有业务断言。
- PRD 逐项证据矩阵无 unknown。

## test_env_verified
- 容器化环境启动并通过核心流程与重启恢复验证。

## cutover_ready
- 远程部署文档、安全配置和回滚步骤完成，但不代表已生产部署。

