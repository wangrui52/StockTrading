# T008 P1 功能

## 目标
实现不破坏历史可复现性的筛选方案、笔记、设置和自定义规则。

## 对应 S/F
S1、S2、S6；F4。

## 约束
C001、C002、C005。

## 执行记录
先写五组 API 失败契约；实现软删除和版本化模型后补迁移、OpenAPI 与前端交互。

## 验证结果
后端 44 passed、覆盖率 94%；前端 9 passed，statements 92%、branches 77%、functions 70%；Ruff、typecheck、build 通过。

## 遗留风险
自定义规则回放和恢复冲突在 T009 差距审计中处理。

## 下一步
执行 T009 本地 E2E。
