# A 股市场宽度与成交额接口调研

调研日期：2026-09-02

## 结论

用户的质疑成立：**有正式的聚合数据时，不应为了展示上涨、下跌家数和成交额而重复保存、扫描全部历史日线。** 但“现成接口”要拆成两类能力：

| 指标 | 可用的正式来源 | 是否直接聚合 | 当前边界 |
| --- | --- | --- | --- |
| 沪、深市场/板块日成交额 | Tushare Pro `daily_info` | 是 | 文档只列 SH、SZ，不含北交所；盘后日频；需积分权限 |
| 指数/市场上涨、持平、下跌家数 | Choice 指标 `GainersNum`、`DrawNum`、`LosersNum` | 是 | Choice 是账号和权限型产品；购买或接入前必须确认“全 A”代码是否含北交所及指标套餐权限 |
| 免费、匿名、一次返回沪深京全 A 宽度 | 未发现交易所正式公开 REST API | — | 交易所官网有查询页面，稳定行情接入则属于许可产品，不等于开放 Web API |
| 不采购商业数据时的宽度 | 单日全市场逐股行情 | 否，需要 `count` | 只需抓一次当日轻量数据并保存每日一行聚合快照，不需要复制全部历史 |

因此，本项目最合适的优化不是笼统地“取消统计”，而是：

1. 成交额优先使用正式聚合字段；若要求沪深京统一口径，则补齐北交所正式来源，或者仍从同一逐股快照求和。
2. 上涨/下跌家数若接受采购，优先验证 Choice 正式指标；若不采购，从当日逐股快照计算即可。
3. 无论数据如何取得，数据库都只保存每天一行 `market_breadth_snapshot`，不要在每个同步批次复制数百万行历史价格和指标。

## 1. 正式、文档化的数据提供方接口

### 1.1 Tushare Pro：成交额已有聚合接口

[Tushare `daily_info` 官方文档](https://tushare.pro/document/2?doc_id=215)明确说明：

- 接口名为 `daily_info`，用于获取“交易所股票交易统计，包括各板块明细”；
- 支持 `SH_A`、`SZ_A`、上海/深圳市场及各板块代码；
- 直接返回 `amount`（交易金额，亿元）、`vol`（成交量，亿股）、`trans_count`（成交笔数，万笔）等字段；
- 600 积分可以调取，频次随积分变化；
- 当前文档的 `exchange` 仅列 `SH`、`SZ`，没有列出北交所。

所以，仅展示沪深成交额时没有必要遍历 5,000 多只股票求和。若产品文案写“全 A 成交额”且股票池包含北京证券交易所，则不能直接把 `SH_A + SZ_A` 冒充“沪深京全 A”；需要另补北交所，或明确显示“沪深 A 股成交额”。

Tushare 也提供按单个交易日返回全市场逐股记录的 [`daily`](https://tushare.pro/document/1?doc_id=27)，字段含 `pct_chg` 和 `amount`，官方还明确建议按日期获取全市场数据，而不是循环股票代码。这可把免费/低成本方案从“逐只抓历史”降为“每天一次单日快照”。不过它仍返回逐股记录，不是上涨、下跌家数聚合。

[`daily_basic`](https://tushare.pro/document/2?doc_id=32)目前提供逐股 `limit_status`：0 为平盘，1/2/3 为上涨类，4/5/6 为下跌类。它可辅助分类，但仍需本地计数，而且必须先确认停牌、新股、退市整理、B 股/基金等排除口径。该接口单次上限 6,000 条，接入时还应校验返回数和股票池覆盖率，避免全市场数量增长后静默截断。

### 1.2 Choice：有现成的上涨、持平、下跌家数指标

东方财富 Choice 官方《[Choice 金融终端使用指南](https://choice.eastmoney.com/FileDownload/Guide/Choice%E9%87%91%E8%9E%8D%E7%BB%88%E7%AB%AF%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97.pdf)》在“指数专项”中列出了：

- 上涨家数 `GainersNum`
- 持平家数 `DrawNum`
- 下跌家数 `LosersNum`

Choice 当前仍维护正式 QuantAPI 产品。[QuantAPI 下载中心](https://quantapi.eastmoney.com/Download?from=web)显示 Python、Java、C++、R 等版本均已更新到 2.7.5.0（2026-07-31）；[QuantAPI R 官方手册](https://quantapi.eastmoney.com/Upload/EMQuantAPI_R.html)说明 `CSS`/`CSD` 可查询股票、指数等品种的指标，具体指标通过指标手册或官网命令生成，并需要登录激活。

这说明“上涨/下跌家数”确实存在正式产品能力，但不能据此推导为免费匿名接口，也不能在未验证时认定任意“全 A”代码都包含沪、深、京。接入前应让供应商或试用账号实际返回一个目标交易日，确认：

- 目标代码的成分范围是否包含北交所；
- 停牌、零成交、新股、退市整理期股票如何归类；
- 指标是盘中实时值还是盘后最终值；
- 历史回溯、调用频次、展示和落库是否在授权范围内。

## 2. 交易所官方数据：公开网页不等于开放 API

### 2.1 面向公众的官网查询栏目

- 上交所官方[股票成交概况](https://www.sse.com.cn/market/stockdata/overview/day/index_his.shtml)提供“每日股票情况”，并明确股票合计的范围；例如自 2019-07-22 起包含主板 A、主板 B、科创板，不含股票回购。
- 深交所官网提供[股票成交日度概况](https://www.szse.cn/market/stock/deal/index.html)查询栏目。
- 北交所官网提供[股票统计数据](https://www.bse.cn/static/statisticdata.html)的日报、周报、月报和年度统计栏目。

这些页面适合作为官方口径核对来源，但调研中未发现交易所承诺给普通开发者使用、具备版本和兼容性保证、且一次返回“沪深京全 A 上涨/下跌家数”的免费 REST API。网页背后即使存在 JSON 请求，也只能归类为**官网网页内部接口**，不能因为域名属于交易所就自动视为对外开发者 API。

### 2.2 稳定的正式交易所接口属于许可接入产品

- [上证所 Level-1 行情官方说明](https://www.sseinfo.com/services/assortment/level1/)称其通过 BINARY、STEP 等协议，经 MDGW 或 VDE 接收，并单列“授权许可”和业务申请。
- [上证行情服务首页](https://www.sseinfo.com/services/assortment/market/)提供 Level-1/Level-2、收费标准、授权许可和技术文档。
- [深交所 Binary 行情数据接口规范](https://www.szse.cn/marketServices/technicalservice/interface/P020250328368568358456.pdf)面向会员、证券公司、信息服务商等行情接入参与方，并非匿名 Web API。
- [北交所境内行情授权指南](https://www.bse.cn/application/guide.html)明确实时基础行情和加工行情产品实行许可使用。

这些接口可以作为机构级稳定数据源，但其成本、接入方式和授权义务明显超出当前个人日线研究工作台的默认范围。

## 3. 网页内部接口与第三方抓取接口

当前仓库的真实边界是：

- `backend/app/adapters/tencent_market_data.py` 使用新浪 `Market_Center.getHQNodeStockCount/getHQNodeData` 获取 `hs_a` 股票池，使用腾讯 `proxy.finance.qq.com/.../newfqkline/get` 获取个股日线；
- `backend/app/adapters/tencent_realtime.py` 使用 `qt.gtimg.cn` 获取指定证券的报价；
- `backend/app/application/dashboard.py` 对当前批次、当前交易日的 `raw` 行计数并求和生成 `market_summary`。

这些腾讯、新浪地址是财经网页实际使用的源，但调研中没有找到对应的厂商正式开发者合同、字段版本、频控、SLA 或商业使用授权文档，因此应标注为**网页内部接口**，而不是“腾讯/新浪官方开放 API”。

AKShare 的 [`stock_zh_a_spot` 官方源代码](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_zh_a_sina.py)同样是对新浪网页源的封装，并在函数说明中直接警告重复运行会被新浪暂时封 IP、大量抓取容易封 IP。其官方仓库中关于东方财富 `push2` 的 [Issue #7098](https://github.com/akfamily/akshare/issues/7098)也记录了 2026 年出现连接中断和一批相关接口故障。AKShare 是开源项目的正式接口，但它封装的数据端点仍不是交易所或数据厂商承诺兼容性的公开行情 API。

因此，不建议再寻找一个未文档化的 `push2`、`qt.gtimg` 或新浪参数，直接把返回的聚合数字当作长期稳定依赖。它最多可以作为可替换适配器，并必须带覆盖率校验、日期校验、失败保留旧快照和来源标识。

## 4. 为什么项目此前仍要自己统计

此前本地统计并不是因为数学上复杂，而是为了保证**统计口径与候选股票池一致**：

- 上涨/下跌家数来自同一激活批次、同一交易日、同一 `hs_a` 覆盖范围；
- 成交额与这些股票使用同一份原始日线；
- 批次完整率低于 99% 时不展示汇总，避免缺失股票造成假结论。

这个口径价值仍然存在；真正不合理的是为了得到四个聚合数，让每个新批次再次保存完整历史行情和完整指标。**本地计算四个聚合值本身成本很低，重复获取和重复存储历史数据才是空间问题。**

## 5. 对 StockTrading 的建议

### 推荐方案：不新增付费依赖，改为每日轻量快照

近期先保留现有来源边界，不把系统稳定性再绑定到一个新的网页内部聚合端点：

1. 每个交易日只对当日 `raw` 行计算一次 `up/down/flat/amount`。
2. 落一行 `market_breadth_snapshot`，建议字段包括：
   - `trade_date`
   - `universe_id` / `universe_version`
   - `up_count`、`down_count`、`flat_count`
   - `suspended_count`、`missing_count`、`covered_count`、`expected_count`
   - `amount_yuan`
   - `source`、`source_as_of`、`calculation_version`
3. 唯一键使用 `(trade_date, universe_id, calculation_version)`，数据批次只引用该快照，不复制它，更不复制全部历史行情。
4. 历史策略环境需要保留“每天一行”的快照；只看当前看板则可只保留最新，但节省量已经没有必要牺牲历史可解释性。

按一年约 250 个交易日计算，即使加索引和 JSON 元数据，这张表通常也是 MB 级以内，而不是每个交易日增加数 GB。

### 可选方案：购买正式聚合数据

若愿意承担账号、积分或商业授权成本：

- 沪深成交额直接接 Tushare `daily_info`；北交所另找正式来源，并在补齐前明确标注“沪深 A 股”。
- 上涨/持平/下跌家数试用 Choice `GainersNum/DrawNum/LosersNum`，通过样例交易日与本地逐股统计对账后再切主源。
- 即使全部改用聚合 API，也仍要把返回值保存为每天一行的版本化证据快照，不能只在页面临时展示，否则历史策略评价无法复现。

## 最终回答

“有现成接口，为什么要自己统计？”——**成交额确实无需逐股统计；上涨/下跌家数也有 Choice 这类正式现成指标。** 此前自己统计的合理性仅在于不采购商业接口，并确保与当前新浪 `hs_a` 股票池完全同口径。优化时应优先消除重复历史存储：有正式聚合源就直接存聚合快照；没有时每天拉一次单日轻量行情、计算一次并只存一行，绝不为这几个数字复制全量历史。
