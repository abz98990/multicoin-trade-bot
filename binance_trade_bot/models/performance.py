"""
Tables for measuring how the strategy is actually doing.

Kept together because they are one feature: an equity curve, the benchmark it
is measured against, and the strategy's own unit-accumulation claim. All three
timestamp in UTC, unlike CoinValue which uses local time.
"""
import json
from datetime import datetime as _datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base
from .coin import Coin


class BenchmarkAnchor(Base):  # pylint: disable=too-few-public-methods
    """
    The portfolio as it stood when measurement began.

    Every benchmark is derived from this one row, so "how are we doing" always
    means "compared with not having traded at all, from this moment". Stored
    rather than recomputed from the first equity row so that pruning or an
    edited history cannot silently move the goalposts.
    """

    __tablename__ = "benchmark_anchor"

    id = Column(Integer, primary_key=True)
    datetime = Column(DateTime)
    holdings = Column(String)  # JSON {symbol: units}
    total_usdt = Column(Float)
    btc_price = Column(Float)

    def __init__(self, holdings: dict, total_usdt: float, btc_price: float):
        self.datetime = _datetime.utcnow()
        self.holdings = json.dumps(holdings, sort_keys=True)
        self.total_usdt = total_usdt
        self.btc_price = btc_price

    def basket(self) -> dict:
        return json.loads(self.holdings or "{}")

    def info(self):
        return {
            "datetime": self.datetime.isoformat(),
            "holdings": self.basket(),
            "total_usdt": self.total_usdt,
            "btc_price": self.btc_price,
        }


class EquitySnapshot(Base):  # pylint: disable=too-few-public-methods
    """
    One row per sampling tick for the whole portfolio, not one per coin.

    n_unpriced exists because the old coin_value/SUM approach dropped anything
    it could not price - the bridge, and every coin whose BTC pair is delisted -
    so a partial valuation was indistinguishable from a real loss. Here a
    snapshot says how complete it is, and holdings makes any two rows checkable
    for comparability instead of merely assumed to be.
    """

    __tablename__ = "equity_history"

    id = Column(Integer, primary_key=True)
    datetime = Column(DateTime, index=True)

    total_usdt = Column(Float)
    n_priced = Column(Integer)
    n_unpriced = Column(Integer)
    holdings = Column(String)  # JSON {symbol: units}

    bench_hodl = Column(Float)    # the anchor basket, untouched, at today's prices
    bench_btc = Column(Float)     # the anchor value, had it all been BTC
    bench_bridge = Column(Float)  # the anchor value, had it all stayed in the bridge

    def __init__(
        self,
        total_usdt: float,
        holdings: dict,
        n_priced: int,
        n_unpriced: int,
        bench_hodl: float = None,
        bench_btc: float = None,
        bench_bridge: float = None,
        datetime: _datetime = None,
    ):
        self.datetime = datetime or _datetime.utcnow()
        self.total_usdt = total_usdt
        self.holdings = json.dumps(holdings, sort_keys=True)
        self.n_priced = n_priced
        self.n_unpriced = n_unpriced
        self.bench_hodl = bench_hodl
        self.bench_btc = bench_btc
        self.bench_bridge = bench_bridge

    def info(self):
        return {
            "datetime": self.datetime.isoformat(),
            "total_usdt": self.total_usdt,
            "n_priced": self.n_priced,
            "n_unpriced": self.n_unpriced,
            "holdings": json.loads(self.holdings or "{}"),
            "bench_hodl": self.bench_hodl,
            "bench_btc": self.bench_btc,
            "bench_bridge": self.bench_bridge,
        }


class RatchetEntry(Base):  # pylint: disable=too-few-public-methods
    """
    One row each time a coin is entered, recording units against the previous
    entry.

    This is the strategy's own thesis made measurable: it claims never to return
    to a coin holding fewer units than last time. ratio < 1 means that claim has
    failed for this coin, which no equity curve would tell you directly.
    """

    __tablename__ = "ratchet_history"

    id = Column(Integer, primary_key=True)
    datetime = Column(DateTime, index=True)

    coin_id = Column(String, ForeignKey("coins.symbol"))
    coin = relationship("Coin", lazy="joined")

    units = Column(Float)
    previous_units = Column(Float)
    ratio = Column(Float)  # units / previous_units; None on the first entry

    def __init__(self, coin: Coin, units: float, previous_units: float = None):
        self.datetime = _datetime.utcnow()
        self.coin = coin
        self.units = units
        self.previous_units = previous_units
        self.ratio = (units / previous_units) if previous_units else None

    def info(self):
        return {
            "datetime": self.datetime.isoformat(),
            "coin": self.coin.info() if self.coin else None,
            "units": self.units,
            "previous_units": self.previous_units,
            "ratio": self.ratio,
        }
