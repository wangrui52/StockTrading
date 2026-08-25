# Assumptions

## A001 本地运行环境可用
- 状态：verified
- 证据：Python 3.13.3、Node 22.21.0、npm 10.8.2、uv、pnpm、Docker 已安装。

## A002 AkShare 当前接口可满足基础日线需求
- 状态：partially_verified
- 证据：冻结契约测试覆盖股票池、raw/qfq、指数和交易日历；真实 `600000` 小样本已通过。
- 未验证：全市场长期稳定性与限流仍需上线前压测。
- 被推翻后的影响：更换 adapter，不改变 domain 和前端 interface。

## A003 SQLite 足以支持单用户日线 V1
- 状态：partially_verified
- 证据：100 只 × 250 日固定样本同步、筛选、报告、备份、容器重启恢复均通过。
- 未验证：6,000 只全市场首次同步与长期容量压测。
- 被推翻后的影响：切换 PostgreSQL adapter，不改变 application modules。

## A004 无既有代码需要兼容
- 状态：verified
- 证据：工作区开始时只有 PRD 和评审报告，且不是 Git 仓库。
