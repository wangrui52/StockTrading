import re
from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

import requests

from app.adapters.tencent_market_data import TencentMarketDataGateway
from app.ports.market_data import MarketDataUnavailable
from app.ports.realtime import RealtimeQuote


class TencentRealtimeGateway(TencentMarketDataGateway):
    source = "tencent-realtime-v1"

    def quotes(self, symbols: list[str]) -> list[RealtimeQuote]:
        if not symbols or len(symbols) > 100:
            raise ValueError("每组报价需要 1 至 100 个股票代码")
        try:
            response = self.get("https://qt.gtimg.cn/", params={"q": ",".join(symbols)}, timeout=15)
            response.raise_for_status()
            response.encoding = "gb18030"
        except (requests.RequestException, TimeoutError) as error:
            raise MarketDataUnavailable("腾讯实时报价请求失败，请稍后重试") from error
        expected = set(symbols)
        quotes = {}
        for symbol, value in re.findall(r'v_((?:sh|sz|bj)\d{6})="([^"\r\n]*)";', response.text):
            if symbol not in expected:
                continue
            fields = value.split("~")
            try:
                if fields[2] != symbol[2:]:
                    continue
                price = self._number(fields[3])
                pct = self._number(fields[32], signed=True) if price > 0 else None
                volume = self._number(fields[6]) * (1 if symbol.startswith("sh68") else 100)
                # 57 为万元精确值，37 为四舍五入后的万元；短响应使用 37。
                amount = self._number(fields[57] if len(fields) > 57 and fields[57] else fields[37])
                quoted_at = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(
                    tzinfo=ZoneInfo("Asia/Shanghai")
                )
                quotes[symbol] = RealtimeQuote(
                    market=symbol[:2].upper(),
                    stock_code=symbol[2:],
                    stock_name=fields[1],
                    latest_price=price or None,
                    pct_change=pct,
                    volume=round(volume),
                    amount=amount * 10_000,
                    quoted_at=quoted_at,
                )
            except (ValueError, IndexError):
                # 缺失或损坏只影响这一只，调用方按请求股票池统计缺失。
                continue
        return list(quotes.values())

    @staticmethod
    def _number(value: str, *, signed: bool = False) -> float:
        number = float(value)
        if not isfinite(number) or (not signed and number < 0):
            raise ValueError("无效报价数字")
        return number
