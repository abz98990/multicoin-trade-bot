"""Symbols Binance does not list, remembered between runs."""
from datetime import datetime as _datetime

from sqlalchemy import Column, DateTime, String

from .base import Base


class UnknownTicker(Base):  # pylint: disable=too-few-public-methods
    """
    A symbol that came back empty from the exchange.

    Discovering one costs a full ticker download, and the set is rebuilt from
    scratch on every restart because the cache lives in memory. Persisting it
    turns roughly 25 downloads per startup into none.

    Entries carry a timestamp and are re-checked after a while: a pair absent
    today may be listed next month, and a permanent record would keep the bot
    blind to it forever.
    """

    __tablename__ = "unknown_tickers"

    symbol = Column(String, primary_key=True)
    datetime = Column(DateTime)

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.datetime = _datetime.utcnow()

    def info(self):
        return {"symbol": self.symbol, "datetime": self.datetime.isoformat()}
