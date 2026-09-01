# AI-Stock-Agent 对 StockTrading 的可借鉴点

> 调研日期：2026-08-31  
> 结论先行：最值得借鉴的是“候选结果 → T+1/T+3/T+5 表现回填 → 形成下一轮策略参考”的反馈闭环，以及“市场环境 → 规则筛选 → AI 解读 → 日报”的产品分层。其现有源码不适合直接移植：所谓“Transformer 自注意力、RAG、实时新闻、自进化”主要停留在提示词和 README 表述，缺少严格回测、参数约束、版本治理和工程化保障。

## 1. 调研对象判定

未提供项目 URL 时，名称无法唯一定位。本报告以 [WolkenZhen/cn-stock-AI-agent](https://github.com/WolkenZhen/cn-stock-AI-agent) 为主要对标对象，同时补充仓库名完全匹配的 [liai030303/AI-Stock-Agent](https://github.com/liai030303/AI-Stock-Agent)。理由是：

1. WolkenZhen 仓库 README 的正式标题就是 **AI-Stock-Agent**，目标市场同为中国 A 股，且包含选股、结果追踪和策略报告，与当前 StockTrading 的产品闭环最接近；
2. liai030303 仓库的 slug 和 README 名称都精确匹配 **AI-Stock-Agent**，内容是 CrewAI 三角色 A 股投研，适合补充 Agent 架构方面的对标；
3. WolkenZhen 仓库页面在调研时显示 32 次提交，最新提交为 `d9851cd8b72764c27adcb0e8cb2eb36722be5ab0`（2026-01-20），无 Release、0 star、0 fork；liai030303 仓库只有 5 次提交，均发生在 2026-03-08，也无 Tag 或 Release。两者都更适合作为思路样本，而不是成熟依赖。

GitHub 上还有 `a-stock-agent`、`StockAgent`、`stock-agent-system` 等近似名称，但项目名、市场和能力边界不同。本文不会把两个候选仓库的能力混称为同一实现；若用户指的是另一个具体链接，应另行对标。

## 2. 源码核验后的真实能力

### 2.1 架构与工作流

该项目是单进程 Python CLI，并不是多 Agent 编排系统。核心链路为：

```text
AkShare 市场/板块数据
  → DeepSeek 生成热点关键词和市场建议
  → 规则过滤深市主板活跃股
  → 每只候选计算 4 个规则因子 + 1 个 LLM 专家因子
  → 加权排序输出 TOP 10
  → CSV 记录候选
  → 后续交易日回填 T+1/T+3/T+5 收盘价
  → 将历史摘要再次交给 DeepSeek 生成下一轮权重
```

证据：

- 主流程与候选排序：[auto_strategy_optimizer.py](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/auto_strategy_optimizer.py)
- LLM 请求、热点判断、专家打分和权重生成：[llm_client.py](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/llm_client.py)
- 技术因子和 ATR 价位：[trading_signal.py](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/trading_signal.py)
- 单股诊断：[main.py](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/main.py)
- LLM 日报：[explainer.py](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/explainer.py)

### 2.2 数据源

源码实际使用 AkShare：

- A 股实时列表：`stock_zh_a_spot_em()`；
- 个股前复权日线：`stock_zh_a_hist(..., adjust="qfq")`；
- 上证指数日线：`stock_zh_index_daily()`；
- 行业/概念板块：`stock_board_industry_spot_em()`、`stock_board_concept_name_em()`。

`requirements.txt` 虽声明 yfinance 是备用源，但主流程没有调用它；数据也没有保存来源版本、采集时间、完整率或批次。见 [requirements.txt](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/requirements.txt)。

### 2.3 “Agent / 自进化 / 记忆 / 回测”的实际边界

- **Agent**：没有 LangGraph、CrewAI 等状态图或多个独立 Agent；实际是若干固定 prompt 的顺序调用。
- **自进化**：把最近至多 30 条候选表现拼成文本，请 DeepSeek 返回一组 JSON 权重；没有优化目标函数、训练过程、显著性检验或参数审批。
- **记忆**：`selection_history.csv` 保存候选与未来价格，属于结构化运行日志，不是向量记忆或 RAG。
- **回测**：只是给历史推荐补 T+1/T+3/T+5 收盘价并标注“波段涨/亏损”等，没有逐日无未来数据回放、组合资金曲线、费用、滑点、停牌/涨跌停成交约束、基准和最大回撤。因此更准确的名称是“推荐结果追踪”，不是完整回测。
- **报告**：能把当日候选交给 LLM 生成 Markdown 日报，但没有事实引用、输入快照哈希、模型版本和 prompt 版本。
- **风险控制**：有 ST、涨停、成交额过滤，并以 ATR 计算参考买入、止盈、止损；但没有组合级仓位、相关性、最大损失、熔断或参数校验。
- **部署**：README 只描述本地 venv + CLI，没有 API、前端、容器、调度、鉴权和可观测性。

### 2.4 精确同名仓库的三角色 Agent 设计

`liai030303/AI-Stock-Agent` 是另一种路线：

```text
量化与基本面矿工
  ├─ AkShare 日线 → MA20/60、MACD、RSI14、BOLL
  └─ PE-TTM、营收变化、现金流

宏观政策研究员
  └─ DuckDuckGo 搜索政策、行业与公司动态

前两份报告作为显式 context
  → CIO 汇总为估值、宏观共振、操作指令三段式报告
```

三个角色由 CrewAI `Process.sequential` 顺序执行，均禁止自行委派，并设置迭代次数；Crew 还设置 `max_rpm=15`。CIO 不直接调用工具，只消费前两项任务的结果。见 [Agent 定义](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/agents.py)、[任务与显式上下文](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/tasks.py) 和 [顺序工作流](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/main.py)。

这个仓库真正值得借鉴的是：

- **客观数据、外部信息、汇总判断分责**，避免一个 prompt 同时抓数据、解释和下结论；
- **前置结果作为显式 context**，最终报告的输入边界更清楚；
- **输出契约明确**，数据获取失败时要求写明失败、禁止编造；
- **统一 provider 工厂**，同一个 OpenAI 兼容适配层切换 DeepSeek、Qwen、智谱，模型入口不散落在业务代码中。

它没有记忆、RAG、回测或真实交易执行；风险条件只写在 prompt 中，不是代码级风控。Streamlit 页面和 CLI 也只是单次请求、单次报告。见 [工具实现](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/tools.py) 和 [Streamlit 页面](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/app.py)。

## 3. 当前 StockTrading 已经更强的基础

当前项目已经具备以下不应被弱化的能力：

- 数据按 `batch_id`、交易日、来源和规则版本隔离，新批次完整率达到 99% 才激活；失败保留上一成功批次；
- 实时快照与日线指标隔离，不拿盘中报价改写收盘日线信号；
- 指标、信号、候选、提醒、报告均为确定性规则，并能追溯到具体批次和版本；
- FastAPI + SQLAlchemy + Alembic + React 的完整产品形态，已有自动化测试和 Docker 部署；
- 明确定位为辅助研究，不接实盘、不自动下单。

因此，不建议用 AI-Stock-Agent 的 CSV/脚本结构替换现有架构。更合理的方式是在现有可追溯数据层上增加“实验性反馈与 AI 解读层”。

## 4. 值得借鉴的能力与落地方式

| 优先级 | 借鉴点 | AI-Stock-Agent 的启发 | StockTrading 建议实现 |
| --- | --- | --- | --- |
| P0 | 候选结果追踪 | 推荐后回填 T+1/T+3/T+5 表现 | 新增 `candidate_outcome`，按原候选的 `batch_id`、规则版本和交易日记录未来第 1/3/5 个有效交易日收益；不能覆盖原候选记录 |
| P0 | 策略实验闭环 | 历史表现影响下一轮因子权重 | 新增“影子策略版本”：离线生成建议权重，先回放验证，人工批准后才成为新规则版本；线上 LLM 不直接改生产参数 |
| P0 | 两阶段分析 | 规则因子缩小范围，LLM 负责语义分析 | 先用现有确定性规则筛出 Top N，再对小样本生成解释/冲突检查；避免全市场逐股调用 LLM、降低成本与不稳定性 |
| P0 | 分层 Agent 契约 | 数据矿工、外部研究、最终汇总各自只承担一种责任 | 不必先引入 CrewAI；先把“证据包、外部事实包、报告结论”定义成三个带 schema 的应用服务，最终层只能引用前两层证据 |
| P1 | 市场环境上下文 | 指数、量能、领涨行业/概念共同形成环境判断 | 增加版本化 `market_regime_snapshot`，保存宽度、指数趋势、成交额和板块强弱的原始证据；先规则化分层，再让 LLM 做自然语言解释 |
| P1 | 可执行的条件清单 | ATR 止盈/止损让输出更易行动 | 在报告中增加“观察触发价、失效条件、风险预算”参考，但必须显示计算公式、使用日线、批次、复权口径，并保持“不自动下单” |
| P1 | 日报产品形态 | 自动汇总候选和市场背景 | 在现有规则报告上增加可选 LLM 摘要；LLM 只能引用结构化证据，输出需保留模型、prompt、输入快照、生成时间和降级状态 |
| P1 | LLM Provider 适配层 | 用一张配置表支持多个 OpenAI 兼容厂商 | 定义单一 `LLMPort`，密钥只读环境/密钥服务；配置模型能力、超时、重试、费用预算和允许的数据出境范围，业务层不识别厂商品牌 |
| P2 | 单股自然语言诊断 | CLI 能快速解释个股 | 前端增加“解释当前规则结论”，输入只取当前激活批次和已落库信号；不让模型自行抓行情或凭空给目标价 |

### 4.1 推荐的数据模型

```text
candidate_outcome
  id
  candidate_result_id
  source_batch_id
  evaluation_trade_date
  horizon_trading_days        # 1 / 3 / 5 / 10
  reference_price
  evaluation_price
  return_rate
  max_favorable_excursion
  max_adverse_excursion
  data_batch_id               # 用哪个未来批次完成评价

strategy_experiment
  id
  base_rule_version
  proposed_parameters_json
  proposer                    # deterministic / llm:model
  prompt_version
  training_window
  backtest_metrics_json
  status                      # DRAFT / VALIDATED / APPROVED / REJECTED
  approved_at
```

这能保留 AI-Stock-Agent 的“反馈闭环”优点，同时延续当前项目已有的批次隔离、版本可追溯和失败不污染生产数据。

### 4.2 推荐的安全工作流

```text
生产规则 v1 生成候选
  → 到期后确定性计算候选结果
  → 离线实验产生权重提案
  → 时间序列 walk-forward 回测
  → 检查收益、回撤、换手、稳定性和样本量
  → 人工批准为规则 v2
  → 小流量/影子运行
  → 再决定是否激活
```

LLM 的合适角色是“提出假设、归纳证据、解释冲突”，不应是未受约束的生产参数写入器。

## 5. 不应直接照搬的实现

### 5.1 README 名称与源码能力不一致

- `llm_client.py` 的“Transformer 自注意力进化”只是 prompt 文本，没有 Transformer 训练或注意力计算；
- 初始化日志写“技术指标+RAG”，仓库没有检索索引、embedding 或知识库；
- README 称“实时新闻热点”，源码取的是东方财富行业/概念板块涨跌榜，并未采集新闻；
- README 称新权重保存到 `factor_weights.json`，当前主流程只把权重保存在内存，下一次仍从 `DEFAULT_WEIGHTS` 开始；
- README 示例为 TOP 5，当前主流程输出 TOP 10。

这些差异说明能力命名必须以可验证实现为准，当前项目应继续保持数据来源、时间和降级状态显式展示。

### 5.2 LLM 输出缺少硬约束

权重函数用正则提取任意 JSON 后直接返回，没有验证：

- 因子名是否属于白名单；
- 值是否为有限数字、是否非负、是否在允许区间；
- 权重是否合计 100；
- 新旧权重变化是否超过上限；
- 样本量是否足够；
- 同一输入能否复现。

当前项目若引入 LLM，至少要使用结构化 schema、范围校验、总和归一、失败回退、模型/prompt 版本记录和人工审批。

### 5.3 不是可信回测

现有代码没有解决：

- 同日收盘数据形成决策后能否按该价格成交；
- 前复权历史在未来公司行动后重算造成的口径变化；
- 停牌、涨跌停、滑点、佣金和印花税；
- 股票池幸存者偏差；
- LLM 非确定性与当下市场上下文对历史参数的污染；
- 多策略试验带来的过拟合。

因此不能把它的 T+1/T+3/T+5 回填结果用作策略有效性证明。

### 5.4 工程风险

- 多处裸 `except` 会把数据/API/解析错误静默降级；
- API Key 配置在 Python 文件中，而不是严格使用环境变量或密钥服务；
- CSV 无事务、无并发控制、无 schema 迁移和版本约束；
- 每只候选单独调用 LLM，成本、时延和限流风险随候选数增长；
- 无自动化测试、API 契约、运行指标和失败审计；
- `main.py` 把 `cost_price` 位置参数传给期望权重字典的 `calculate_logic()`，输入成本价时会触发类型错误，说明当前代码缺少基础回归验证。

精确同名的 liai030303 仓库也存在不能照搬的问题：

- DuckDuckGo 工具只返回标题和正文前 200 字，不返回来源 URL，无法把宏观结论审计到具体网页；
- 风控阈值和“一票否决”只存在于 prompt，模型没有被代码级规则兜底；
- `requirements.txt` 引用了 `langchain-google-genai`，源码却导入 `langchain_openai`，同时使用 Streamlit 但未声明 `streamlit`，依赖清单与运行代码不一致；
- 仓库最新提交实际跟踪了 `.env`，即使当前值是否有效未知，也违反密钥文件不入库的基本边界；
- 设计文档仍围绕 Gemini，运行代码已改为智谱/Qwen/DeepSeek，文档与实现发生漂移。

当前项目若增加外部检索，必须保存 `source_url`、发布时间、抓取时间、证据片段和结论引用映射；若引入多模型，也必须先解决数据合规、密钥轮换和完整依赖锁定。

## 6. 建议实施顺序

### 第一阶段：先做不依赖 LLM 的反馈闭环

1. 持久化候选 T+1/T+3/T+5 的收益、最大有利/不利波动；
2. 增加候选命中率、平均收益、中位数、回撤、按市场状态分组统计；
3. 做严格按有效交易日的 walk-forward 回放，并加入费用和不可成交规则；
4. 页面展示“当时为什么入选”和“后来表现如何”。

这是价值最高、可验证性最强的一步。

### 第二阶段：增加市场环境和策略实验

1. 将指数趋势、上涨家数占比、成交额、板块强度做成版本化快照；
2. 建立策略参数实验表和审批状态；
3. 允许规则引擎离线生成候选权重方案，先影子运行；
4. 明确训练窗口、验证窗口和最低样本量。

### 第三阶段：可选 LLM 增强

1. 先用于现有报告的证据归纳和冲突解释；
2. 再允许 LLM 提出策略实验假设，但不直接激活；
3. 对输出做 schema 验证、引用绑定、模型/prompt 版本化和费用限额；
4. 模型不可用时完整保留现有规则链路。

## 7. 最终判断

AI-Stock-Agent 对当前项目最有价值的不是“接 DeepSeek”，而是把一次性筛选结果变成可持续评价的数据资产。建议优先建设候选结果追踪和时间序列验证，再把 LLM 放在解释与实验提案层。

不建议近期引入自动下单、LLM 直接改权重或所谓“自进化生产策略”。当前 StockTrading 的批次隔离、确定性规则和风险边界比该参考项目成熟，应以这些能力作为不可退让的底座。

## 一手来源

### 自进化选股候选（主要对标）

- [WolkenZhen/cn-stock-AI-agent 官方仓库](https://github.com/WolkenZhen/cn-stock-AI-agent)
- [官方 README](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/README.md)
- [主选股与结果回填](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/auto_strategy_optimizer.py)
- [DeepSeek 调用与权重提示词](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/llm_client.py)
- [技术因子和交易参考价位](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/trading_signal.py)
- [单股诊断](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/main.py)
- [日报生成](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/explainer.py)
- [依赖声明](https://github.com/WolkenZhen/cn-stock-AI-agent/blob/d9851cd8b72764c27adcb0e8cb2eb36722be5ab0/requirements.txt)

### 精确同名的 CrewAI 候选（Agent 架构补充）

- [liai030303/AI-Stock-Agent 官方仓库](https://github.com/liai030303/AI-Stock-Agent)
- [官方 README](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/README.md)
- [Agent 定义与 Provider 工厂](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/agents.py)
- [任务、输出契约与上下文](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/tasks.py)
- [AkShare 与搜索工具](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/tools.py)
- [顺序工作流](https://github.com/liai030303/AI-Stock-Agent/blob/99d28bd941da0ace3dd38dc2aaea1daf0e00b282/main.py)
- [最新提交](https://github.com/liai030303/AI-Stock-Agent/commit/99d28bd941da0ace3dd38dc2aaea1daf0e00b282)
