import enum
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base
from .coin import Coin


class TradeState(enum.Enum):
    STARTING = "STARTING"
    ORDERED = "ORDERED"
    COMPLETE = "COMPLETE"
    # An order that reached the exchange but ended without filling. Without this
    # a cancelled order's row stays ORDERED forever and reads as still live.
    CANCELED = "CANCELED"


class Trade(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "trade_history"

    id = Column(Integer, primary_key=True)

    alt_coin_id = Column(String, ForeignKey("coins.symbol"))
    alt_coin = relationship("Coin", foreign_keys=[alt_coin_id], lazy="joined")

    crypto_coin_id = Column(String, ForeignKey("coins.symbol"))
    crypto_coin = relationship("Coin", foreign_keys=[crypto_coin_id], lazy="joined")

    selling = Column(Boolean)

    # Binance's id for the order this row represents. Null for rows written
    # before the column existed, and for orders that never reached the exchange.
    order_id = Column(Integer, index=True)

    state = Column(Enum(TradeState))

    # The price the jump was decided at, and the price actually filled at.
    # Their difference is slippage, which for a passive limit order on a wide
    # book can be the whole edge.
    decision_price = Column(Float)
    fill_price = Column(Float)

    # ordered_ts comes from the exchange's own transactTime, so it is exact.
    # fill_ts is when we observed the fill, which the REST poller can only
    # resolve to its polling interval - treat it as accurate to ~1s, not better.
    ordered_ts = Column(DateTime)
    fill_ts = Column(DateTime)

    alt_starting_balance = Column(Float)
    alt_trade_amount = Column(Float)
    crypto_starting_balance = Column(Float)
    crypto_trade_amount = Column(Float)

    datetime = Column(DateTime)

    def __init__(self, alt_coin: Coin, crypto_coin: Coin, selling: bool):
        self.alt_coin = alt_coin
        self.crypto_coin = crypto_coin
        self.state = TradeState.STARTING
        self.selling = selling
        self.datetime = datetime.utcnow()

    def seconds_to_fill(self):
        """How long the order rested on the book before filling."""
        if self.ordered_ts is None or self.fill_ts is None:
            return None
        return (self.fill_ts - self.ordered_ts).total_seconds()

    def info(self):
        return {
            "id": self.id,
            "order_id": self.order_id,
            "alt_coin": self.alt_coin.info(),
            "crypto_coin": self.crypto_coin.info(),
            "selling": self.selling,
            "state": self.state.value,
            "alt_starting_balance": self.alt_starting_balance,
            "alt_trade_amount": self.alt_trade_amount,
            "crypto_starting_balance": self.crypto_starting_balance,
            "crypto_trade_amount": self.crypto_trade_amount,
            "decision_price": self.decision_price,
            "fill_price": self.fill_price,
            "ordered_ts": self.ordered_ts.isoformat() if self.ordered_ts else None,
            "fill_ts": self.fill_ts.isoformat() if self.fill_ts else None,
            "seconds_to_fill": self.seconds_to_fill(),
            "datetime": self.datetime.isoformat(),
        }
