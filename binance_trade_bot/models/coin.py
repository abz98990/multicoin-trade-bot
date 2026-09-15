from sqlalchemy import Boolean, Column, DateTime, Float, String

from .base import Base


class Coin(Base):
    __tablename__ = "coins"
    symbol = Column(String, primary_key=True)
    enabled = Column(Boolean)

    # Set when a stop-loss fires, to keep the bot from buying straight back in.
    cooldown_until = Column(DateTime)

    # Overrides the global scout margin/multiplier when scouting FROM this
    # coin specifically - a standing trait of the coin ("PEPE always needs a
    # bigger edge"), not tied to any one holding period the way stop_loss/
    # take_profit are. None means "use the global setting".
    scout_margin_override = Column(Float)

    def __init__(self, symbol, enabled=True):
        self.symbol = symbol
        self.enabled = enabled

    def __add__(self, other):
        if isinstance(other, str):
            return self.symbol + other
        if isinstance(other, Coin):
            return self.symbol + other.symbol
        raise TypeError(f"unsupported operand type(s) for +: 'Coin' and '{type(other)}'")

    def __repr__(self):
        return f"[{self.symbol}]"

    def info(self):
        return {
            "symbol": self.symbol,
            "enabled": self.enabled,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "scout_margin_override": self.scout_margin_override,
        }
