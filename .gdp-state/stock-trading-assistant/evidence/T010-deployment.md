# T010 部署与最终审计证据

## 构建与运行

- `docker compose config` 通过；宿主机仅 `127.0.0.1:8080` 暴露，后端只在容器网络监听。
- backend、scheduler、backup、frontend 四个镜像构建通过；生产容器使用 `uv run --no-sync`，不会启动时安装开发依赖。
- 四服务均保持 Up，后端 healthcheck 为 healthy；Nginx 同源代理 `/api/v1/health` 返回 200。
- 容器内执行 100×250 demo 同步后，看板返回交易日 `2025-03-31`、四个指数和候选结果；重启 backend 后同一批次仍可读取。

## 备份与迁移

- 七槽轮转备份单元测试通过。
- 容器备份文件 `PRAGMA integrity_check` 返回 `ok`，并包含当前 Alembic 版本。
- 已有 Docker 数据卷从旧版本在线升级到 `a9f3c8d2e710`，`operation_log` 表存在。
- 部署文档包含启动、SSH 隧道、备份恢复、升级回滚和公网开放前安全门禁。

## 运行边界

- 验证结束后已停止并移除本轮容器与网络；命名数据卷保留，未执行破坏性删除。
- 未执行真实远程服务器部署；这属于 PRD V1 的未来部署动作，不在本轮授权范围。
