"""A human-readable record of what the bot decided and why.

Distinct from the file log (binance_trade_bot/logger.py): that one is for an
operator tailing a terminal. This one is for the dashboard, so it only holds
decisions and changes - jumps, risk-level edits, stop/target hits, manual
trades - not every price tick. A tick is not a decision.
"""
from datetime import datetime as _datetime

from sqlalchemy import Column, DateTime, Integer, String

from .base import Base


class ActivityLog(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True)
    datetime = Column(DateTime, index=True)
    category = Column(String)  # "jump", "risk", "position", "trade"
    message = Column(String)

    def __init__(self, category: str, message: str):
        self.datetime = _datetime.utcnow()
        self.category = category
        self.message = message

    def info(self):
        return {
            "datetime": self.datetime.isoformat(),
            "category": self.category,
            "message": self.message,
        }
