# T001 工程与测试基线证据

## 红灯

- 后端：`uv run pytest tests/test_health.py -v` 因 `ModuleNotFoundError: No module named 'app'` 失败。
- 前端：`pnpm test --run` 因无法解析 `./App` 失败。
- 应用壳行为扩展后，前端因缺少“主导航”可访问元素失败。

## 绿灯

- 后端：1 passed；分支覆盖率 100%，门槛 70% 通过。
- 前端：1 test passed。
- 前端构建：TypeScript 和 Vite production build 通过，86 modules transformed。

## 运行时

- Python 3.13.3，FastAPI 0.141.1。
- Node 22.21.0，React 19.2.8，Vite 7.3.6，Vitest 3.2.7。

## 未验证

- 尚未启动两个常驻进程进行浏览器验证。
- 当前页面只是 T001 应用壳，PRD 业务页面未实现。

