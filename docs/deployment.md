# 部署与运维

## 1. 部署边界

默认方案使用单机 Docker Compose：Nginx 提供静态页面和同源 API 代理，FastAPI 只在容器网络中可见，SQLite、调度器和备份进程共享持久卷。宿主机端口固定绑定 `127.0.0.1`，因此不会直接暴露到局域网或公网。

## 2. 首次部署

服务器需安装 Docker Engine 和 Compose 插件。

```bash
git clone <repository-url> StockTrading
cd StockTrading
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
docker compose -f deploy/docker-compose.yml ps
curl http://127.0.0.1:8080/api/v1/health
```

后端容器启动前自动执行 Alembic 迁移。`backend` 健康后，`scheduler`、`backup` 和 `frontend` 才会启动。

## 3. 远程访问

推荐从个人电脑建立 SSH 隧道：

```bash
ssh -L 8080:127.0.0.1:8080 <user>@<server>
```

随后在本机打开 `http://127.0.0.1:8080`。不要把 Compose 端口改成 `0.0.0.0`。如果未来需要公网访问，应先增加身份认证、HTTPS、CSRF 防护、可信代理和密钥管理，再单独进行安全评审。

## 4. 自动同步

`scheduler` 每 30 秒检查一次系统设置。默认在 Asia/Shanghai 交易日 18:30 后触发一次 `AUTO` 同步；关闭自动同步、非交易日、当日已有自动任务时不会重复执行。手动同步与自动同步共用幂等流水线。

## 5. 备份与恢复

`backup` 每日 02:00 后使用 SQLite 在线备份 API 写入一个星期槽位，循环保留最近 7 份：

```text
/data/backups/stock-trading-weekday-0.db
...
/data/backups/stock-trading-weekday-6.db
```

查看持久卷和备份：

```bash
docker volume ls
docker compose -f deploy/docker-compose.yml exec backup ls -lh /data/backups
```

恢复会覆盖当前数据库，需先停止服务并由运维人员手动执行。建议恢复前额外复制当前数据库，再把选定备份复制为 `/data/stock_trading.db`，之后重新启动并检查 `/api/v1/system/status`。

## 6. 升级与回滚

```bash
git pull --ff-only
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs --tail=200 backend scheduler
```

数据库迁移前先确认当天备份可读。应用镜像可回滚到旧提交；数据库结构回滚应依据对应 Alembic 迁移评估，不自动执行破坏性 downgrade。

## 7. 常用检查

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail=200 backend
curl http://127.0.0.1:8080/api/v1/health
curl http://127.0.0.1:8080/api/v1/system/status
```

日志不得输出完整报告正文、关注笔记或潜在密钥。数据源异常时页面继续使用上次成功批次，并在系统设置显示失败阶段和失败清单。
