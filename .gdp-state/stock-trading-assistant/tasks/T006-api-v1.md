# T006 P0 REST interface

## 目标
以版本化 REST 契约暴露 P0 查询与命令，并让前端只依赖 OpenAPI。

## 对应 S/F
S1、S2、S3；F1。

## 约束
C001、C005。

## 涉及文件
`backend/app/api/v1/`、`backend/app/application/`、`backend/openapi.json`、`backend/tests/contract/test_api_v1.py`。

## 执行记录
先写完整用户闭环 API 契约红灯，再实现 application service、路由和统一错误；最后增加显式响应模型与 OpenAPI 快照回归。

## 验证结果
39 passed、1 skipped；覆盖率 94%；Ruff 通过。

## 遗留风险
远程认证、真实并发同步和浏览器消费待后续 task。

## 下一步
执行 T007 前端 API client 与页面。
