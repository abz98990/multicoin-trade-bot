import sys
import time
from datetime import datetime
from traceback import format_exc
from typing import Dict, List

from sqlalchemy.orm import Session

from .binance_api_manager import BinanceAPIManager
from .config import Config
from .database import Database
from .logger import Logger
from .models import Coin, CoinValue, EquitySnapshot, Pair, TradeState


class AutoTrader:
    def __init__(
        self,
        binance_manager: BinanceAPIManager,
        database: Database,
        logger: Logger,
        config: Config,
    ):
        self.manager = binance_manager
        self.db = database
        self.logger = logger
        self.config = config

    def initialize(self):
        self.initialize_trade_thresholds()

    def transaction_through_bridge(self, pair: Pair):
        """
        Jump from the source coin to the destination coin through bridge coin
        """
        can_sell = False
        balance = self.manager.get_currency_balance(pair.from_coin.symbol)
        from_coin_price = self.manager.get_ticker_price(pair.from_coin + self.config.BRIDGE)

        if balance and balance * from_coin_price > self.manager.get_min_notional(
            pair.from_coin.symbol, self.config.BRIDGE.symbol
        ):
            can_sell = True
        else:
            self.logger.info("Skipping sell")

        if can_sell and self.manager.sell_alt(pair.from_coin, self.config.BRIDGE) is None:
            self.logger.info("Couldn't sell, going back to scouting mode...")
            return None

        result = self.manager.buy_alt(pair.to_coin, self.config.BRIDGE)
        if result is not None:
            self.db.set_current_coin(pair.to_coin, result.price)
            self.update_trade_threshold(pair.to_coin, result.price)
            self.db.log_ratchet(pair.to_coin, self.manager.get_currency_balance(pair.to_coin.symbol, True))
            return result

        self.logger.info("Couldn't buy, going back to scouting mode...")
        return None

    def update_trade_threshold(self, coin: Coin, coin_price: float):
        """
        Update all the coins with the threshold of buying the current held coin
        """

        if coin_price is None:
            self.logger.info(f"Skipping update... current coin {coin + self.config.BRIDGE} not found")
            return

        session: Session
        with self.db.db_session() as session:
            for pair in session.query(Pair).filter(Pair.to_coin == coin):
                from_coin_price = self.manager.get_ticker_price(pair.from_coin + self.config.BRIDGE)

                if from_coin_price is None:
                    self.logger.info(f"Skipping update for coin {pair.from_coin + self.config.BRIDGE} not found")
                    continue

                pair.ratio = from_coin_price / coin_price

    def initialize_trade_thresholds(self):
        """
        Initialize the buying threshold of all the coins for trading between them
        """
        session: Session
        with self.db.db_session() as session:
            for pair in session.query(Pair).filter(Pair.ratio.is_(None)).all():
                if not pair.from_coin.enabled or not pair.to_coin.enabled:
                    continue
                self.logger.info(f"Initializing {pair.from_coin} vs {pair.to_coin}")

                from_coin_price = self.manager.get_ticker_price(pair.from_coin + self.config.BRIDGE)
                if from_coin_price is None:
                    self.logger.info(f"Skipping initializing {pair.from_coin + self.config.BRIDGE}, symbol not found")
                    continue

                to_coin_price = self.manager.get_ticker_price(pair.to_coin + self.config.BRIDGE)
                if to_coin_price is None:
                    self.logger.info(f"Skipping initializing {pair.to_coin + self.config.BRIDGE}, symbol not found")
                    continue

                pair.ratio = from_coin_price / to_coin_price

    def recover_open_orders(self):
        """
        Take ownership of anything left resting on the exchange at startup.

        A restart otherwise abandons an in-flight order: the bot forgets it,
        keeps the balance locked, and goes on to place further orders with
        whatever is left. Each order is either seen through to its fill or
        cancelled by its own timeout, and a completed buy is booked exactly as
        transaction_through_bridge would have booked it.
        """
        bridge = self.config.BRIDGE.symbol

        try:
            open_orders = self.manager.get_open_orders()
        except Exception:  # pylint: disable=broad-except
            self.logger.warning(f"Could not check for orders left open by a previous run:\n{format_exc()}")
            return

        if not open_orders:
            return

        known = {coin.symbol for coin in self.db.get_coins(False)}
        self.logger.info(f"Found {len(open_orders)} order(s) left open by a previous run")

        for order in open_orders:
            symbol = order["symbol"]
            order_id = order["orderId"]

            if not symbol.endswith(bridge):
                self.logger.info(f"Leaving {symbol} order {order_id} alone: not a {bridge} pair")
                continue

            origin_symbol = symbol[: -len(bridge)]
            if origin_symbol not in known:
                self.logger.info(f"Leaving {symbol} order {order_id} alone: {origin_symbol} is not a tracked coin")
                continue

            self.logger.info(f"Resuming {order['side']} order {order_id} on {symbol}")
            result = self.manager.resume_order(origin_symbol, bridge, order_id)

            if result is None:
                self.db.finish_trade(order_id, TradeState.CANCELED)
                self.logger.info(f"Order {order_id} was cancelled or timed out; its balance is free again")
                continue

            self.db.finish_trade(order_id, TradeState.COMPLETE, result.cumulative_quote_qty)

            if order["side"] == "BUY":
                coin = self.db.get_coin(origin_symbol)
                self.db.set_current_coin(coin, result.price)
                self.update_trade_threshold(coin, result.price)
                self.db.log_ratchet(coin, self.manager.get_currency_balance(origin_symbol, True))
                self.logger.info(f"Recovered an in-flight jump: now holding {origin_symbol}")
            else:
                # A sell is the first leg of a jump; the next scout picks the
                # destination from current ratios, so there is nothing to book.
                self.logger.info(f"Sell of {origin_symbol} completed; scouting will place the matching buy")

    def scout(self):
        """
        Scout for potential jumps from the current coin to another coin
        """
        raise NotImplementedError()

    def _get_ratios(self, coin: Coin, coin_price):
        """
        Given a coin, get the current price ratio for every other enabled coin
        """
        ratio_dict: Dict[Pair, float] = {}

        for pair in self.db.get_pairs_from(coin):
            optional_coin_price = self.manager.get_ticker_price(pair.to_coin + self.config.BRIDGE)

            if optional_coin_price is None:
                self.logger.info(f"Skipping scouting... optional coin {pair.to_coin + self.config.BRIDGE} not found")
                continue

            self.db.log_scout(pair, pair.ratio, coin_price, optional_coin_price)

            # Obtain (current coin)/(optional coin)
            coin_opt_coin_ratio = coin_price / optional_coin_price

            # Fees
            from_fee = self.manager.get_fee(pair.from_coin, self.config.BRIDGE, True)
            to_fee = self.manager.get_fee(pair.to_coin, self.config.BRIDGE, False)
            transaction_fee = from_fee + to_fee - from_fee * to_fee

            if self.config.USE_MARGIN == "yes":
                ratio_dict[pair] = (
                    (1 - transaction_fee) * coin_opt_coin_ratio / pair.ratio - 1 - self.config.SCOUT_MARGIN / 100
                )
            else:
                ratio_dict[pair] = (
                    coin_opt_coin_ratio - transaction_fee * self.config.SCOUT_MULTIPLIER * coin_opt_coin_ratio
                ) - pair.ratio
        return ratio_dict

    # When output is piped there is no cursor to repaint, so the line is only
    # emitted when it changes - plus this heartbeat, so silence never looks
    # like a hang.
    SCOUT_REPORT_HEARTBEAT = 60

    def report_scout(self, coin: Coin, considered: int, viable: dict):
        """
        One status line per scouting pass.

        The original said "Current coin: ICXUSDT", which is the held coin glued
        to the bridge symbol. It reads like a single trading pair and gives no
        hint that every candidate is checked on every pass, which is the most
        common misreading of this bot's output.
        """
        if viable:
            best = max(viable, key=viable.get)
            detail = f"{len(viable)} above threshold, best {best.to_coin_id}"
        else:
            detail = "none above threshold"
        line = f"scouting {considered} candidates from {coin.symbol} - {detail}"

        if sys.stdout is not None and sys.stdout.isatty():
            # A terminal: repaint in place, as the original did.
            print(f"{datetime.now()} - CONSOLE - INFO - {line} ", end="\r")
            return

        now = time.time()
        unchanged = line == getattr(self, "_last_scout_line", None)
        if unchanged and now - getattr(self, "_last_scout_report", 0) < self.SCOUT_REPORT_HEARTBEAT:
            return
        self._last_scout_line = line
        self._last_scout_report = now
        print(f"{datetime.now()} - CONSOLE - INFO - {line}", flush=True)

    def _jump_to_best_coin(self, coin: Coin, coin_price: float):
        """
        Given a coin, search for a coin to jump to
        """
        all_ratios = self._get_ratios(coin, coin_price)

        # keep only ratios bigger than zero
        ratio_dict = {k: v for k, v in all_ratios.items() if v > 0}
        self.report_scout(coin, len(all_ratios), ratio_dict)

        # Best first, but skip any pair whose book is too wide to trade through:
        # a signal that clears the threshold by a hair is not worth paying a
        # comparable spread for. Falling through to the next candidate beats
        # standing still, which is what the old single-choice version did.
        for pair in sorted(ratio_dict, key=ratio_dict.get, reverse=True):
            if self.db.is_cooling_down(pair.to_coin_id):
                continue
            if not self.manager.spread_is_acceptable(pair.to_coin.symbol, self.config.BRIDGE.symbol):
                continue
            self.logger.info(f"Will be jumping from {coin} to {pair.to_coin_id}")
            self.transaction_through_bridge(pair)
            return

    def bridge_scout(self):
        """
        If we have any bridge coin leftover, buy a coin with it that we won't immediately trade out of
        """
        bridge_balance = self.manager.get_currency_balance(self.config.BRIDGE.symbol)

        for coin in self.db.get_coins():
            current_coin_price = self.manager.get_ticker_price(coin + self.config.BRIDGE)

            if current_coin_price is None:
                continue

            ratio_dict = self._get_ratios(coin, current_coin_price)
            if not any(v > 0 for v in ratio_dict.values()):
                # There will only be one coin where all the ratios are negative. When we find it, buy it if we can
                if bridge_balance > self.manager.get_min_notional(coin.symbol, self.config.BRIDGE.symbol):
                    self.logger.info(f"Will be purchasing {coin} using bridge coin")
                    self.manager.buy_alt(coin, self.config.BRIDGE)
                    return coin
        return None

    def check_risk_levels(self):
        """
        Close the open position if it has hit its stop or its target.

        This is the one place the bot will sell without buying something else,
        so it is also the one place the ratchet's "never take a loss" invariant
        is deliberately broken. Off unless stop_loss or take_profit is set.
        """
        if not (self.config.STOP_LOSS or self.config.TAKE_PROFIT):
            return

        position = self.db.get_current_position()
        if not position:
            return

        stop, target = position.get("stop_loss"), position.get("take_profit")
        if not (stop or target):
            return

        bridge = self.config.BRIDGE.symbol
        symbol = position["coin"]["symbol"]

        price = self.manager.get_ticker_price(symbol + bridge)
        if price is None:
            return

        # Only act on a position we actually hold; after a close the current
        # coin still names the coin we just left.
        balance = self.manager.get_currency_balance(symbol)
        if not balance or balance * price <= self.manager.get_min_notional(symbol, bridge):
            return

        if stop and price <= stop:
            reason, level = "stop-loss", stop
        elif target and price >= target:
            reason, level = "take-profit", target
        else:
            return

        entry = position.get("entry_price")
        move = f" ({(price / entry - 1) * 100:+.2f}% from entry)" if entry else ""
        self.logger.warning(
            f"{reason} hit on {symbol}: {price:.8g} vs {level:.8g}{move} - closing to {bridge}"
        )

        coin = self.db.get_coin(symbol)
        if self.manager.sell_alt(coin, self.config.BRIDGE) is None:
            self.logger.warning(f"Couldn't close {symbol}; will try again on the next check")
            return

        if reason == "stop-loss":
            # Otherwise the next scout can buy straight back in, and the stop
            # achieved nothing but a round trip in fees.
            self.db.start_cooldown(symbol, self.config.STOP_COOLDOWN)

        self.logger.info(f"{symbol} closed to {bridge}; scouting will choose the next position")

    def _portfolio(self):
        """Everything held right now, valued in the bridge currency."""
        bridge = self.config.BRIDGE.symbol
        holdings, total, priced, unpriced = {}, 0.0, 0, 0

        symbols = [coin.symbol for coin in self.db.get_coins(False)]
        if bridge not in symbols:
            symbols.append(bridge)

        for symbol in symbols:
            balance = self.manager.get_currency_balance(symbol)
            if not balance:
                continue
            holdings[symbol] = balance

            if symbol == bridge:
                # The bridge is the unit of account; there is no <bridge><bridge>
                # ticker to look up, which is what used to drop it from totals.
                total += balance
                priced += 1
                continue

            price = self.manager.get_ticker_price(symbol + bridge)
            if price is None:
                unpriced += 1
                continue
            total += balance * price
            priced += 1

        return holdings, total, priced, unpriced

    def _benchmarks(self, anchor):
        """Value the untouched anchor basket, and the same money held as BTC."""
        bridge = self.config.BRIDGE.symbol

        hodl = 0.0
        for symbol, units in anchor.basket().items():
            if symbol == bridge:
                hodl += units
                continue
            price = self.manager.get_ticker_price(symbol + bridge)
            if price is not None:
                hodl += units * price

        btc_now = self.manager.get_ticker_price("BTC" + bridge)
        bench_btc = None
        if anchor.btc_price and btc_now:
            bench_btc = anchor.total_usdt / anchor.btc_price * btc_now

        return hodl, bench_btc, anchor.total_usdt

    def log_performance(self):
        """
        Sample the equity curve and its benchmarks.

        Portfolio value on its own says nothing in a market that moves - the
        question is whether trading beat leaving the same holdings alone, so
        both are recorded from the same prices at the same instant.
        """
        holdings, total, priced, unpriced = self._portfolio()

        anchor = self.db.get_benchmark_anchor()
        if anchor is None:
            if not holdings:
                return  # nothing to anchor to yet
            btc_price = self.manager.get_ticker_price("BTC" + self.config.BRIDGE.symbol)
            anchor = self.db.set_benchmark_anchor(holdings, total, btc_price)

        bench_hodl, bench_btc, bench_bridge = self._benchmarks(anchor)

        self.db.log_equity(
            EquitySnapshot(total, holdings, priced, unpriced, bench_hodl, bench_btc, bench_bridge)
        )

    def update_values(self):
        """
        Log current value state of all altcoin balances against BTC and USDT in DB.
        """
        now = datetime.now()

        session: Session
        with self.db.db_session() as session:
            coins: List[Coin] = session.query(Coin).all()
            for coin in coins:
                balance = self.manager.get_currency_balance(coin.symbol)
                if balance == 0:
                    continue
                usd_value = self.manager.get_ticker_price(coin + "USDT")
                btc_value = self.manager.get_ticker_price(coin + "BTC")
                cv = CoinValue(coin, balance, usd_value, btc_value, datetime=now)
                session.add(cv)
                self.db.send_update(cv)
