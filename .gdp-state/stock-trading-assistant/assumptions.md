# Assumptions

## A001 本地运行环境可用
- 状态：verified
- 证据：Python 3.13.3、Node 22.21.0、npm 10.8.2、uv、pnpm、Docker 已安装。

## A002 AkShare 当前接口可满足基础日线需求
- 状态：pending
- 验证方式：任务 5 的冻结契约测试和 live Spike。
- 被推翻后的影响：更换 adapter，不改变 domain 和前端 interface。

## A003 SQLite 足以支持单用户日线 V1
- 状态：pending
- 验证方式：全市场同步和筛选性能测试。
- 被推翻后的影响：切换 PostgreSQL adapter，不改变 application modules。

## A004 无既有代码需要兼容
- 状态：verified
- 证据：工作区开始时只有 PRD 和评审报告，且不是 Git 仓库。

