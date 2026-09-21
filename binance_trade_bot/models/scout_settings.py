"""
Site-wide scout threshold override, editable live from the dashboard.

Config.SCOUT_MARGIN/SCOUT_MULTIPLIER come from user.cfg and are fixed for
the life of the process. This is the live-editable counterpart: a single
row, read fresh on every scout pass, that overrides the file default until
cleared. Per-coin overrides (Coin.scout_margin_override) take priority over
this when both are set for the same pair.
"""
from sqlalchemy import Boolean, Column, Float, Integer

from .base import Base

SINGLETON_ID = 1


class ScoutSettings(Base):  # pylint: disable=too-few-public-methods
    __tablename__ = "scout_settings"

    id = Column(Integer, primary_key=True)
    margin_override = Column(Float)

    # Set to True after a take-profit exit so the bot knows it is parked in
    # the bridge currency on purpose and should not buy back in via
    # bridge_scout. Cleared automatically when a normal ratio-triggered jump
    # fires and the bot re-enters a coin.
    parked_in_bridge = Column(Boolean, default=False)

    # How often to send the email digest, in hours. 0 = disabled (even if
    # email_enabled=yes). Changed live from the dashboard without a restart.
    email_interval_hours = Column(Float, default=1.0)

    def __init__(self):
        self.id = SINGLETON_ID
        self.margin_override = None
        self.parked_in_bridge = False
        self.email_interval_hours = 1.0

    def info(self):
        return {
            "margin_override": self.margin_override,
            "parked_in_bridge": bool(self.parked_in_bridge),
            "email_interval_hours": self.email_interval_hours,
        }
