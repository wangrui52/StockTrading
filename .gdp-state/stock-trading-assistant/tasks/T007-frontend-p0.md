# T007 前端 API client 与页面

## 目标
实现以活动批次为上下文的研究工作台，覆盖 P0 用户闭环和独立状态。

## 对应 S/F
S1、S3、S6；F1。

## 约束
C001、C002、C005。

## 涉及文件
`frontend/src/shared/api/`、`frontend/src/features/`、`frontend/src/app/`。

## 执行记录
先将静态空页测试改成真实 loading/empty/error/success 契约并确认全红；实现页面后由覆盖率门槛推动补齐详情、表单、自选和提醒交互测试。

## 验证结果
8 tests passed；statements 91%、branches 76%、functions 80%；typecheck 和 build 通过。

## 遗留风险
真实浏览器视觉、键盘与窄屏验收属于 T009。

## 下一步
执行 T008 P1 功能。
