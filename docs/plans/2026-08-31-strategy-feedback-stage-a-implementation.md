# 策略反馈阶段 A 实现计划

> **致 Claude：** 必须使用子技能 dev-executing-plans 逐任务执行此计划。

**目标：** 在不影响现有日线同步和实时快照的前提下，完成候选股 T+1/T+3/T+5 后续表现评价、策略效果查询与前端展示。

**架构：** 新增纯领域计算模块 `outcomes.py`，由应用层 `CandidateOutcomeModule` 负责到期发现、幂等持久化和汇总查询；同步批次激活提交后再独立触发评价，评价失败不能回滚批次。前端只展示后端给出的收益、MFE、MAE、覆盖率和缺失原因，不重复计算金融指标。

**技术栈：** Python 3.13、FastAPI、SQLAlchemy 2、Alembic、SQLite、pytest、React 19、TypeScript、TanStack Query、Vitest、OpenAPI TypeScript。

**执行约束：** 当前工作区含未提交的实时行情改动；所有修改必须保留这些改动。按用户默认约定不执行 `git commit` 或 `git push`，计划中的每个“检查点”只运行测试和检查 diff。

---

### 任务 1：候选评价领域计算

**文件：**
- 创建：`backend/app/domain/outcomes.py`
- 创建：`backend/tests/unit/test_candidate_outcomes.py`

**步骤 1：编写失败的金样本测试**

覆盖 T+1、T+3、T+5 收益、MFE、MAE，明确参考价为 T+1 原始开盘价：

```python
def test_calculates_return_mfe_and_mae_from_next_open():
    bars = [
        OutcomeBar(date(2026, 8, 24), 10.0, 11.0, 9.0, 10.5, False, 100),
        OutcomeBar(date(2026, 8, 25), 10.5, 12.0, 10.0, 11.5, False, 100),
        OutcomeBar(date(2026, 8, 26), 11.5, 13.0, 9.5, 12.6, False, 100),
    ]
    result = calculate_outcome(bars, horizon=3)
    assert result.return_rate == pytest.approx(20.0)
    assert result.mfe == pytest.approx((13 / 10.5 - 1) * 100)
    assert result.mae == pytest.approx((9 / 10.5 - 1) * 100)
```

同时覆盖：周末/节假日不按自然日计数、T+1 停牌、成交量为 0、开盘价为 0、异常 OHLC、行情不足。

**步骤 2：运行测试确认失败**

运行：`cd backend && uv run pytest tests/unit/test_candidate_outcomes.py -v`

预期：FAIL，提示 `app.domain.outcomes` 不存在。

**步骤 3：实现最小纯计算模型**

实现冻结值对象 `OutcomeBar`、`CompletedOutcome`、`UnavailableOutcome` 和：

```python
def calculate_outcome(bars: Sequence[OutcomeBar], horizon: Literal[1, 3, 5]) -> OutcomeResult:
    """bars 只包含 T 之后按有效交易日升序排列的原始价格。"""
```

领域层不得查询数据库，不得读取实时快照；`UNAVAILABLE` 必须返回稳定原因码，不用 0 补值。

**步骤 4：运行测试确认通过**

运行：`cd backend && uv run pytest tests/unit/test_candidate_outcomes.py -v`

预期：全部 PASS。

**步骤 5：检查点**

运行：`git diff -- backend/app/domain/outcomes.py backend/tests/unit/test_candidate_outcomes.py`

确认公式、百分数单位和不可用原因与 PRD 一致。

### 任务 2：评价表与 Alembic 迁移

**文件：**
- 修改：`backend/app/infrastructure/models.py`
- 创建：`backend/migrations/versions/<revision>_candidate_outcomes.py`
- 修改：`backend/tests/integration/test_migrations.py`

**步骤 1：编写失败的迁移测试**

断言升级到 head 后存在 `candidate_outcome`、`outcome_run`，并验证：

- `candidate_result_id + horizon_trading_days + calculation_version` 唯一；
- `COMPLETED` 可保存评价批次、日期、价格、收益、MFE、MAE；
- `UNAVAILABLE` 可保存原因且数值为空；
- 常用过滤索引覆盖规则版本、horizon、源批次日期和状态。

**步骤 2：运行测试确认失败**

运行：`cd backend && uv run pytest tests/integration/test_migrations.py -v`

预期：FAIL，缺少新表。

**步骤 3：增加 ORM 与迁移**

新增 `CandidateOutcome`、`OutcomeRun`。迁移的 `down_revision` 必须基于执行时的实际 Alembic head；当前实时行情迁移链为 `f84d962ae143 -> a95e073bf254`，不得制造分叉 head。

`CandidateOutcome` 保存 `source_batch_id`、`source_trade_date`、`rule_version` 作为审计和查询字段；计算版本首期固定为 `outcome-v1`。

**步骤 4：运行迁移测试**

运行：`cd backend && uv run pytest tests/integration/test_migrations.py -v`

预期：全部 PASS。

### 任务 3：到期发现与幂等评价应用模块

**文件：**
- 创建：`backend/app/application/candidate_outcomes.py`
- 创建：`backend/tests/integration/test_candidate_outcomes.py`

**步骤 1：编写失败的接口级集成测试**

通过 `CandidateOutcomeModule` 公共接口覆盖：

- 新激活批次只评价已到期候选；
- 同一候选生成 horizon 1、3、5 三条记录，未到期保持 `PENDING`；
- 评价使用候选源批次之后的有效交易日，不使用自然日偏移；
- 查询原始 `DailyPrice.adjustment == "raw"`，不读取 `RealtimeSnapshot`；
- 重复执行不重复插入；
- 不同市场相同代码不串数据；
- 单股缺失/停牌写 `UNAVAILABLE`，不会令整个 run 失败；
- 未处理异常令 `OutcomeRun` 进入 `FAILED`，不留下假完成统计。

**步骤 2：运行测试确认失败**

运行：`cd backend && uv run pytest tests/integration/test_candidate_outcomes.py -v`

预期：FAIL，缺少应用模块。

**步骤 3：实现应用模块**

提供：

```python
class CandidateOutcomeModule:
    def evaluate_due_outcomes(self, evaluation_batch_id: int) -> OutcomeRunView: ...
    def query_outcomes(self, filters: OutcomeFilters) -> OutcomePage: ...
    def summarize_outcomes(self, filters: OutcomeFilters) -> OutcomeSummary: ...
    def get_candidate_outcomes(self, candidate_result_id: int) -> list[OutcomeView]: ...
```

到期判断以 `TradeCalendar.is_open` 和已存在的批次行情共同校验；批量写入使用短事务。汇总只统计 `COMPLETED`，中位数在应用层由已完成收益计算，正收益指标命名为 `positive_return_ratio`。

**步骤 4：运行测试确认通过**

运行：`cd backend && uv run pytest tests/integration/test_candidate_outcomes.py -v`

预期：全部 PASS。

### 任务 4：批次激活后的独立调度

**文件：**
- 修改：`backend/app/application/sync_pipeline.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/integration/test_sync_pipeline.py`

**步骤 1：编写失败的调度测试**

测试自动同步和手动强制激活两条路径：批次提交为 READY/active 后调用 outcome runner；runner 抛错时批次仍保持 active，错误只落入 `OutcomeRun`。

**步骤 2：运行测试确认失败**

运行：`cd backend && uv run pytest tests/integration/test_sync_pipeline.py -v -k outcome`

预期：FAIL，尚未触发评价。

**步骤 3：实现提交后触发 seam**

为 `SyncPipeline` 注入可替换的 `outcome_runner`。必须先提交批次激活事务，再调用评价模块；不要把评价逻辑放入 `_build_batch` 的激活事务。`create_app` 使用真实 `CandidateOutcomeModule`，测试传 fake runner。

手动 `POST /data-batches/{id}/activate` 也在提交后触发一次幂等评价，避免手动激活漏跑。

**步骤 4：运行同步回归测试**

运行：`cd backend && uv run pytest tests/integration/test_sync_pipeline.py tests/contract/test_api_v1.py -v`

预期：全部 PASS；现有同步失败、完整率门槛和批次切换语义不变。

### 任务 5：策略效果 REST API 与 OpenAPI 契约

**文件：**
- 创建：`backend/app/api/v1/strategy_router.py`
- 修改：`backend/app/api/v1/schemas.py`
- 修改：`backend/app/main.py`
- 创建：`backend/tests/contract/test_strategy_api.py`
- 修改：`backend/openapi.json`
- 修改：`frontend/src/shared/api/schema.d.ts`
- 修改：`frontend/src/shared/api/client.ts`

**步骤 1：编写失败的 API 契约测试**

覆盖：

- `GET /api/v1/strategy/outcomes` 的规则版本、horizon、日期、状态和分页过滤；
- `GET /api/v1/strategy/outcomes/summary` 返回样本数、覆盖率、均值、中位数、正收益样本占比、平均 MFE/MAE；
- `GET /api/v1/strategy/outcomes/{candidate_result_id}`；
- `POST /api/v1/strategy/outcome-runs` 幂等补跑；
- 非法 horizon/日期范围返回 422，不存在资源返回 404，尚未到期返回明确状态而非错误。

**步骤 2：运行测试确认失败**

运行：`cd backend && uv run pytest tests/contract/test_strategy_api.py -v`

预期：FAIL，路由不存在。

**步骤 3：实现路由和 schema**

响应必须携带 `calculation_version`、过滤范围、完成/不可用/未到期数量和数据日期。API 只调用 `CandidateOutcomeModule`，不在路由里复制 SQL 或计算公式。

**步骤 4：导出并校验 OpenAPI**

运行：`cd backend && uv run python -m scripts.export_openapi`

运行：`cd frontend && pnpm generate:api`

运行：`cd backend && uv run pytest tests/contract/test_strategy_api.py tests/contract/test_api_v1.py -v`

预期：API 与 OpenAPI snapshot 全部 PASS。

### 任务 6：策略效果页面

**文件：**
- 创建：`frontend/src/features/strategy-effect/StrategyEffectPage.tsx`
- 创建：`frontend/src/features/strategy-effect/StrategyEffectPage.test.tsx`
- 修改：`frontend/src/app/App.tsx`
- 修改：`frontend/src/app/app.css`
- 修改：`frontend/src/shared/api/client.ts`

**步骤 1：编写失败的页面测试**

覆盖 loading、empty、error、partial、样本不足和正常数据；验证：

- 主导航可进入“策略效果”；
- 默认 horizon 为 T+5、最近 60 个有效交易日、当前生产规则；
- 可切换 T+1/T+3/T+5 与规则版本；
- 样本量小于 30 固定展示“样本不足，不用于判断策略有效性”；
- 页面用“正收益样本占比”，不用“胜率”；
- 明细显示代码、名称、源候选日、参考/评价日、收益、MFE、MAE及缺失原因。

**步骤 2：运行测试确认失败**

运行：`cd frontend && pnpm test -- StrategyEffectPage.test.tsx`

预期：FAIL，页面组件不存在。

**步骤 3：实现页面最小闭环**

使用 TanStack Query 请求 summary 和 list；过滤条件进入 query key。百分数只格式化，不重新计算；收益正负颜色沿用 `.rise/.fall`，缺失状态以文字和原因展示。

**步骤 4：运行前端测试**

运行：`cd frontend && pnpm test -- StrategyEffectPage.test.tsx App.test.tsx`

预期：全部 PASS。

### 任务 7：候选列表与个股详情的后续表现

**文件：**
- 修改：`backend/app/api/v1/schemas.py`
- 修改：`backend/app/application/dashboard.py`
- 修改：`backend/app/api/v1/router.py`
- 修改：`frontend/src/features/dashboard/DashboardPage.tsx`
- 修改：`frontend/src/features/stock-detail/StockDetailPage.tsx`
- 修改：`backend/tests/contract/test_api_v1.py`
- 修改：`frontend/src/app/App.test.tsx`

**步骤 1：编写失败的展示契约测试**

候选列表新增精简 `outcome_status`；个股详情只展示当前激活批次对应候选的三个 horizon。非候选股票返回空列表，不报错。

**步骤 2：运行测试确认失败**

运行：`cd backend && uv run pytest tests/contract/test_api_v1.py -v -k outcome`

运行：`cd frontend && pnpm test -- App.test.tsx`

预期：新增断言 FAIL。

**步骤 3：实现展示扩展**

看板只展示“未到期/已评价/数据缺失”摘要，不把完整评价对象塞入每一行。详情页增加“后续表现”区，展示参考价口径和计算版本。

**步骤 4：运行契约与页面回归**

运行：`cd backend && uv run pytest tests/contract/test_api_v1.py -v`

运行：`cd frontend && pnpm test -- App.test.tsx`

预期：全部 PASS。

### 任务 8：历史 backfill 与全量验证

**文件：**
- 创建：`backend/scripts/backfill_candidate_outcomes.py`
- 创建：`backend/tests/integration/test_candidate_outcome_backfill.py`
- 修改：`README.md`

**步骤 1：编写失败的 backfill 测试**

验证 dry-run 不写库、正式执行按评价批次顺序补算、重复执行幂等、输出应评价/完成/不可用/待到期数量；测试只使用冻结数据库，不访问网络。

**步骤 2：实现显式 backfill 命令**

```bash
cd backend
uv run python -m scripts.backfill_candidate_outcomes --dry-run
uv run python -m scripts.backfill_candidate_outcomes
```

迁移本身不得执行长时间业务计算。README 记录命令、评价口径和“非投资建议”边界。

**步骤 3：运行后端质量门禁**

运行：`cd backend && uv run ruff check app tests scripts`

运行：`cd backend && uv run pytest -m 'not live' --cov=app --cov-report=term-missing`

预期：Lint 通过；非 live 测试全部 PASS，覆盖率不低于当前基线。

**步骤 4：运行前端质量门禁**

运行：`cd frontend && pnpm typecheck`

运行：`cd frontend && pnpm test:coverage`

运行：`cd frontend && pnpm build`

预期：类型检查、测试和生产构建全部通过。

**步骤 5：迁移与 dirty tree 检查**

运行：`cd backend && uv run alembic upgrade head`

运行：`git status --short`

运行：`git diff --check`

确认：迁移只有一个 head；现有实时行情文件仍保留；没有提交或推送。

**步骤 6：运行验收说明**

自动化测试只证明计算、接口和渲染逻辑。真实历史 backfill 会读取本机已有日线数据，但不得自动发起自选股实时刷新或外部行情请求；若本机数据不足，报告覆盖率和缺失原因，不伪造 99% 验收结果。
