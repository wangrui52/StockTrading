# StockTrading 策略反馈闭环与 AI 辅助分析技术设计

> 日期：2026-08-31
>
> 状态：待评审
>
> 对应需求：[策略反馈闭环与 AI 辅助分析 PRD](../product/2026-08-31-strategy-feedback-ai-analysis-prd.md)
>
> 延续设计：[A 股交易辅助决策工具技术设计](../plans/2026-08-25-stock-trading-assistant-design.md)

## 1. 设计目标

在现有 FastAPI、SQLAlchemy、SQLite、React 架构上增加四项能力：

1. 对候选股进行按有效交易日、可追溯和幂等的后续评价；
2. 以 walk-forward 方式运行策略实验，阻断未来数据泄漏；
3. 保存版本化市场环境证据；
4. 在确定性规则链路之后增加可选、可校验、可降级的 AI 解读。

设计必须保持以下 invariant：

- 盘中实时快照不参与日线候选评价、策略回放和生产信号；
- `data_batch`、`rule_version` 继续作为结果一致性的事实来源；
- AI 不得写入 `daily_price`、`daily_indicator`、`signal_event`、`candidate_result` 或激活生产规则；
- AI 关闭或失败时，原有功能行为不变；
- 不接自动交易，不生成保证性目标价。

## 2. 当前基线与改动范围

### 2.1 可复用模块

| 当前模块 | 复用方式 |
|---|---|
| `SyncPipeline` | 在新批次成功激活后调度候选评价，不把评价失败并入批次激活事务 |
| `CandidateEngine` | 作为生产候选的确定性实现；策略实验通过显式参数构造独立 evaluator |
| `IndicatorEngine` / `SignalEngine` | 回放与生产共用公式和行为，不在实验模块复制实现 |
| `RuleVersion` | 承载经批准的新生产规则版本；需要补充激活状态和来源治理 |
| `AnalysisReport` | 保留确定性规则报告；AI 报告使用独立表，不污染现有内容 |
| `OperationLog` | 记录实验审批、激活和 AI 交互的最小审计事件 |
| OpenAPI → TypeScript | 新接口继续由 OpenAPI 生成前端类型 |

### 2.2 新增前后端范围

```text
backend/app/
├── domain/
│   ├── outcomes.py              # 候选评价纯计算
│   ├── backtesting.py           # 回放统计与成交约束
│   └── ai_evidence.py           # 证据包和值对象
├── application/
│   ├── candidate_outcomes.py    # 到期发现、幂等评价与查询
│   ├── strategy_experiments.py  # 实验状态机与审批
│   ├── market_regimes.py        # 市场环境快照
│   └── ai_analysis.py           # 证据冻结、调用、校验和持久化
├── ports/
│   └── llm.py                   # LLMPort
├── adapters/
│   ├── openai_compatible_llm.py # 生产 Adapter
│   └── fake_llm.py              # 测试 Adapter
└── api/v1/
    ├── strategy_router.py
    └── ai_router.py

frontend/src/features/
├── strategy-effect/
├── strategy-experiments/
└── ai-analysis/
```

## 3. Module、interface 与 seam

本设计不引入只做转发的浅层包装。复杂行为集中到少量 deep module，callers 和 tests 通过同一 interface 使用。

### 3.1 `CandidateOutcomeModule`

**Interface**

```python
evaluate_due_outcomes(evaluation_batch_id: int) -> OutcomeRunResult
query_outcomes(filters: OutcomeFilters) -> OutcomePage
summarize_outcomes(filters: OutcomeFilters) -> OutcomeSummary
```

caller 只需知道评价批次、过滤条件、幂等行为和 typed errors。有效交易日定位、参考价选择、停牌判断、MFE/MAE 窗口、数据版本及批量写入全部封装在 implementation 内。

Dependencies 属于 local-substitutable：SQLAlchemy/SQLite 可用内存数据库替代，因此 repository seam 保持 internal，不额外暴露一组只服务于测试的公共 port。

### 3.2 `StrategyExperimentModule`

**Interface**

```python
create_experiment(command: CreateExperiment) -> StrategyExperimentView
run_experiment(experiment_id: int) -> ExperimentRunView
review_experiment(experiment_id: int, decision: ReviewDecision) -> StrategyExperimentView
activate_approved_rule(experiment_id: int) -> RuleVersionView
```

implementation 负责参数 schema、状态机、walk-forward 切片、费用和不可成交规则、指标聚合、结果原子替换、审批与操作日志。该 module 可以复用现有领域 engines，但不允许直接调用 LLM。

### 3.3 `AIAnalysisModule`

**Interface**

```python
generate_analysis(command: GenerateAIAnalysis) -> AIAnalysisView
get_analysis(analysis_id: int) -> AIAnalysisView
```

implementation 负责：构建证据包、冻结输入、调用 Provider、解析 schema、校验证据引用和禁用表述、预算记账、失败映射与持久化。

`LLMPort` 位于 true external seam。至少存在两个 Adapter：OpenAI 兼容生产 Adapter 和确定性 fake Adapter。

```python
class LLMPort(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse: ...
```

`LLMRequest` 只包含模型无关的 messages、结构化输出 schema、超时和 token 限额；业务层不知道厂商 URL、鉴权 header 或 SDK 类型。

### 3.4 `MarketRegimeModule`

**Interface**

```python
build_snapshot(batch_id: int) -> MarketRegimeSnapshotView
get_snapshot(batch_id: int) -> MarketRegimeSnapshotView
```

指数趋势、市场宽度和缺失原因藏在 implementation 内。板块来源尚未形成第二个合理 Adapter 前，不提前创建板块数据 port；完成来源 Spike 后再决定 seam。

## 4. 总体数据流

```text
Tencent/Sina daily source
  → SyncPipeline
  → READY data_batch
  → activate batch
       ├─ CandidateOutcomeModule.evaluate_due_outcomes(batch_id)
       └─ MarketRegimeModule.build_snapshot(batch_id)

candidate_result + candidate_outcome + market_regime_snapshot
  → StrategyExperimentModule
  → deterministic walk-forward metrics
  → DRAFT / VALIDATED / APPROVED / SHADOW
  → explicit activation
  → new RuleVersion

daily evidence + market regime + optional external evidence
  → immutable evidence snapshot
  → AIAnalysisModule
  → LLMPort
  → schema and citation validator
  → ai_analysis_report
```

评价和市场快照在批次激活后独立执行。它们失败时记录独立任务状态，不回滚已经通过 99% 完整率门槛的日线批次，也不能留下部分聚合结果冒充完成。

## 5. 数据模型

### 5.1 `candidate_outcome`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | integer PK | 主键 |
| `candidate_result_id` | FK | 原始候选 |
| `source_batch_id` | FK | 候选来源批次，冗余用于快速审计 |
| `evaluation_batch_id` | FK nullable | 完成评价所用批次 |
| `horizon_trading_days` | integer | 1、3、5 |
| `reference_trade_date` | date nullable | T 后第一个有效交易日 |
| `evaluation_trade_date` | date nullable | T 后第 N 个有效交易日 |
| `reference_price` | float nullable | 默认 T+1 不复权开盘价 |
| `evaluation_price` | float nullable | T+N 不复权收盘价 |
| `return_rate` | float nullable | 百分数值 |
| `mfe` / `mae` | float nullable | 百分数值 |
| `status` | string | `PENDING/COMPLETED/UNAVAILABLE` |
| `unavailable_reason` | string nullable | 停牌、缺失、退市等 |
| `calculation_version` | string | 评价公式版本 |
| `created_at` / `updated_at` | datetime | 审计时间 |

唯一约束：`candidate_result_id + horizon_trading_days + calculation_version`。

### 5.2 `outcome_run`

记录一次到期扫描：`evaluation_batch_id`、状态、应评价数、完成数、不可用数、开始/结束时间和错误摘要。用于避免把候选评价失败混入 `sync_job`。

### 5.3 `market_regime_snapshot`

| 字段 | 说明 |
|---|---|
| `batch_id` | 与数据批次一一对应、唯一 |
| `regime_version` | 环境分类公式版本 |
| `breadth` | 上涨、下跌、平盘数量及覆盖率 |
| `turnover` | 有效股票聚合成交额 |
| `index_states` | 上证、深证、创业板、北证 50 的趋势证据 |
| `sector_states` | nullable；来源 Spike 完成后启用 |
| `classification` | 规则化环境标签或 `INSUFFICIENT_DATA` |
| `missing_reasons` | 缺失字段及原因 |

### 5.4 `strategy_experiment`

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `base_rule_version` | 基础规则 |
| `proposed_parameters` | 通过 schema 校验后的参数 |
| `proposer_type` | `USER/DETERMINISTIC/LLM` |
| `provider/model/prompt_version` | LLM 提案时记录，否则为空 |
| `training_window` / `validation_window` | 明确时间边界 |
| `execution_assumptions` | 参考价、费用、滑点、不可成交规则 |
| `status` | 实验状态机 |
| `metrics` | 聚合指标；运行中为空 |
| `approved_at` / `rejected_at` | 审批时间 |
| `created_at` / `updated_at` | 审计时间 |

### 5.5 `strategy_experiment_result`

保存每个决策日、股票、候选原因、参考价、评价价、费用、可成交状态和收益。唯一约束包含 `experiment_id + decision_trade_date + market + stock_code + horizon`。

### 5.6 `ai_evidence_snapshot`

保存不可变 JSON 证据和 SHA-256 哈希：源 `batch_id`、`rule_version`、market、stock_code、行情、指标、信号、候选、市场环境、可选外部证据 ID。证据项均有稳定 `evidence_id`。

### 5.7 `ai_analysis_report`

| 字段 | 说明 |
|---|---|
| `evidence_snapshot_id` | 冻结输入 |
| `status` | `PENDING/SUCCEEDED/FAILED/REJECTED` |
| `provider` / `model` | 生成模型 |
| `prompt_version` | Prompt 契约版本 |
| `generation_parameters` | temperature、max_tokens 等非密钥参数 |
| `structured_content` | 通过 schema 与引用校验的结果 |
| `token_usage` / `latency_ms` | 成本与性能 |
| `error_code` / `error_summary` | 失败原因，不保存密钥或完整上游响应 |
| `report_version` | 同一证据包可生成多版 |
| `created_at` | 生成时间 |

## 6. 状态机与事务

### 6.1 候选评价

```text
PENDING → COMPLETED
        ↘ UNAVAILABLE
```

- `COMPLETED` 只在价格、日期和指标全部写入后提交。
- `UNAVAILABLE` 必须有 reason，可在更高 `calculation_version` 下重新评价，但不覆盖旧版本。

### 6.2 策略实验

```text
DRAFT → VALIDATING → VALIDATED → APPROVED → SHADOW
                  ↘ FAILED       ↘ REJECTED
```

- 只有 `VALIDATED` 可审批。
- `APPROVED` 不等于生产激活。
- 生产激活创建新的 `RuleVersion`，并在单个事务内写审批关联和操作日志。
- 同一实验只能创建一个规则版本；重复请求返回原结果。

### 6.3 AI 分析

```text
PENDING → SUCCEEDED
        ↘ FAILED
        ↘ REJECTED  # schema、引用或禁用表述不合格
```

外部调用不持有数据库长事务。流程为：先提交 `PENDING` 和证据快照 → 调用外部模型 → 新事务校验并更新最终状态。

## 7. 计算口径

### 7.1 有效交易日定位

以 `trade_calendar` 为主，并要求目标日期在已激活 `data_batch` 中有对应股票行情。不能用自然日 `+ timedelta(days=N)`。

### 7.2 收益与波动

```text
return_rate = (evaluation_close_raw / reference_open_raw - 1) * 100
mfe = (max(high_raw in [reference_day, evaluation_day]) / reference_open_raw - 1) * 100
mae = (min(low_raw in [reference_day, evaluation_day]) / reference_open_raw - 1) * 100
```

若 T+1 开盘为 0、停牌或无成交，状态为 `UNAVAILABLE`。不顺延成交，以免引入未声明的执行假设。

### 7.3 Walk-forward 防泄漏

- 决策日 D 只能读取 `trade_date <= D` 且当时已存在的批次内容。
- 候选用 D 日收盘后规则计算，最早参考价为下一有效交易日开盘。
- 市场环境使用 D 的快照，不能用验证期标签。
- 参数提案窗口和验证窗口严格不重叠。
- 前复权序列必须固定到回放数据版本；不能直接使用今天重新计算后包含未来公司行为影响的序列冒充历史当时视图。
- 基准使用同时间范围内指数收益，并记录来源和缺失。

### 7.4 参数 schema

参数定义集中在版本化 schema，不接收任意 JSON：

```json
{
  "candidate": {
    "min_history_days": 120,
    "rsi_min": 45,
    "rsi_max": 75,
    "volume_ratio_min": 1.2,
    "event_lookback_days": 3
  }
}
```

校验规则包括字段白名单、有限数字、上下界、跨字段关系和最大变更幅度。未知字段直接拒绝。

## 8. REST interface

统一前缀 `/api/v1`。

### 8.1 候选评价与看板

- `GET /strategy/outcomes?rule_version=&horizon=&date_from=&date_to=&page=`
- `GET /strategy/outcomes/summary?rule_version=&horizon=&date_from=&date_to=`
- `GET /strategy/outcomes/{candidate_result_id}`
- `POST /strategy/outcome-runs`：运维手动补评价；正常路径由批次激活后调度。
- `GET /strategy/outcome-runs/{id}`

响应统一包含过滤范围、样本量、完成率、数据日期和计算版本。

### 8.2 策略实验

- `POST /strategy/experiments`
- `GET /strategy/experiments`
- `GET /strategy/experiments/{id}`
- `POST /strategy/experiments/{id}/run`
- `POST /strategy/experiments/{id}/review`
- `POST /strategy/experiments/{id}/activate`

状态冲突使用 `409`，参数非法使用 `422`，资源不存在使用 `404`。`run/review/activate` 支持幂等键。

### 8.3 AI 分析

- `POST /ai/analyses`：输入 `batch_id + market + stock_code`，不允许前端提交自由行情数据。
- `GET /ai/analyses/{id}`
- `GET /ai/analyses?market=&stock_code=&batch_id=`
- `GET /ai/settings`
- `PATCH /ai/settings`：只保存非密钥设置。

AI 创建接口返回 `202` 和任务状态；前端轮询或使用现有任务查询模式，不在首期引入 WebSocket。

### 8.4 错误代码

| code | 场景 |
|---|---|
| `OUTCOME_NOT_DUE` | 评价尚未到期 |
| `OUTCOME_DATA_UNAVAILABLE` | 参考日或评价日不可用 |
| `EXPERIMENT_STATE_CONFLICT` | 当前状态不允许操作 |
| `EXPERIMENT_FUTURE_DATA_RISK` | 检测到未来数据泄漏 |
| `EXPERIMENT_PARAMETER_INVALID` | 参数 schema 校验失败 |
| `AI_DISABLED` | AI 未启用 |
| `AI_PROVIDER_UNAVAILABLE` | 超时、限流或外部错误 |
| `AI_OUTPUT_REJECTED` | schema、引用或禁用表述校验失败 |
| `AI_BUDGET_EXCEEDED` | 达到预算上限 |

## 9. AI 输出契约与安全

### 9.1 输出 schema

```json
{
  "summary": "string",
  "positive_evidence": [{"text": "string", "evidence_ids": ["E1"]}],
  "risks_and_conflicts": [{"text": "string", "evidence_ids": ["E2"]}],
  "observation_conditions": [{"text": "string", "evidence_ids": ["E3"]}],
  "data_gaps": ["string"],
  "disclaimer": "string"
}
```

所有 `evidence_ids` 必须存在于冻结证据包。事实段落没有引用、引用不存在、包含禁用词或数字无法在证据中解析时，将报告标记为 `REJECTED`。

### 9.2 Provider 配置

- 使用 `pydantic-settings` 从环境变量读取 endpoint、API key 和默认模型。
- 数据库只保存 enabled、provider、model、timeout、retry、token limit、daily budget 等非密钥配置。
- 前端只接收 `credential_configured: boolean`。
- Adapter 负责 HTTP/SDK 异常转为 typed errors，业务层不识别厂商异常类型。
- 重试只针对超时、连接失败和明确可重试的 429/5xx；格式错误不盲目重试。

### 9.3 数据最小化

只发送目标股票当前证据包所需字段，不发送数据库路径、其他自选股、用户笔记、操作日志、密钥或整库数据。外部信息研究启用前，AI 不具备自主联网工具。

## 10. 后台任务与调度

- 候选评价：每个日线批次激活后触发一次，也提供幂等手动补跑。
- 市场环境：在批次激活后生成，可与候选评价独立运行。
- 策略回放：后台任务，单实例 SQLite 环境同一时间只运行一个重任务，避免长写事务争用。
- AI 生成：单次个股任务；首期限制并发 1，超时和日预算从设置读取。
- 应用启动时把遗留 `PENDING/RUNNING` 任务标记为中断，允许用户重试，不自动重复外部收费请求。

首期可沿用 FastAPI 后台任务和单实例约束；任务持续时间或并发增长后，再评估持久化 worker/queue，避免现在引入浅层基础设施。

## 11. 前端设计

### 11.1 路由

- `/strategy-effect`：策略效果看板和候选评价明细。
- `/strategy-experiments`：实验列表、新建、回放结果和审批。
- 现有 `/stocks/:market/:code`：增加“后续表现”和“AI 解读”区。
- 现有 `/reports`：区分“规则报告”和“AI 解读”。
- 现有 `/settings`：增加 AI 启停和非密钥配置状态。

### 11.2 状态

每个新增页面覆盖 loading、empty、error、stale、partial、insufficient-sample。AI 区额外覆盖 disabled、credential-missing、budget-exceeded、rejected。

前端不计算收益、MFE、MAE、回放指标或证据引用；只展示后端事实，避免与后端口径分叉。

## 12. 迁移与兼容

建议按以下顺序新增 Alembic 迁移：

1. `candidate_outcome`、`outcome_run`；
2. `market_regime_snapshot`；
3. `strategy_experiment`、`strategy_experiment_result`；
4. `ai_evidence_snapshot`、`ai_analysis_report`；
5. 扩展 `rule_version`：增加 `status`、`source_experiment_id`、`activated_at`，并为现有版本回填 `ACTIVE`。

迁移只新增表和 nullable 字段，不改写现有行情、信号、候选或报告。历史候选评价通过显式 backfill 任务生成，迁移过程不执行长时间业务计算。

SQLite 约束：

- 对 `candidate_outcome(candidate_result_id, horizon_trading_days, calculation_version)` 建唯一索引；
- 对评价过滤常用字段建组合索引；
- JSON 只保存展示快照和稀疏指标，核心过滤字段使用独立列；
- 批量评价分小事务提交，避免长时间占用写锁。

## 13. 测试策略

### 13.1 领域与 module interface 测试

- 有效交易日跨周末、节假日和连续停牌。
- T+1/T+3/T+5 参考日、评价日、收益、MFE、MAE 金样本。
- 缺失、退市、无成交、零价格和异常 OHLC。
- 实验状态机所有合法与非法迁移。
- 参数白名单、范围、跨字段关系和最大变化。
- walk-forward 时间切片和未来数据泄漏阻断。
- 成交费用、滑点和不可成交规则。
- AI schema、引用、禁用表述、数字证据和失败降级。

测试通过 module interface 断言可观察结果，不依赖 implementation 内部 helper。

### 13.2 集成测试

- 新批次激活后评价任务幂等生成。
- 评价失败不回滚已激活批次，也不产生半完成聚合。
- 实验结果原子替换、重试与审批审计。
- `APPROVED` 与生产激活分离，重复激活幂等。
- AI 外部调用不持有数据库事务。
- 设置变更不修改历史 AI 报告。
- Alembic 从当前 head 升级、空库升级和已有数据升级。

### 13.3 Adapter 契约测试

- `FakeLLMAdapter` 固定返回成功、超时、429、非法 JSON、缺引用和禁用表述。
- `OpenAICompatibleLLMAdapter` 使用冻结 HTTP 响应验证字段映射，不在默认测试访问真实模型。
- 真实模型验证标记为 `live`，必须显式启用且设置硬预算。

### 13.4 前端与 E2E

- 策略效果过滤、样本不足、评价缺失和版本对比。
- 实验创建、运行、审批二次确认和状态冲突。
- AI disabled、missing credential、loading、success、rejected、failure。
- E2E：同步冻结数据 → 生成候选 → 激活未来批次 → 完成评价 → 查看效果 → 创建回放 → 批准影子策略。
- AI E2E 使用 fake Adapter，不依赖网络和付费模型。

## 14. 可观测性

记录以下结构化字段：任务类型、任务 ID、batch_id、rule_version、experiment_id、analysis_id、状态、耗时、错误代码、样本数和 Token 用量。不记录 API key、完整 Prompt、隐藏推理、完整报告正文或用户笔记。

关键运行指标：

- 到期评价积压数和覆盖率；
- 回放任务耗时、失败率和样本数；
- AI 成功、失败、拒绝、限流、预算耗尽次数；
- Provider P50/P95 延迟和 Token 日用量；
- 数据库写锁冲突次数。

## 15. 实施顺序

### 阶段 A：确定性评价闭环

1. 迁移 `candidate_outcome/outcome_run`；
2. 实现 `CandidateOutcomeModule` 和金样本；
3. 接入批次激活后调度；
4. 增加策略效果接口和页面；
5. 运行历史 backfill 并验证覆盖率。

### 阶段 B：策略实验

1. 冻结参数 schema 和执行假设；
2. 实现 walk-forward 引擎及泄漏测试；
3. 实现实验状态机、审批和影子运行；
4. 扩展 `RuleVersion` 激活治理；
5. 增加实验页面。

### 阶段 C：AI 基础

1. 建立市场环境和证据快照；
2. 定义 `LLMPort`、fake 和生产 Adapter；
3. 实现结构化输出与引用校验；
4. 增加 AI 报告、设置和审计；
5. 使用 fake Adapter 完成全链路验收后再进行受预算限制的 live 验证。

### 阶段 D：外部研究

完成来源、许可、抓取稳定性和引用格式 Spike 后，再增加外部信息 Adapter；未完成前不在 Prompt 中声称具备实时新闻能力。

## 16. 明确不做

- 不让 LLM 直接修改或激活生产参数；
- 不把简单未来价格回填包装成完整回测；
- 不使用盘中实时报价评估日线策略；
- 不由前端计算或修正策略指标；
- 不保存明文密钥；
- 不接自动下单、券商账户或组合资金执行；
- 不把 Prompt 当作唯一风控；
- 不在首期引入 CrewAI、LangGraph、向量数据库或独立任务队列。

## 17. 开发前 Spike

1. 明确 A 股涨跌停、停牌和不可成交的回放口径，并准备覆盖主板、创业板、科创板、北交所、ST 和新股阶段的冻结样本。
2. 验证腾讯/新浪历史数据是否能稳定提供 T+1 开盘和区间高低价，并记录来源修订行为。
3. 确定回放前复权数据的历史版本冻结方案，避免未来公司行为污染过去视图。
4. 用当前 SQLite 数据规模测量历史候选 backfill 和 3 年 walk-forward 耗时。
5. 选定首个 OpenAI 兼容模型，确认结构化输出能力、数据出境范围、计费和限流；未确认前保持 AI 默认关闭。
