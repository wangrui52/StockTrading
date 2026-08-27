# 最新交易日真实行情修复与验收计划

> 使用 dev-executing-plans 的逐任务验证方式；用户已要求连续推进到真实数据验收，本次不在批次间等待确认、不提交 Git。

**目标：** 当前运行的网站能够通过真实数据源同步最新已收盘交易日，而非演示数据。

**架构：** 保留 FastAPI / React / SQLite 和 ARM64 容器。修复 JavaScript 解码依赖；最新日期由后端交易日历和上海收盘时间决定。同步只在数据校验通过后激活新批次，失败继续保留旧批次。演示与真实数据明确分隔。

**技术栈：** AkShare、mini-racer、SQLAlchemy、Pytest、TanStack Query、Vitest、Docker Compose。

## 1. 数据源与运行时

- 测试：`backend/tests/contract/test_market_data_contract.py`、`backend/tests/unit/test_runtime_dependencies.py`。
- 先验证旧 Linux JavaScript 运行时依赖与新鲜度失败；替换为支持 ARM64 的 mini-racer，锁定依赖。
- 实测东方财富与备用源；备用源必须保留真实日期、成交量/成交额单位和复权口径，不用 0 或旧价格冒充缺失数据。
- 通过同一适配器契约回归，随后在 ARM64 容器运行 JavaScript 和真实日线探测。

## 2. 同步生命周期与演示隔离

- 测试：`backend/tests/integration/test_sync_pipeline.py`、`backend/tests/unit/test_scheduler.py`、`backend/tests/contract/test_api_v1.py`。
- 实现：`backend/app/application/sync_pipeline.py`、`backend/app/application/scheduler.py`、`backend/app/api/v1/router.py`、数据模型及迁移。
- 先写失败测试：日历失败记录 FAILED；周末/盘中选择上一已收盘交易日；演示历史不得并入真实历史；空/过期数据不能激活；同一任务幂等。
- 增加来源元数据，保留旧批次；更新真实股票名称，修复历史断档时错误走增量的问题。
- 调度异常不退出进程，错误可见且能重试，不生成假停牌行情。

## 3. 页面与启动行为

- 测试：`frontend/src/app/App.test.tsx`、启动脚本契约。
- 实现：`frontend/src/features/dashboard/DashboardPage.tsx`、`start_local.command`、OpenAPI 生成物与 README。
- 已有批次时仍显示手动同步；交由后端选择最新交易日；轮询任务，完成后刷新行情、详情、筛选等缓存。
- 明确显示演示数据/同步失败，不自动初始化演示数据，演示仅显式启用。

## 4. 部署与真实验收

- 运行后端测试与覆盖率（至少 70%）、前端测试与构建、启动脚本契约、静态检查。
- 使用 SQLite 在线备份保存运行数据，不删除 volume、不覆盖用户自选和决策记录。
- 重建服务并发起一次真实全股票池同步，检查当前激活批次、完成率、指数及沪深京股票实际交易日与指标。
- 若来源本身尚未发布当天收盘日线，明确缺口，不降低完整率或将样本冒充全市场；继续检查受支持的公开备用源。

## 实施记录

- 东方财富当前真实请求断连，改用已实测可用的新浪股票池 + 腾讯日线；腾讯请求不传结束日期，避免当天日线被遗漏。
- 完成 ARM64 JS 解码、批次来源迁移、原子任务占用、逐股流式持久化、信号批次隔离和确认态继承。
- 前端始终提供同步入口、显示演示标记/错误/进度，并在激活批次变化后刷新查询缓存。
- 迁移前已创建 `/data/backups/pre-latest-data-20260827.db` 在线备份并通过完整性检查；原有业务数据未删除。
- 本机容器真实同步已完成：批次 #2，来源 `tencent-sina-v1`，交易日 2026-08-27，状态 READY，未强制激活。
- 新浪 `hs_a` 池 5,549 只，成功 5,544、缺失 5，完整率 99.9099%；沪 2,313、深 2,895、京 336。各市场当日 raw/qfq 数量一致，所有成功股票当日涨跌幅非空。
- 缺失代码：920125、920138、600491、000016、002274。未用旧价格或假停牌补足；该来源池不包含 689009 等 CDR，不能把完整率解释为全部交易所证券的覆盖率。
- 同步开始于上海时间 15:31:33，完成于 15:55:22，约 24 分钟；已验证部署重启后批次保持有效，再次 POST `{}` 仍返回 job_id=2/batch_id=2，不创建重复任务。
- 四个主要指数均为 2026-08-27；已通过 API 验收浦发银行、平安银行、诺思兰德、华兴源创当天 OHLCV，通过浏览器验收看板自动切换日期、国机汽车详情和图表显示。
- 最终迁移版本 `e73c851fd032`。689 单位修正迁移本次无匹配记录（不在来源池中），真实 688 样本成交量为股；此前没有腾讯来源的 689 报告，不需要重生成历史报告。
- 后端 94 passed / 1 skipped（旧东方财富 live 测试默认跳过），覆盖率约 92%；前端 15 passed，语句覆盖率 94.22%，类型检查与构建通过；Ruff、启动脚本契约、git diff --check 通过。
- 定向审查关闭了并发任务、批次信号污染、除权假涨跌幅，以及次日增量覆盖已验证历史涨跌幅的问题。自动调度的跨进程租约恢复仍是既有边界，运维升级需等待任务结束。

## 技术依据

- mini-racer 的 ARM64 wheel：https://pypi.org/project/mini-racer/
- uv 依赖覆盖：https://docs.astral.sh/uv/concepts/resolution/
- AkShare 接口字段以本机 1.18.94 源码和当轮真实响应共同核实。
