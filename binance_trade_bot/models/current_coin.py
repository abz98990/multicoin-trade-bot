from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base
from .coin import Coin


class CurrentCoin(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "current_coin_history"
    id = Column(Integer, primary_key=True)
    coin_id = Column(String, ForeignKey("coins.symbol"))
    coin = relationship("Coin")
    datetime = Column(DateTime)

    # Each row is the opening of a position, so the entry price and the risk
    # levels derived from it belong here. Null on positions opened before the
    # levels existed, or opened without a known price.
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)

    def __init__(self, coin: Coin, entry_price=None, stop_loss=None, take_profit=None):
        self.coin = coin
        self.datetime = datetime.utcnow()
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit

    def info(self):
        return {
            "datetime": self.datetime.isoformat(),
            "coin": self.coin.info(),
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
        }
