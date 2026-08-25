import os
from datetime import date

import pytest

from app.adapters.akshare_market_data import AkShareMarketDataGateway
from app.ports.market_data import StockRecord

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="需要显式启用真实网络测试"),
]


def test_fetches_known_stock_history_from_real_akshare() -> None:
    stock = StockRecord("SH", "600000", "浦发银行", None, None, False)
    records = AkShareMarketDataGateway().daily_prices(stock, date.today())

    assert records
    assert {item.adjustment for item in records} == {"raw", "qfq"}
    assert all(item.volume >= 0 for item in records)
