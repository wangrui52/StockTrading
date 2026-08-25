# 本地一键启动脚本设计

## 目标

提供一个可在 macOS Finder 双击或终端直接执行的 `start_local.command`，自动启动本项目并打开浏览器，不要求用户记忆 Docker Compose 命令。

## 选定方案

采用 Docker Compose 一键启动。脚本以自身所在目录作为项目根目录，检查 Docker CLI；Docker daemon 未启动时拉起 Docker Desktop并限时等待。随后创建缺失的 `.env`、构建并启动四个服务、幂等执行演示数据初始化、验证健康接口，最后打开本地页面。

## 安全与幂等

- 不执行 `docker compose down -v`，不删除或覆盖已有 SQLite 数据卷。
- `.env` 已存在时不覆盖；缺失时从 `.env.example` 复制。
- 演示数据脚本检测到有效批次后直接返回，不重复插入。
- 任一步骤失败立即退出，并保留终端错误信息。
- 端口从 `.env` 的 `APP_PORT` 读取，缺失时使用 `8080`。

## 验证

- Shell 语法检查通过。
- Docker 已运行与未运行两条分支均可进入 Compose 启动。
- 连续执行两次不会重复演示数据，也不会删除已有数据。
- 健康接口返回成功后才打开浏览器。
