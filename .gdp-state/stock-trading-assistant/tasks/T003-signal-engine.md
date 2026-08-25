# T003 信号引擎

## 目标
实现状态型条件、跨日事件、趋势摘要和信号风险等级的统一领域计算。

## 对应 S/F
S2、S4；F2、F4。

## 约束
C002、C005。

## 输入
同一交易日序列的标准行情和指标快照。

## 输出
逐交易日、带规则版本的 `SignalEvaluation`。

## 涉及文件
`backend/app/domain/market.py`、`backend/app/domain/signals.py`、`backend/tests/unit/test_signal_engine.py`。

## 执行记录
先写 10 个信号与趋势测试并确认 module 缺失红灯；实现后补零前高边界测试，确认除零红灯后修复。

## 验证命令
`uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=70`；`uv run ruff check app tests`。

## 验证结果
21 passed；后端总覆盖率 97%；Ruff 通过。

## 遗留风险
真实行情回放与事件幂等入库属于 T004/T005。

## 下一步
执行 T004 数据库与 repository adapters。

