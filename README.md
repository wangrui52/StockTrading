# StockTrading · A 股交易辅助决策工具

按照 `A股交易辅助决策工具_PRD_V1.1.md` 实现的个人日线研究工作台。系统完成“同步数据 → 查看市场 → 组合筛选 → 查看个股 → 加入自选 → 处理提醒 → 生成报告”闭环，不接实盘、不自动下单。

## 主要功能

| 模块 | 能力 |
| --- | --- |
| 行情看板 | 查看市场指数、候选股票、数据日期与来源，手动同步最新已收盘交易日 |
| 股票筛选 | 组合价格、涨跌幅、成交额、RSI、均线和 MACD 条件，分页查看命中原因 |
| 个股详情 | K 线、成交量、MA、MACD、RSI、近期信号与关注笔记 |
| 自选与提醒 | 管理自选股，查看并确认规则触发的提醒 |
| 分析报告 | 按规则生成个股报告，保留交易日、批次和版本，导出 Markdown |
| 系统运维 | 同步进度与失败记录、自动同步设置、数据库迁移和定期备份 |

报告由程序规则生成，无需配置大模型 API Key。

## 技术栈

- 后端：Python 3.13、FastAPI、SQLAlchemy、Alembic、SQLite、Pandas。
- 数据接入：新浪股票池与交易日历、腾讯日线，保留 AkShare 适配器与离线测试适配器。
- 前端：React 19、TypeScript、Vite、TanStack Query、ECharts。
- 测试与部署：pytest、Vitest、Playwright、Docker Compose、Nginx。

## 工程结构

```text
StockTrading/
├── backend/
│   ├── app/                  # API、应用服务、领域计算、数据适配器与持久化
│   ├── migrations/           # Alembic 数据库迁移
│   ├── scripts/              # 调度、备份、演示数据和 OpenAPI 导出
│   └── tests/                # 单元、集成、契约和显式启用的网络测试
├── frontend/
│   ├── src/features/         # 看板、筛选、详情、自选、报告和设置页面
│   ├── src/shared/api/       # API 客户端与生成的协议类型
│   └── e2e/                  # 浏览器端到端测试
├── deploy/                   # Docker Compose 与 Nginx 配置
├── docs/                     # 部署说明和设计、实施记录
├── tests/                    # 本地启动脚本测试
├── .env.example              # 本地端口配置示例
├── Makefile                  # 开发与测试命令
└── start_local.command       # macOS 一键启动入口
```

## 本地启动

先获取源码：

```bash
git clone https://github.com/wangrui52/StockTrading.git
cd StockTrading
```

### 一键启动（推荐）

先安装 Docker Desktop。macOS 可在 Finder 中双击根目录的 `start_local.command`，也可以在终端运行：

```bash
./start_local.command
```

脚本会自动启动 Docker Desktop、构建服务并打开浏览器。已有数据库和配置不会被覆盖。启动成功后访问 `http://127.0.0.1:8080`，点击「同步最新交易日」（空库为「同步数据」）获取真实行情。首次同步需拉取全市场历史日线，页面会显示进度；只有完整率达到 99% 的批次才会替换当前数据。

默认不写入演示行情。如需演示，在没有激活批次的数据库上显式运行：

```bash
START_LOCAL_DEMO=1 ./start_local.command
```

演示数据为截至 **2025-03-31** 的固定样本，不是实时行情；页面会标明「演示数据（非真实行情）」。已有激活批次时脚本不会覆盖数据。依赖和镜像准备完成后，演示样本生成不依赖行情网络。

设置 `START_LOCAL_NO_OPEN=1` 可禁止脚本自动打开浏览器。停止服务使用以下命令，命名数据卷会保留：

```bash
docker compose -f deploy/docker-compose.yml down
```

真实来源为新浪 A 股股票池和交易日历、腾讯未复权/前复权日线。后端按上海时区选取最新已收盘交易日，盘中或休市日使用上一交易日，不是实时盘口。自动同步默认在交易日 18:30 运行。

### 源码开发方式

依赖 Python 3.13、`uv`、Node.js 22 和 `pnpm`。

```bash
make install
cd backend
uv run alembic upgrade head
uv run python -m scripts.seed_demo   # 可选：为空库生成固定演示数据
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端：

```bash
cd frontend
pnpm dev
```

打开 [开发页面](http://127.0.0.1:5173)，API 文档位于 [Swagger UI](http://127.0.0.1:8000/docs)。后端和前端默认都只监听本机回环地址，Vite 会把 `/api` 请求代理到后端。

源码方式不会自动启动调度器；需要自动同步时，在另一个终端执行：

```bash
cd backend
uv run python -m scripts.run_scheduler
```

默认数据库为 `backend/stock_trading.db`，可通过 `DATABASE_URL` 指定其他 SQLite 路径。Docker 部署会自动运行调度器和备份服务。

## 数据口径与限制

- 只处理已收盘交易日的日线，不提供盘中实时盘口。自动同步默认于上海时间交易日 18:30 运行。
- 当前股票覆盖范围取决于新浪 `hs_a` 股票池，不等同于交易所全部证券；该池未列出的证券不会进入同步完整率分母。
- 展示价格采用未复权数据，技术图表和指标采用前复权数据；成交额统一为元，成交量统一为股。
- 新批次只有完整率达到 99% 才会激活。同步失败时保留上一次成功批次，不以旧日线伪造当天行情。
- 公共数据源可能限流、延迟或缺失字段；缺失行业、上市日期等信息保持空值。首次全市场历史同步可能耗时较长。
- 演示批次与真实批次按来源区分。日期固定在 2025-03-31 时，请先检查页面来源标记和同步任务状态。

## 验证

在项目根目录执行后端测试、前端单元测试和类型检查：

```bash
make test
make build-frontend
```

启动脚本测试使用模拟命令，不会启动真实 Docker：

```bash
zsh tests/test_start_local_script.sh
zsh tests/test_start_local_docker_recovery.sh
```

浏览器测试首次运行需要安装 Chromium，并确保 8000、5173 端口没有被其他服务占用：

```bash
cd frontend
pnpm exec playwright install chromium
pnpm test:e2e
```

默认后端测试跳过真实网络测试。`RUN_LIVE_TESTS=1` 可显式启用现有 AkShare 网络用例，但它不能替代当前腾讯数据链路的完整同步验收。

后端协议调整后，重新生成 OpenAPI 和前端类型：

```bash
cd backend
uv run python -m scripts.export_openapi
cd ../frontend
pnpm generate:api
```

## 容器运行

```bash
test -f .env || cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
docker compose -f deploy/docker-compose.yml ps
curl http://127.0.0.1:8080/api/v1/health
```

服务仅发布到 `127.0.0.1:8080`；可在项目根目录的 `.env` 中通过 `APP_PORT` 调整端口。SQLite 数据存放于 `stock_data` 命名卷，备份服务每天 02:00 后执行一次，循环保留 7 个星期槽位。

项目默认没有身份认证。远程服务器请通过 SSH 隧道访问；公网开放前必须另行增加认证、HTTPS、CSRF 防护和密钥管理。不要将个人数据库、备份或 `.env` 上传到仓库；这些运行数据不属于源码交付。

常用诊断命令：

```bash
docker compose -f deploy/docker-compose.yml logs --tail=100 backend scheduler
curl http://127.0.0.1:8080/api/v1/system/status
```

完整步骤见 [部署与运维](docs/deployment.md)。

## 项目文档

- [产品需求 PRD V1.1](A股交易辅助决策工具_PRD_V1.1.md)
- [整体设计](docs/plans/2026-08-25-stock-trading-assistant-design.md)
- [实施计划](docs/plans/2026-08-25-stock-trading-assistant-implementation.md)
- [最新交易日数据同步计划](docs/plans/2026-08-27-latest-trading-data.md)
- [部署与运维](docs/deployment.md)

## 免责声明

本工具仅用于个人研究和信息整理，不构成投资建议。历史数据和技术指标不代表未来表现。
