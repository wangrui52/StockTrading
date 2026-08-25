# T002 指标引擎证据

## 红灯

- 首次因 `No module named 'app.domain'` 失败。
- 交易日乱序测试首次因未抛出 `ValueError` 失败。

## 绿灯

- MA5/10/20/60 窗口与样本不足通过。
- MACD 首值初始化、DIF、DEA 和双倍柱通过显式数值断言。
- Wilder RSI 上涨、下跌、横盘边界通过。
- BOLL 使用总体标准差，量比和零成交量边界通过。
- 严格交易日顺序 invariant 通过。
- 后端全量 10 passed，覆盖率 96%，Ruff 通过。

## 未验证

尚未与真实 AkShare 冻结样本和外部基准库对比。

