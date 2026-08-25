# T002 指标引擎

## 目标
实现 PRD 定义的统一、纯领域指标计算。

## 对应 S/F
S2、S4；F2、F4。

## 约束
C002。

## 输入
严格按交易日升序的标准前复权收盘价和原始成交量。

## 输出
逐交易日的 MA、MACD、RSI、BOLL、量比和不可用原因。

## 涉及文件
`backend/app/domain/market.py`、`backend/app/domain/indicators.py`、`backend/tests/unit/test_indicator_engine.py`。

## 执行记录
先创建 8 个公式/边界测试并确认 domain module 缺失红灯，随后实现引擎；再补严格交易日排序测试，确认未抛错红灯后增加 interface invariant。

## 验证命令
`uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=70`；`uv run ruff check app tests`。

## 验证结果
10 passed；后端总覆盖率 96%；Ruff 通过。

## 遗留风险
当前金样本为人工可验证序列；任务 5 需补真实冻结行情样本与基准库对比。

## 下一步
执行 T003 信号引擎。

