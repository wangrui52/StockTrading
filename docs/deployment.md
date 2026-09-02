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

`scheduler` 每 30 秒检查一次系统设置。默认在 Asia/Shanghai 交易日 18:30 后触发一次 `AUTO` 同步；关闭自动同步、非交易日、当日已有成功自动任务时不会重复执行。失败任务间隔至少 5 分钟重试，异常不使调度进程退出。手动同步与自动同步共用跨进程互斥、幂等流水线。

首页始终提供同步按钮，`POST /api/v1/sync-jobs` 请求体 `{}` 由后端选取最新已收盘交易日（上海时间 15:00 前取上一交易日）；仍支持显式 `target_trade_date`。已有成功批次时仍可同步。来源尚未发布当天数据时记录缺口，不拿旧日线伪造停牌数据，完整率不足 99% 时保留旧激活批次。

首次使用新浪完整 A 股股票池（沪、深、京），每只拉取腾讯最多约 750 个交易日的未复权和前复权历史；小并发拉取、逐股持久化和计算，避免全市场历史常驻内存。后续仅在同来源且日期连续时增量同步，前复权历史修订触发完整回拉。演示批次、失败批次的历史和信号不会混入正式批次；同来源成功批次的提醒确认状态保留。

上述完整股票池仅用于收盘日线研究批次。盘中实时报价不提供全市场刷新，只在自选股页面按点击时固化的自选名单查询，不影响日线批次。

字段边界：金额统一为元、成交量统一为股（腾讯科创板与其他市场原始单位不同）；当日涨跌幅使用日期与收盘价匹配的官方快照，历史缺少除权参考价时留空，不用相邻未复权价格推算。新浪股票池缺少的行业、上市日期保持空值。公开源可限流或延迟，失败清单在系统状态中可查。

覆盖范围以新浪 `hs_a` 股票池为准，不包含该池未列出的证券。2026-08-27 实测该池 5,549 只，未包含 689009 等 CDR；适配器虽支持 688/689 的股单位解析，不代表这些证券都已纳入同步池。完整率的分母是该来源股票池，而非交易所所有证券。

ARM64 镜像使用 `mini-racer==0.14.1`，通过 uv override 排除 AkShare 间接引入的旧 `py-mini-racer`。交易日历网络请求有 15 秒超时，解码器显式关闭。

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

升级前先确认当前没有正在执行的同步任务；不要在同步中单独重启后端或调度器。当前是单实例调度部署，启动恢复不提供跨进程租约判断。

## 7. 常用检查

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail=200 backend
curl http://127.0.0.1:8080/api/v1/health
curl http://127.0.0.1:8080/api/v1/system/status
```

日志不得输出完整报告正文、关注笔记或潜在密钥。数据源异常时页面继续使用上次成功批次，并在系统设置显示失败阶段和失败清单。
