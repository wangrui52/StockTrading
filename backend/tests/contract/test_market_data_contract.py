from datetime import date

import pandas as pd
import pytest

from app.adapters.akshare_market_data import AkShareMarketDataGateway
from app.ports.market_data import MarketDataUnavailable


class FrozenAkShare:
    __version__ = "1.18.94"

    def tool_trade_date_hist_sina(self) -> pd.DataFrame:
        return pd.DataFrame({"trade_date": [date(2025, 3, 31)]})

    def stock_info_a_code_name(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "code": ["600000", "000001", "430047", "900901"],
                "name": ["浦发银行", "平安银行", "诺思兰德", "B股"],
            }
        )

    def stock_zh_a_hist(self, **kwargs: str) -> pd.DataFrame:
        adjustment = kwargs["adjust"]
        close = 10.2 if adjustment == "" else 9.8
        return pd.DataFrame(
            {
                "日期": [date(2025, 3, 31)],
                "股票代码": [kwargs["symbol"]],
                "开盘": [10.0],
                "收盘": [close],
                "最高": [10.5],
                "最低": [9.5],
                "成交量": [1234],
                "成交额": [1_250_000.0],
                "涨跌幅": [2.0],
                "换手率": [1.5],
            }
        )


def test_akshare_adapter_normalizes_market_fields_and_units() -> None:
    adapter = AkShareMarketDataGateway(FrozenAkShare())

    assert adapter.is_trade_date(date(2025, 3, 31))
    stocks = adapter.list_stocks()
    assert [(item.market, item.stock_code) for item in stocks] == [
        ("SH", "600000"),
        ("SZ", "000001"),
        ("BJ", "430047"),
    ]

    records = adapter.daily_prices(stocks[0], date(2025, 3, 31))
    assert [item.adjustment for item in records] == ["raw", "qfq"]
    assert records[0].volume == 123_400
    assert records[0].amount == 1_250_000.0
    assert records[0].pct_change == 2.0
    assert records[1].close == 9.8


def test_akshare_errors_are_standardized() -> None:
    class BrokenAkShare(FrozenAkShare):
        def stock_zh_a_hist(self, **kwargs: str) -> pd.DataFrame:
            raise TimeoutError("source timeout")

    adapter = AkShareMarketDataGateway(BrokenAkShare())
    stock = adapter.list_stocks()[0]

    with pytest.raises(MarketDataUnavailable, match="600000"):
        adapter.daily_prices(stock, date(2025, 3, 31))
