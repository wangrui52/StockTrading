# T001 工程与测试基线

## 目标
建立前后端隔离工程和可执行测试基线。

## 对应 S/F
S1、S3、S4；F1。

## 约束
C001、C002、C003。

## 输入
PRD、技术设计、实施计划、本机运行时。

## 输出
Git 仓库、backend/frontend 目录、失败后转绿的 health 与 App 测试。

## 涉及文件
见实现计划任务 1。

## 执行记录
已建立 backend/frontend 独立依赖与测试配置。后端和前端测试均先因生产模块缺失出现目标红灯，再用最小实现转绿；前端生产构建已通过。

## 验证命令
后端 Pytest、前端 Vitest、格式与类型检查。

## 验证结果
通过。见 `evidence/T001-engineering-baseline.md`。

## 遗留风险
FastAPI TestClient 输出第三方弃用警告，当前不影响测试行为；后续升级或切换 ASGI transport 时处理。

## 下一步
执行 T002 指标引擎。
