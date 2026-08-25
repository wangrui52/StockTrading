# T005 数据 adapter 与同步 pipeline

## 目标
隔离易变化的数据源字段，实现可离线测试、可幂等重试且失败不污染活动批次的同步流水线。

## 对应 S/F
S1、S4；F3、F6。

## 约束
C002、C003、C005。

## 涉及文件
`backend/app/ports/market_data.py`、`backend/app/adapters/*market_data.py`、`backend/app/application/sync_pipeline.py`、同步集成与契约测试。

## 执行记录
先写 fake pipeline 失败测试，再实现状态机；然后以冻结 DataFrame 写 AkShare 契约红灯，按官方字段契约实现并追加 opt-in live 测试。

## 验证命令
`uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=70`；`uv run ruff check app tests migrations`；`RUN_LIVE_TESTS=1 uv run pytest tests/live/test_akshare_live.py -v`。

## 验证结果
默认集 33 passed、1 skipped；覆盖率 94%；Ruff 通过；真实数据 1 passed。

## 遗留风险
全市场性能、源站限流、失败重试退避与股票详情字段补全待后续。

## 下一步
执行 T006 P0 REST interface。
