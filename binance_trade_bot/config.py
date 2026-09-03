# Config consts
import configparser
import os

from .models import Coin

CFG_FL_NAME = "user.cfg"
USER_CFG_SECTION = "binance_user_config"


class Config:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    def __init__(self):
        # Init config
        config = configparser.ConfigParser()
        config["DEFAULT"] = {
            "bridge": "USDT",
            "use_margin": "no",
            "scout_multiplier": "5",
            "scout_margin": "0.8",
            "scout_sleep_time": "5",
            "hourToKeepScoutHistory": "1",
            "tld": "com",
            "strategy": "default",
            "sell_timeout": "0",
            "buy_timeout": "0",
            "cross_spread": "yes",
            "max_spread": "0.5",
            "stop_loss": "0",
            "take_profit": "0",
            "stop_cooldown": "60",
            "testnet": False,
        }

        if not os.path.exists(CFG_FL_NAME):
            print("No configuration file (user.cfg) found! See README. Assuming default config...")
            config[USER_CFG_SECTION] = {}
        else:
            config.read(CFG_FL_NAME)

        self.BRIDGE_SYMBOL = os.environ.get("BRIDGE_SYMBOL") or config.get(USER_CFG_SECTION, "bridge")
        self.BRIDGE = Coin(self.BRIDGE_SYMBOL, False)
        self.TESTNET = os.environ.get("TESTNET") or config.getboolean(USER_CFG_SECTION, "testnet")

        # Prune settings
        self.SCOUT_HISTORY_PRUNE_TIME = float(
            os.environ.get("HOURS_TO_KEEP_SCOUTING_HISTORY") or config.get(USER_CFG_SECTION, "hourToKeepScoutHistory")
        )

        # Get config for scout
        self.SCOUT_MULTIPLIER = float(
            os.environ.get("SCOUT_MULTIPLIER") or config.get(USER_CFG_SECTION, "scout_multiplier")
        )
        self.SCOUT_SLEEP_TIME = int(
            os.environ.get("SCOUT_SLEEP_TIME") or config.get(USER_CFG_SECTION, "scout_sleep_time")
        )

        # Get config for binance
        self.BINANCE_API_KEY = os.environ.get("API_KEY") or config.get(USER_CFG_SECTION, "api_key")
        self.BINANCE_API_SECRET_KEY = os.environ.get("API_SECRET_KEY") or config.get(USER_CFG_SECTION, "api_secret_key")
        self.BINANCE_TLD = os.environ.get("TLD") or config.get(USER_CFG_SECTION, "tld")

        # Get supported coin list from the environment
        supported_coin_list = [
            coin.strip() for coin in os.environ.get("SUPPORTED_COIN_LIST", "").split() if coin.strip()
        ]
        # Get supported coin list from supported_coin_list file
        if not supported_coin_list and os.path.exists("supported_coin_list"):
            with open("supported_coin_list") as rfh:
                for line in rfh:
                    line = line.strip()
                    if not line or line.startswith("#") or line in supported_coin_list:
                        continue
                    supported_coin_list.append(line)
        self.SUPPORTED_COIN_LIST = supported_coin_list

        self.CURRENT_COIN_SYMBOL = os.environ.get("CURRENT_COIN_SYMBOL") or config.get(USER_CFG_SECTION, "current_coin")

        self.STRATEGY = os.environ.get("STRATEGY") or config.get(USER_CFG_SECTION, "strategy")

        self.SELL_TIMEOUT = os.environ.get("SELL_TIMEOUT") or config.get(USER_CFG_SECTION, "sell_timeout")
        self.BUY_TIMEOUT = os.environ.get("BUY_TIMEOUT") or config.get(USER_CFG_SECTION, "buy_timeout")

        self.USE_MARGIN = os.environ.get("USE_MARGIN") or config.get(USER_CFG_SECTION, "use_margin")

        # Price orders at the far side of the book so they fill on placement
        # instead of resting on the bid and timing out.
        self.CROSS_SPREAD = (os.environ.get("CROSS_SPREAD") or config.get(USER_CFG_SECTION, "cross_spread")).lower()

        # Crossing costs the spread. Above this width the spread is a large
        # fraction of the jump threshold and the trade is not worth making;
        # keep it well under your threshold. 0 disables the check.
        self.MAX_SPREAD = float(os.environ.get("MAX_SPREAD") or config.get(USER_CFG_SECTION, "max_spread"))

        # Conventional risk levels, set when a position is opened and measured
        # against the entry price. Both 0 by default: a stop-loss sells at a
        # loss, which is exactly what the ratchet is built never to do, so it
        # has to be opted into deliberately.
        self.STOP_LOSS = float(os.environ.get("STOP_LOSS") or config.get(USER_CFG_SECTION, "stop_loss"))
        self.TAKE_PROFIT = float(os.environ.get("TAKE_PROFIT") or config.get(USER_CFG_SECTION, "take_profit"))

        # After stopping out, hold that coin out of consideration for a while.
        # Without it the very next scout can buy straight back into the coin it
        # just stopped out of, which makes the stop pointless.
        self.STOP_COOLDOWN = float(
            os.environ.get("STOP_COOLDOWN") or config.get(USER_CFG_SECTION, "stop_cooldown")
        )
        self.SCOUT_MARGIN = float(os.environ.get("SCOUT_MARGIN") or config.get(USER_CFG_SECTION, "scout_margin"))
