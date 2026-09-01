from datetime import UTC, date, datetime

import pytest

from app.adapters.tencent_market_data import TencentMarketDataGateway
from app.ports.market_data import MarketDataUnavailable, StockRecord


class Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def history_get(url, *, params, timeout):
    assert timeout == 15
    symbol, _, start, end, count, adjustment = params["param"].split(",")
    assert end == ""  # 指定未来结束日期会漏掉来源的当天日线
    rows = [
        ["2026-08-26", "10", "10", "11", "9", "1234", {}, "1.5", "125"],
        ["2026-08-27", "10", "11", "12", "9", "2345", {}, "2.5", "250"],
    ]
    quote = [""] * 33
    quote[3], quote[30], quote[32] = "11", "20260827151500", "10.00"
    return Response(
        {"code": 0, "data": {symbol: {(adjustment + "day"): rows, "qt": {symbol: quote}}}}
    )


def test_ex_dividend_history_does_not_create_false_daily_crash():
    gateway = TencentMarketDataGateway(get=history_get)
    rows = gateway.daily_prices(
        StockRecord("SH", "600000", "测试", None, None, False), date(2026, 8, 27)
    )
    assert all(r.pct_change is None for r in rows if r.trade_date < date(2026, 8, 27))

    def missing_quote(url, *, params, timeout):
        response = history_get(url, params=params, timeout=timeout)
        symbol = params["param"].split(",")[0]
        response.body["data"][symbol].pop("qt")
        return response

    rows = TencentMarketDataGateway(get=missing_quote).daily_prices(
        StockRecord("SH", "600000", "测试", None, None, False), date(2026, 8, 27)
    )
    assert all(r.pct_change is None for r in rows)


@pytest.mark.parametrize(
    "market,code,multiplier",
    [
        ("SH", "600000", 100),
        ("SZ", "000001", 100),
        ("SH", "688001", 1),
        ("SH", "689009", 1),
        ("BJ", "920047", 100),
    ],
)
def test_latest_daily_fields_units_and_raw_change(market, code, multiplier):
    gateway = TencentMarketDataGateway(get=history_get)
    rows = gateway.daily_prices(
        StockRecord(market, code, "测试", None, None, False), date(2026, 8, 27)
    )
    assert len(rows) == 4
    assert rows[-1].trade_date == date(2026, 8, 27)
    assert rows[-1].volume == 2345 * multiplier
    assert rows[-1].amount == 2_500_000
    assert rows[-1].turnover_rate == 2.5
    assert rows[-1].pct_change == pytest.approx(10)


def test_stale_or_empty_history_is_not_disguised_as_a_suspension():
    gateway = TencentMarketDataGateway(get=history_get)
    stock = StockRecord("SH", "600000", "测试", None, None, False)
    with pytest.raises(MarketDataUnavailable, match="最新日线"):
        gateway.daily_prices(stock, date(2026, 8, 28))


def test_index_uses_actual_date_and_does_not_return_stale_rows():
    gateway = TencentMarketDataGateway(get=history_get)
    rows = gateway.index_prices(date(2026, 8, 27))
    assert len(rows) == 4
    assert all(row.trade_date == date(2026, 8, 27) for row in rows)
    with pytest.raises(MarketDataUnavailable):
        gateway.index_prices(date(2026, 8, 28))


def test_stock_pool_rejects_incomplete_pagination():
    def get(url, *, params, timeout):
        if "StockCount" in url:
            return Response("2")
        return Response([{"symbol": "sh600000", "code": "600000", "name": "浦发银行"}])

    with pytest.raises(MarketDataUnavailable, match="股票池"):
        TencentMarketDataGateway(get=get).list_stocks()


def test_stock_pool_keeps_all_three_markets_and_names():
    def get(url, *, params, timeout):
        if "StockCount" in url:
            return Response("3")
        return Response(
            [
                {"symbol": "sh600000", "code": "600000", "name": "浦发银行"},
                {"symbol": "sz000001", "code": "000001", "name": "平安银行"},
                {"symbol": "bj920047", "code": "920047", "name": "诺思兰德"},
            ]
        )

    stocks = TencentMarketDataGateway(get=get).list_stocks()
    assert [(s.market, s.stock_code) for s in stocks] == [
        ("SH", "600000"),
        ("SZ", "000001"),
        ("BJ", "920047"),
    ]


def test_latest_trade_date_uses_shanghai_close_and_calendar():
    import pandas as pd

    class Calendar:
        def tool_trade_date_hist_sina(self):
            return pd.DataFrame(
                {
                    "trade_date": [
                        date(2026, 8, 26),
                        date(2026, 8, 27),
                        date(2026, 8, 28),
                        date(2026, 8, 31),
                    ]
                }
            )

    gateway = TencentMarketDataGateway(client=Calendar(), get=history_get)
    assert gateway.latest_trade_date(datetime(2026, 8, 27, 6, 59, tzinfo=UTC)) == date(2026, 8, 26)
    assert gateway.latest_trade_date(datetime(2026, 8, 27, 7, 1, tzinfo=UTC)) == date(2026, 8, 27)
    assert gateway.latest_trade_date(datetime(2026, 8, 30, 8, tzinfo=UTC)) == date(2026, 8, 28)


def test_default_calendar_network_is_bounded():
    calls = []

    def get(url, *, timeout):
        calls.append(timeout)
        raise TimeoutError("calendar timed out")

    with pytest.raises(MarketDataUnavailable):
        TencentMarketDataGateway(get=get).latest_trade_date()
    assert calls == [15]


def realtime_line(symbol, *, price="12.34", stamp="20260828110830", volume="1234"):
    fields = [""] * 58
    fields[1:7] = ["测试股票", symbol[2:], price, "12", "12.1", volume]
    fields[30], fields[32], fields[37], fields[57] = stamp, "2.83", "15", "15.1234"
    return f'v_{symbol}="{"~".join(fields)}";'


@pytest.mark.parametrize(
    "symbol,volume",
    [
        ("sh600000", 123400),
        ("sz000001", 123400),
        ("sh688981", 1234),
        ("sh689009", 1234),
        ("bj920001", 123400),
    ],
)
def test_realtime_uses_source_timestamp_and_normalizes_units(symbol, volume):
    from app.adapters.tencent_realtime import TencentRealtimeGateway

    class QuoteResponse:
        text = realtime_line(symbol)

        def raise_for_status(self):
            pass

    def get(url, *, params, timeout):
        assert params == {"q": symbol}
        assert timeout == 15
        return QuoteResponse()

    row = TencentRealtimeGateway(get=get).quotes([symbol])[0]
    assert row.latest_price == 12.34
    assert row.volume == volume
    assert row.amount == pytest.approx(151234)
    assert row.quoted_at.isoformat() == "2026-08-28T11:08:30+08:00"


@pytest.mark.parametrize(
    "price,stamp",
    [("NaN", "20260828110830"), ("inf", "20260828110830"), ("-1", "20260828110830"), ("12", "bad")],
)
def test_realtime_bad_record_does_not_hide_good_record(price, stamp):
    from app.adapters.tencent_realtime import TencentRealtimeGateway

    class QuoteResponse:
        text = realtime_line("sh600000", price=price, stamp=stamp) + realtime_line("sz000001")

        def raise_for_status(self):
            pass

    rows = TencentRealtimeGateway(get=lambda *a, **k: QuoteResponse()).quotes(
        ["sh600000", "sz000001"]
    )
    assert [row.stock_code for row in rows] == ["000001"]


def test_realtime_keeps_old_timestamp_and_marks_zero_price_unavailable():
    from app.adapters.tencent_realtime import TencentRealtimeGateway

    class QuoteResponse:
        text = realtime_line("sh600000", price="0", stamp="20260827150000", volume="0")

        def raise_for_status(self):
            pass

    row = TencentRealtimeGateway(get=lambda *a, **k: QuoteResponse()).quotes(["sh600000"])[0]
    assert row.latest_price is None
    assert row.pct_change is None
    assert row.quoted_at.date() == date(2026, 8, 27)


def test_realtime_network_timeout_is_actionable_and_group_size_is_bounded():
    from app.adapters.tencent_realtime import TencentRealtimeGateway

    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise TimeoutError("source timeout")

    gateway = TencentRealtimeGateway(get=timeout)
    with pytest.raises(MarketDataUnavailable, match="腾讯实时报价请求失败"):
        gateway.quotes(["sh600000"])
    assert calls == [15]
    with pytest.raises(ValueError):
        gateway.quotes(["sh600000"] * 101)
    assert calls == [15]
