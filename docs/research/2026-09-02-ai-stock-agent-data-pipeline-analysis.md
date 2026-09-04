# ai-stock-agent 数据处理链路源码分析

## 1. 分析边界与结论

- 对标仓库：`/Users/wangrui/Documents/github/ai-stock-agent`
- 当前 checkout：`main`，commit `10677fef9198386ad691f9203741bb9d58d69003`
- 分析方式：静态源码审计；未联网调用第三方接口，未运行定时任务，未修改对标仓库。
- 工作树现状：审计开始时存在未跟踪的 `docs/`，本次未触碰。README 仅作导航，以下结论均以实现代码为依据。

先给结论：这个项目没有实现“全市场涨跌家数/市场宽度”，也没有“先扫描全市场、再让 AI 分析候选股”的链路。它把业务拆成三条相对独立的路径：

1. 股票主数据：管理员手动从巨潮资讯导入，按股票代码 upsert。
2. 行情数据：实时行情按单只股票即时查询腾讯接口、不落库；历史 K 线每天对数据库中的全部股票逐股抓取新浪最近 1023 根数据，数据库依靠唯一键忽略重复行。
3. AI 分析：核心是逐条分析新闻，每条新闻最多产生 3 个股票信号；另有用户指定单只股票后才执行的新闻回溯、信号生成、研报和回测工具。没有全市场候选筛选器。

因此，它值得借鉴的是“稳定自然键去重、派生周期查询时计算、AI 只处理事件/用户指定标的”，而不是它的抓取调度：其 K 线任务虽然不会重复落库历史，却每天对每只股票重新下载最多 1023 根、且同时请求 `1d` 和 `4h`，网络与队列开销仍偏大。

## 2. 股票范围与导入

### 2.1 持久化股票池：管理员手动导入，不由定时任务自动维护

`POST /stocks/import` 受 JWT 和管理员守卫保护，调用 `StocksService.importFromCninfo()`；并非普通用户访问页面时自动入库，也不在三个系统 Cron 中（[stocks.controller.ts:20](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.controller.ts#L20)、[stocks.controller.ts:100](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.controller.ts#L100)、[stocks.service.ts:254](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L254)）。

导入器默认只请求 `https://www.cninfo.com.cn/new/data/szse_stock.json`，读取 `stockList`，再按代码首位映射 SH/SZ/BJ。源码注释称其为“全量 A 股”，但实现没有像市场列表接口那样并发调用深、沪、北三个 URL；未进行真实接口响应验证前，不能仅凭注释断言这个单 URL 当前确实覆盖沪深京全 A（[stocks.importer.ts:52](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.importer.ts#L52)、[stocks.importer.ts:72](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.importer.ts#L72)、[stocks.importer.ts:90](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.importer.ts#L90)、[stocks.importer.ts:128](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.importer.ts#L128)）。

解析后以 500 条为一批，先查询已存在 code 统计新增/更新，再按 `stocks.code` 冲突更新名称、交易所和更新时间；不会每次复制一份股票主数据（[stocks.importer.ts:157](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.importer.ts#L157)、[stocks.importer.ts:184](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.importer.ts#L184)、[stocks.importer.ts:208](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.importer.ts#L208)）。数据库也把 `stocks.code` 定义为全局唯一（[schema.ts:70](/Users/wangrui/Documents/github/ai-stock-agent/packages/shared/src/db/schema.ts#L70)）。

这也意味着当前模型无法表达“不同市场存在相同代码”的情况；对当前主要面向 A 股的范围问题不大，但若以后扩展港股/美股，唯一键应改为 `(market, code)`。

### 2.2 页面选择用的“市场列表”与持久化股票池是两套路径

`GET /stocks/market-list` 不读 `stocks` 表，而是并发请求巨潮深市、沪市、北交所三个列表，按 code 在内存合并去重，结果只放进进程内 5 分钟缓存（[stocks.service.ts:264](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L264)、[stocks.service.ts:283](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L283)、[stocks.service.ts:299](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L299)、[stocks.service.ts:314](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L314)、[stocks.service.ts:335](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L335)）。

所以“页面能选到股票”不等于 `stocks` 表已经有这只股票；而 K 线定时任务只遍历 `stocks` 表。首次部署若未调用管理员导入接口，K 线任务会处理 0 只股票。

此外，新闻 AI 分析得到明确股票代码和名称后，也会按 code upsert `stocks`；即持久化股票池还可能由新闻信号被动补充（[stocks.service.ts:105](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L105)、[news-analyze.processor.ts:214](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/news/processors/news-analyze.processor.ts#L214)）。

## 3. 行情来源与抓取粒度

### 3.1 实时行情：腾讯，按单股请求，不落库

`GET /stocks/quote/:code` 每次拼接 `http://qt.gtimg.cn/q={sh|sz|bj}{code}`，请求单只股票，解析最新价、昨收、开高低、成交量/额、换手率、PE、PB 和涨跌幅，然后直接返回。这里没有批量全市场报价、没有数据库写入，也没有持久化缓存（[stocks.controller.ts:67](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.controller.ts#L67)、[stocks.service.ts:447](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L447)、[stocks.service.ts:473](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L473)、[stocks.service.ts:486](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L486)、[stocks.service.ts:566](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L566)）。

### 3.2 历史 K 线：新浪，逐股逐周期、每次最多 1023 根

历史行情来自新浪 `CN_MarketDataService.getKLineData`。每个请求只带一个 symbol 和一个 scale，固定 `datalen=1023`（[sina.crawler.ts:17](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/crawlers/sina.crawler.ts#L17)、[sina.crawler.ts:45](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/crawlers/sina.crawler.ts#L45)、[sina.crawler.ts:59](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/crawlers/sina.crawler.ts#L59)）。

周期处理有两个重要事实：

- `4h` 使用 `scale=240`，代码将其解释为 A 股一个完整交易日的一根 K 线。
- `1d` 先用 `scale=1440`，空结果时再请求 `scale=240`；因此在常见 fallback 路径下，`1d` 与 `4h` 可能保存相同粒度的数据，只是 interval 标签不同。
- 北交所代码虽然股票列表和实时行情支持，但 K 线 `buildSymbol` 只接受 6/0/3 开头，8/4 开头会直接返回空；所以全市场口径实际上不一致。

证据见 [sina.crawler.ts:77](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/crawlers/sina.crawler.ts#L77)、[sina.crawler.ts:96](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/crawlers/sina.crawler.ts#L96)、[sina.crawler.ts:120](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/crawlers/sina.crawler.ts#L120)。

`7d/30d` 不单独抓取、也不持久化，而是查询时基于 `1d`（无数据则用 `4h`）实时聚合，这一点能避免保存可重复计算的派生周期（[klines.service.ts:10](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/klines.service.ts#L10)、[klines.service.ts:25](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/klines.service.ts#L25)、[klines.service.ts:43](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/klines.service.ts#L43)）。

## 4. 涨跌统计与市场概览

源码中没有上涨家数、下跌家数、平盘家数、涨停/跌停家数或全市场成交额的采集、表结构、聚合服务和 API。

现有仪表盘的“统计”是：今日信号数、新闻总数、股票主数据总数、用户最大回测收益、近 7 日信号数趋势；并不是市场涨跌概览（[dashboard.service.ts:16](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/dashboard/dashboard.service.ts#L16)、[dashboard.service.ts:37](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/dashboard/dashboard.service.ts#L37)、[dashboard.service.ts:48](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/dashboard/dashboard.service.ts#L48)、[dashboard.service.ts:104](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/dashboard/dashboard.service.ts#L104)）。前端也只展示这四项和信号趋势（[dashboard/page.tsx:116](</Users/wangrui/Documents/github/ai-stock-agent/apps/frontend/app/(layout)/dashboard/page.tsx#L116>)、[dashboard/page.tsx:161](</Users/wangrui/Documents/github/ai-stock-agent/apps/frontend/app/(layout)/dashboard/page.tsx#L161>)）。

腾讯单股 quote 的 `change/changePercent` 只是详情或交易操作时的即时字段，不能等价为全市场涨跌统计（[stocks.service.ts:55](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L55)、[stocks.service.ts:591](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/stocks/stocks.service.ts#L591)）。

因此，对“这个项目怎么处理涨跌统计”的准确回答是：**没有处理，也没有为这项功能保存全市场历史。** 它不能作为“涨跌统计应该自己逐股算还是调用现成接口”的实现样板。

## 5. 历史行情与指标如何存储

### 5.1 K 线是共享事实表，不按同步批次复制

`klines` 只保存 `stockId, interval, datetime, OHLCV`，联合唯一键为 `(stockId, interval, datetime)`，没有 `batchId`（[schema.ts:156](/Users/wangrui/Documents/github/ai-stock-agent/packages/shared/src/db/schema.ts#L156)）。抓取消费者把整次响应转换为行，并使用 `onConflictDoNothing`；重复抓回的 1023 根不会再次插入（[kline-fetch.processor.ts:58](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/processors/kline-fetch.processor.ts#L58)、[kline-fetch.processor.ts:66](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/processors/kline-fetch.processor.ts#L66)、[kline-fetch.processor.ts:78](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/processors/kline-fetch.processor.ts#L78)）。同步抓取路径同样如此（[klines.service.ts:88](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/klines.service.ts#L88)、[klines.service.ts:95](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/klines.service.ts#L95)、[klines.service.ts:106](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/klines/klines.service.ts#L106)）。

好处是数据库随新增交易日近似线性增长，不会因每日批次重复保存全部历史。代价是：

- 历史源若修订已有 OHLCV，`onConflictDoNothing` 不会更新旧行。
- 没有来源版本、抓取批次或校验快照，不能像 StockTrading 那样重现“某一批次当时看到的数据”。
- `fetchedCount/日志中的入库 N 根` 实际是接口返回/尝试插入的行数，不是数据库真实新增行数。

### 5.2 没有行情技术指标表

当前 schema 没有 MA/MACD/RSI/BOLL 等个股技术指标表，K 线抓取流程也不计算这些指标。项目中出现的“指标”主要是回测/账户的收益率、回撤、波动率、胜率等绩效指标，它们由交易和回测结果计算，不是随行情为全市场逐日预计算的技术指标。

这一点对存储优化非常关键：它之所以体积较小，不只是因为 K 线去重，也因为没有为每个股票、每个历史交易日、每个批次重复保存一套技术指标与信号。

## 6. 增量更新与定时任务

### 6.1 数据库写入“去重”，但网络抓取并非真正增量

每天 09:00 的 K 线任务读取 `stocks` 全表，对每只股票分别投递 `1d` 和 `4h` 两条 MQ 消息（[kline-update.job.ts:9](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/kline-update.job.ts#L9)、[kline-update.job.ts:29](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/kline-update.job.ts#L29)、[kline-update.job.ts:39](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/kline-update.job.ts#L39)、[kline-update.job.ts:43](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/kline-update.job.ts#L43)）。每条消息的 crawler 仍请求最近 1023 根，API 没有使用库内最大 datetime 作为 start 参数。

所以应区分：

- **存储层增量**：是。唯一键 + `onConflictDoNothing` 只增加新 datetime。
- **下载层增量**：否。每天重复下载窗口内全部历史。
- **计算层增量**：项目没有行情指标计算；不存在相应问题。

如果持久化股票为约 5500 只，一轮理论上会产生约 11000 个请求任务；MQ 的 500ms delay 是否能实现全局限速还取决于 QueueService 的消费实现，至少 Job 本身没有按批次合并行情请求。

### 6.2 三个系统任务与恢复边界

种子脚本只写入三个配置：新闻抓取 19:00、新闻 AI 分析 20:00、K 线更新 09:00；不导入股票或行情（[seed.ts:7](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/drizzle/seed.ts#L7)、[seed.ts:89](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/drizzle/seed.ts#L89)）。系统任务真正的时间由代码中的 `@Cron` 硬编码，DB 的 `enabled` 用于运行前开关检查（[kline-update.job.ts:29](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/kline-update.job.ts#L29)、[news-crawl.job.ts:35](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/news-crawl.job.ts#L35)、[news-analyze.job.ts:38](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/news-analyze.job.ts#L38)、[scheduler.service.ts:160](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/scheduler.service.ts#L160)）。

动态通知任务保存在 `scheduler_jobs`，服务启动时从 DB 恢复并重新注册；但这不代表系统停机期间错过的任务会补跑（[scheduler.service.ts:42](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/scheduler.service.ts#L42)、[scheduler.service.ts:78](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/scheduler.service.ts#L78)）。

新闻抓取每天只取东方财富财经导读前 3 页，URL 逐条入队；新闻以 sourceUrl 的 MD5 唯一去重，已存在就跳过（[news-crawl.job.ts:10](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/news-crawl.job.ts#L10)、[news-crawl.job.ts:49](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/news-crawl.job.ts#L49)、[news-crawl.processor.ts:39](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/news/processors/news-crawl.processor.ts#L39)、[schema.ts:85](/Users/wangrui/Documents/github/ai-stock-agent/packages/shared/src/db/schema.ts#L85)）。

## 7. AI 分析范围：逐新闻/指定股票，不是全市场逐股

### 7.1 定时 AI 是逐条新闻分析

20:00 任务查询最近两天 `PENDING` 新闻，然后逐条投递或同步调用 DeepSeek（[news-analyze.job.ts:13](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/news-analyze.job.ts#L13)、[news-analyze.job.ts:51](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/news-analyze.job.ts#L51)、[news-analyze.job.ts:71](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/scheduler/jobs/news-analyze.job.ts#L71)）。

每条新闻只调用一次 LLM，解析后最多保留 3 条信号；再按 LLM 提取出的股票代码 upsert 股票、写 signal。它不是遍历股票池让 LLM 逐股判断（[news-analyze.processor.ts:97](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/news/processors/news-analyze.processor.ts#L97)、[news-analyze.processor.ts:120](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/news/processors/news-analyze.processor.ts#L120)、[news-analyze.processor.ts:131](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/news/processors/news-analyze.processor.ts#L131)、[news-analyze.processor.ts:137](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/news/processors/news-analyze.processor.ts#L137)）。

### 7.2 用户追踪是显式指定一只股票

`backtrackHistory(stockCode)` 对指定代码构造 4 组搜索词，联网搜索合并去重后最多送 30 条结果给 DeepSeek，最终最多保存用户要求的 100 条新闻；不是对全市场运行（[tracking.service.ts:60](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L60)、[tracking.service.ts:85](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L85)、[tracking.service.ts:109](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L109)、[tracking.service.ts:129](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L129)、[tracking.service.ts:550](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L550)）。

“一键生成历史信号”也只查询该股票或该回溯批次的 `PENDING` 新闻，单次最多 30 条，逐新闻调用 AI，并在返回结果时只保留目标股票代码匹配的信号（[tracking.service.ts:863](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L863)、[tracking.service.ts:915](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L915)、[tracking.service.ts:927](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L927)、[tracking.service.ts:947](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/tracking/tracking.service.ts#L947)）。

Agent 的工具清单也只有持仓、新闻、信号、指定股票研报/回测/联网新闻和创建定时任务，没有“全市场扫描”或“候选股排名”工具（[tool-meta.ts:18](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/agent/tools/tool-meta.ts#L18)、[tool-meta.ts:54](/Users/wangrui/Documents/github/ai-stock-agent/apps/backend/src/agent/tools/tool-meta.ts#L54)）。

所以“AI 只处理候选”也不是准确描述：该项目根本没有确定性的候选生成阶段。AI 处理的是新闻事件，或用户明确指定的个股；股票信号本身是 AI 从新闻中抽取的结果。

## 8. 与 StockTrading 当前方案对比

StockTrading 当前的批次模型会：获取全市场股票；若存在近 10 天的上一活跃批次，则只从来源回拉最近 10 天，但把上一批次该股票的全部历史与新窗口合并；随后把合并后的全部价格写入新 `batch_id`，并基于全部 qfq 历史重新计算、保存每个历史日的指标和信号（[sync_pipeline.py:196](../../backend/app/application/sync_pipeline.py#L196)、[sync_pipeline.py:204](../../backend/app/application/sync_pipeline.py#L204)、[sync_pipeline.py:257](../../backend/app/application/sync_pipeline.py#L257)、[sync_pipeline.py:454](../../backend/app/application/sync_pipeline.py#L454)、[sync_pipeline.py:538](../../backend/app/application/sync_pipeline.py#L538)、[sync_pipeline.py:562](../../backend/app/application/sync_pipeline.py#L562)）。`daily_price`、`daily_indicator`、`signal_event` 的唯一键都含 `batch_id`，所以不同批次间允许相同历史重复存在（[models.py:114](../../backend/app/infrastructure/models.py#L114)、[models.py:158](../../backend/app/infrastructure/models.py#L158)、[models.py:173](../../backend/app/infrastructure/models.py#L173)）。

| 维度 | ai-stock-agent | StockTrading 当前实现 |
|---|---|---|
| 股票主数据 | code 唯一，重复导入 upsert | market+code 业务实体，批次外维护 |
| K 线事实 | `(stockId, interval, datetime)` 唯一，无批次复制 | 唯一键含 batch_id，每批复制完整历史 |
| 历史修订 | 冲突忽略，旧历史不能被修订 | 新批次可保留修订后的完整版本 |
| 指标 | 不保存个股技术指标 | 每批、每股、每历史日重新计算并保存 |
| 信号/候选 | AI 新闻信号，无全市场候选筛选 | 确定性规则扫描全市场并保存候选 |
| 市场涨跌统计 | 未实现 | 可由全市场日行情计算，但不应因此复制完整历史 |
| 下载增量 | 非真正增量，每日重复取 1023 根 | 近批次通常只向来源回拉 10 天，再复制旧历史 |
| 审计重现 | 弱，无行情批次版本 | 强，可按 batch/rule_version 重现 |

两者是在优化不同目标：ai-stock-agent 优先节省存储但牺牲历史版本与修订能力；StockTrading 用不可变批次保证完整性、激活回滚和策略审计，但当前把“批次元数据版本化”实现成了“每批物理复制全部事实和派生数据”，成本过高。

## 9. 可借鉴点与不应照搬项

### 9.1 建议借鉴

1. **把行情事实从批次快照中拆出来。** `daily_price` 以稳定自然键（建议 `(source, market, stock_code, trade_date, adjustment)`）保存一份；同步运行只记录 batch 与事实版本/水位的关联。若要支持来源修订，可增加 revision 或有效期，而非复制整个历史。
2. **涨跌统计单独建轻量日快照。** 如果没有可靠聚合接口，当天只拉一次全市场轻量报价，在内存统计后保存 `trade_date, scope, up/down/flat/limit_up/limit_down/amount, source` 一行；不要让这项功能依赖全历史 K 线、指标和信号复制。
3. **派生周期查询时计算。** 类似对标项目的 7d/30d 聚合，能由日线稳定计算的展示周期无需重复存储。
4. **AI 限定到候选/事件/显式个股。** StockTrading 已有确定性候选引擎，适合先用规则扫描全市场，再只让 AI 对 Top N 候选或用户指定股票做解释；不要用 LLM 逐股扫描约 5500 只。
5. **内容类数据用稳定指纹去重。** 新闻以 URL hash 去重的模式可以借鉴到外部证据，但要保留来源、抓取时间和版本，不应覆盖批次归属来伪装不可变快照。
6. **区分下载增量、存储增量、计算增量。** 优化验收应分别统计第三方请求数、下载行数、数据库新增/更新行数、指标重算股票数，避免“onConflict 去重”被误称为端到端增量。

### 9.2 不建议照搬

1. 不要照搬“每天每股两周期、每次 1023 根”的 K 线抓取；应根据最大已落库交易日请求缺口，并对前复权修订设置低频重校验窗口。
2. 不要把同一日粒度的数据同时标为 `1d` 和 `4h` 保存；先明确业务语义。
3. 不要照搬 `onConflictDoNothing` 处理行情修订；这会永久保留旧错值。可采用原始行情版本表或基于来源校验时间的受控更新。
4. 不要把 `stocks.code` 设为跨市场全局唯一；StockTrading 当前 `(market, stock_code)` 更稳妥。
5. 不要把这个项目当作涨跌统计参考实现，因为它根本没有该功能。

## 10. 对当前优化决策的直接回答

如果 StockTrading 仍需全市场候选筛选，推荐的目标不是退化成“只同步自选股”，而是：

- 全市场保留一份可修订的日线事实；每日只补当天/缺口。
- 指标按 `(rule_version, price_revision)` 缓存或只保留策略所需窗口，避免每批全历史复制。
- 每次运行保留轻量 batch manifest、完整率、来源水位、规则版本和候选快照，以维持审计与激活/回滚。
- 市场涨跌统计作为独立日快照获取或计算。
- AI 只消费确定性候选 Top N、新闻事件或用户指定股票。

这样既保留 StockTrading 的全市场筛选、批次激活和策略反馈优势，又能获得 ai-stock-agent 的主要存储收益；不必在“全市场能力”和“数据库无限膨胀”之间二选一。
