# StockTrading · A 股交易辅助决策工具

按照 `A股交易辅助决策工具_PRD_V1.1.md` 实现的个人日线研究工作台，支持自选股按需刷新实时报价。系统完成“同步数据 → 查看市场 → 组合筛选 → 查看个股 → 加入自选 → 处理提醒 → 生成报告”闭环，不接实盘、不自动下单。

## 主要功能

| 模块 | 能力 |
| --- | --- |
| 行情看板 | 查看市场指数、候选股票、数据日期与来源，手动同步最新已收盘交易日 |
| 股票筛选 | 组合价格、涨跌幅、成交额、RSI、均线和 MACD 条件，分页查看命中原因 |
| 个股详情 | K 线、成交量、MA、MACD、RSI、近期信号与关注笔记 |
| 策略效果 | 跟踪候选股 T+1/T+3/T+5 后续表现，按规则版本、日期和评价状态查看汇总与明细 |
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

### 刷新自选股实时行情

自选股页面提供「刷新自选股行情」：只查询点击时已经加入自选的股票，不读取或刷新全市场股票池。采集中新增的自选股需要下一次点击才会纳入；列表优先显示自选实时报价及来源时间，尚无报价时明确显示「日线参考」。

- 一次点击获取一次自选快照，不会持续自动采集；刷新期间重复请求复用任务，结束后有 30 秒冷却。
- 报价可能延迟；休市、停牌或旧报价不能视为当前成交，缺失报价不会被补齐或推算。
- 空自选列表不会创建刷新任务；获取失败时保留上一次成功的自选快照。
- 实时快照持久化保存最新一次成功结果，服务重启会把中断任务标记为失败，允许重试。
- 实时数据不写入 `data_batch`、`daily_price` 或指标表，不改变日线同步、筛选、提醒、报告和策略评价。

### 阶段 A：候选结果追踪与策略效果

主导航的「策略效果」入口用于查看候选股 T+1/T+3/T+5 后续表现。评价采用以下统一口径：

- `T` 是候选产生的交易日；`T+N` 按有效交易日计算，不按自然日偏移。
- 参考价为 `T+1` 有效交易日的不复权开盘价，评价价为 `T+N` 有效交易日的不复权收盘价。
- MFE 是从参考日到评价日区间内，不复权最高价相对参考价的最大正向变化；MAE 是同一区间内不复权最低价相对参考价的最大负向变化。
- `expected_evaluation_trade_date` 是完整权威交易日历能够确定时的预计 T+N 日期；`PENDING` 可展示该日期，但实际 `evaluation_trade_date` 仍为空，不能据此视为已完成评价。
- `max_drawdown_approx` 取当前过滤范围内 `COMPLETED` 样本 MAE 的最差值（最小的负百分数），只表示候选持有窗口最差 MAE 的样本级近似，不是组合资金曲线最大回撤。
- `PENDING` 表示尚未到评价时点；`COMPLETED` 表示价格、日期、收益、MFE 和 MAE 已完整计算；`UNAVAILABLE` 表示参考日或评价日停牌、无成交、行情缺失等导致无法评价，具体原因单独记录；`PARTIAL` 是候选的聚合状态，表示其 T+1/T+3/T+5 评价中同时存在不同状态。

日线新批次成功激活后会自动触发到期候选评价。API 服务、独立 scheduler 和历史 backfill 使用同一评价模块；评价在批次激活提交后独立执行，因此评价失败不会回滚已经成功激活的日线批次。

同步会使用行情适配器提供的权威交易日历，按批次实际行情历史的最早日期到目标日期，逐自然日持久化 `TradeCalendar.is_open`。候选评价先用这段完整日历确定 T+1/T+3/T+5，再检查同一评价批次的不复权行情；若权威开市日尚未来到、日历区间不完整，或该批次所有股票共同缺少某个所需开市日，相关 horizon 保持 `PENDING`，不会拿后续日期前移替代。旧数据库无需新增迁移，因为现有 `TradeCalendar(market, trade_date, is_open)` 已能承载该区间；但只有 target 日期的旧日历在下一次成功同步补齐覆盖范围前，历史评价会安全地保持 `PENDING`。

候选评价在单个进程内通过线程锁串行，在多个进程之间通过 SQLite 数据库文件旁的 `*.candidate-outcomes.lock` 文件串行，锁覆盖任务认领、计算和终态写入的完整过程。backend、scheduler 与 backfill 必须指向同一个 SQLite 数据库文件，并共享该数据库所在目录；不要在运行期间删除或替换锁文件。内存 SQLite 仅用于测试，只提供进程内串行；当前评价执行不支持非 SQLite 数据库，遇到其他数据库后端会明确报错，而不会假装已获得跨进程锁。

真实运行前必须先升级数据库结构，并建议先备份现有数据库：

```bash
cd backend
uv run alembic upgrade head
```

历史评价只通过显式 backfill 生成。先使用 dry-run 查看计划，不写入数据库；输出会分别列出当前已发布快照计数、各评价批次/来源/规则 cohort 的预计 `expected/completed/unavailable/pending` 及本次计划汇总。`final_logical_outcome_rows` 是全部有效候选最终各保留 T+1/T+3/T+5 后的逻辑唯一行数；`projected_totals.expected_count` 是本次依次处理每个评价批次和规则 cohort 的累计计算工作量，同一候选会随多个评价批次重复计算，因此可能更大。确认后再正式执行。Docker Compose 与源码运行使用的是两套不同位置的数据库，请选择与当前部署方式一致的命令。

如果服务通过 Docker Compose 运行，请在仓库根目录进入 `backend` 容器执行。命令沿用容器的 `DATABASE_URL=sqlite+pysqlite:////data/stock_trading.db`，直接处理 `stock_data` 命名卷中的生产数据库，不会误建宿主机数据库：

```bash
docker compose -f deploy/docker-compose.yml exec backend \
  uv run --no-sync python -m scripts.backfill_candidate_outcomes --dry-run
docker compose -f deploy/docker-compose.yml exec backend \
  uv run --no-sync python -m scripts.backfill_candidate_outcomes
```

如果后端以源码方式运行，请进入 `backend` 目录执行。未设置 `DATABASE_URL` 时，目标是宿主机的 `backend/stock_trading.db`，不是 Docker 的 `stock_data` 命名卷：

```bash
cd backend
uv run python -m scripts.backfill_candidate_outcomes --dry-run
uv run python -m scripts.backfill_candidate_outcomes
```

源码运行需要指定其他数据库或保留新的计算口径版本时，可追加可选参数：

```bash
uv run python -m scripts.backfill_candidate_outcomes --dry-run \
  --database-url 'sqlite+pysqlite:///./stock_trading.db' \
  --calculation-version 'outcome-v1'
```

backfill 只读取数据库中已有的候选、批次和不复权日线，不会自动下载行情，也不会触发实时行情刷新。正式执行前请备份数据库；若历史数据不足，记录会保持待评价或标明不可用原因，不会用推算值补齐。正式 backfill 与在线评价使用同一数据库时会自动竞争同一文件锁：已有评价未结束时 backfill 会等待，每个评价批次完成并释放锁后再继续，不需要也不应手工绕过锁。

评价批次只处理与自身 `DataBatch.source` 相同的候选批次，演示数据和真实数据不会互相评价。每次重算会创建新的 run attempt 和独立候选评价快照；只有最新 `COMPLETED` attempt 会发布，失败 attempt、旧版未绑定行和跨来源污染行均不会进入 API 或 dry-run 的当前发布统计。历史物理行保留用于审计，不做原地解绑或改写；再次执行正式 backfill 会生成同来源的干净快照并在成功后原子切换。因此无需修改默认 `outcome-v1`。建议先 dry-run、备份数据库，再使用与部署方式对应的正式命令完成修复。

策略效果中的“正收益样本占比”只描述已完成评价样本中收益大于零的比例，不称为“胜率”，也不代表未来收益或构成投资建议。

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

- 指标分析只处理已收盘交易日的日线；实时报价仅在自选股页面手动按需刷新，不提供全市场或持续推送盘口。日线自动同步默认于上海时间交易日 18:30 运行。
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
