from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.sqlalchemy_repositories import SQLAlchemyReportStore
from app.infrastructure.models import (
    AnalysisReport,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    SignalEvent,
)


def create_stock_report(
    session: Session, batch: DataBatch, *, market: str, stock_code: str
) -> AnalysisReport:
    price = session.scalar(
        select(DailyPrice).where(
            DailyPrice.batch_id == batch.id,
            DailyPrice.market == market,
            DailyPrice.stock_code == stock_code,
            DailyPrice.trade_date == batch.trade_date,
            DailyPrice.adjustment == "raw",
        )
    )
    indicator = session.scalar(
        select(DailyIndicator).where(
            DailyIndicator.batch_id == batch.id,
            DailyIndicator.market == market,
            DailyIndicator.stock_code == stock_code,
            DailyIndicator.trade_date == batch.trade_date,
        )
    )
    signals = session.scalars(
        select(SignalEvent).where(
            SignalEvent.batch_id == batch.id,
            SignalEvent.market == market,
            SignalEvent.stock_code == stock_code,
            SignalEvent.trade_date <= batch.trade_date,
            SignalEvent.rule_version == batch.rule_version,
            SignalEvent.trade_date == batch.trade_date,
        )
    ).all()
    values = indicator.values if indicator else {}
    signal_codes = [item.rule_code for item in signals]
    close = price.close if price else None
    ma5 = values.get("ma5")
    ma20 = values.get("ma20")
    if close is None or ma5 is None or ma20 is None:
        trend = "样本不足，暂不生成趋势结论"
    elif close > ma20 and ma5 > ma20:
        trend = f"收盘价 {close:.4f}、MA5 {ma5:.4f} 均高于 MA20 {ma20:.4f}"
    else:
        trend = f"收盘价 {close:.4f}、MA5 {ma5:.4f} 与 MA20 {ma20:.4f} 未同时满足偏强条件"
    technical = ", ".join(f"{name}={values.get(name)}" for name in ("ma5", "ma20", "rsi14"))
    event_text = "、".join(signal_codes) if signal_codes else "无当日事件"
    content = f"""# {market}{stock_code} 交易辅助分析

## 数据口径与完整性

- 交易日：{batch.trade_date.isoformat()}
- 数据批次：{batch.id}
- 规则版本：{batch.rule_version}
- 数据完整率：{batch.completeness_rate:.2%}

## 趋势判断

{trend}（规则版本 {batch.rule_version}）。

## 技术指标

{technical}；指标采用前复权价格序列。

## 量能变化

成交量={price.volume if price else None} 股，成交额={price.amount if price else None} 元。

## 关注理由

当日规则事件：{event_text}。

## 风险与冲突信号

风险证据以事件 payload 和数据完整率为准；当前事件：{event_text}。

## 条件触发与失效条件

触发证据：{event_text}；若后续有效交易日不再满足对应规则，则该条件失效。

## 结论摘要

本摘要只复述批次 #{batch.id}、规则 {batch.rule_version} 的已落库指标与事件：{event_text}。

## 免责声明

本工具仅用于个人研究和信息整理，不构成投资建议。历史数据和技术指标不代表未来表现。
"""
    return SQLAlchemyReportStore(session).create_report(
        batch_id=batch.id,
        market=market,
        stock_code=stock_code,
        trade_date=batch.trade_date,
        rule_version=batch.rule_version,
        template_version="v1",
        content=content,
    )
