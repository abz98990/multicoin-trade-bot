"""
Site-wide scout threshold override, editable live from the dashboard.

Config.SCOUT_MARGIN/SCOUT_MULTIPLIER come from user.cfg and are fixed for
the life of the process. This is the live-editable counterpart: a single
row, read fresh on every scout pass, that overrides the file default until
cleared. Per-coin overrides (Coin.scout_margin_override) take priority over
this when both are set for the same pair.
"""
from sqlalchemy import Column, Float, Integer

from .base import Base

SINGLETON_ID = 1


class ScoutSettings(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "scout_settings"

    id = Column(Integer, primary_key=True)
    margin_override = Column(Float)

    def __init__(self):
        self.id = SINGLETON_ID
        self.margin_override = None

    def info(self):
        return {"margin_override": self.margin_override}
