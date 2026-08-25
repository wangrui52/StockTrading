from sqlalchemy.orm import Session

from app.adapters.sqlalchemy_repositories import SQLAlchemyReportStore
from app.infrastructure.models import AnalysisReport, DataBatch


def create_stock_report(
    session: Session, batch: DataBatch, *, market: str, stock_code: str
) -> AnalysisReport:
    content = f"""# {market}{stock_code} 交易辅助分析

- 交易日：{batch.trade_date.isoformat()}
- 数据批次：{batch.id}
- 规则版本：{batch.rule_version}
- 数据完整率：{batch.completeness_rate:.2%}

本报告仅用于日线数据研究，不构成投资建议。
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
