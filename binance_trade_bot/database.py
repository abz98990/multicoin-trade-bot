import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Optional, Union

from socketio import Client
from socketio.exceptions import ConnectionError as SocketIOConnectionError
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from .config import Config
from .logger import Logger
from .models import *  # pylint: disable=wildcard-import


class Database:
    def __init__(self, logger: Logger, config: Config, uri="sqlite:///data/crypto_trading.db"):
        self.logger = logger
        self.config = config
        self.engine = create_engine(uri)
        self.SessionMaker = sessionmaker(bind=self.engine)
        self.socketio_client = Client()

    def socketio_connect(self):
        if self.socketio_client.connected and self.socketio_client.namespaces:
            return True
        try:
            if not self.socketio_client.connected:
                self.socketio_client.connect("http://api:5123", namespaces=["/backend"])
            while not self.socketio_client.connected or not self.socketio_client.namespaces:
                time.sleep(0.1)
            return True
        except SocketIOConnectionError:
            return False

    @contextmanager
    def db_session(self):
        """
        Creates a context with an open SQLAlchemy session.
        """
        session: Session = scoped_session(self.SessionMaker)
        yield session
        session.commit()
        session.close()

    def set_coins(self, symbols: List[str]):
        session: Session

        # Add coins to the database and set them as enabled or not
        with self.db_session() as session:
            # For all the coins in the database, if the symbol no longer appears
            # in the config file, set the coin as disabled
            coins: List[Coin] = session.query(Coin).all()
            for coin in coins:
                if coin.symbol not in symbols:
                    coin.enabled = False

            # For all the symbols in the config file, add them to the database
            # if they don't exist
            for symbol in symbols:
                coin = next((coin for coin in coins if coin.symbol == symbol), None)
                if coin is None:
                    session.add(Coin(symbol))
                else:
                    coin.enabled = True

        # For all the combinations of coins in the database, add a pair to the database
        with self.db_session() as session:
            coins: List[Coin] = session.query(Coin).filter(Coin.enabled).all()
            for from_coin in coins:
                for to_coin in coins:
                    if from_coin != to_coin:
                        pair = session.query(Pair).filter(Pair.from_coin == from_coin, Pair.to_coin == to_coin).first()
                        if pair is None:
                            session.add(Pair(from_coin, to_coin))

    def get_coins(self, only_enabled=True) -> List[Coin]:
        session: Session
        with self.db_session() as session:
            if only_enabled:
                coins = session.query(Coin).filter(Coin.enabled).all()
            else:
                coins = session.query(Coin).all()
            session.expunge_all()
            return coins

    def get_scout_margin_override(self) -> Optional[float]:
        """The site-wide override, or None if it's never been set/was cleared."""
        session: Session
        with self.db_session() as session:
            row = session.query(ScoutSettings).get(1)
            return row.margin_override if row else None

    def set_scout_margin_override(self, value: Optional[float]):
        """value=None clears the override, falling back to user.cfg's setting."""
        session: Session
        with self.db_session() as session:
            row = session.query(ScoutSettings).get(1)
            if row is None:
                row = ScoutSettings()
                session.add(row)
            row.margin_override = value
        self.log_event(
            "risk",
            f"Global jump threshold override set to {'none' if value is None else f'{value:g}%'}",
        )

    def get_coin_scout_margin_override(self, symbol: str) -> Optional[float]:
        """The per-coin override for scouting FROM this coin, or None."""
        coin = self.get_coin(symbol)
        return coin.scout_margin_override if coin else None

    def set_coin_scout_margin_override(self, symbol: str, value: Optional[float]):
        """value=None clears it, falling back to the global/file setting."""
        session: Session
        with self.db_session() as session:
            coin: Coin = session.query(Coin).get(symbol)
            if coin is None:
                return None
            coin.scout_margin_override = value
        self.log_event(
            "risk",
            f"{symbol} jump threshold override set to {'none' if value is None else f'{value:g}%'}",
        )
        return self.get_coin(symbol).info()

    def get_parked_in_bridge(self) -> bool:
        """True when the bot has intentionally exited to the bridge after a take-profit."""
        session: Session
        with self.db_session() as session:
            row = session.query(ScoutSettings).get(1)
            return bool(row.parked_in_bridge) if row else False

    def set_parked_in_bridge(self, value: bool):
        """Park (True) or un-park (False) the bot from the bridge currency."""
        session: Session
        with self.db_session() as session:
            row = session.query(ScoutSettings).get(1)
            if row is None:
                row = ScoutSettings()
                session.add(row)
            row.parked_in_bridge = value

    def get_email_interval_hours(self) -> float:
        """
        The live email digest interval in hours.

        Reads the DB value first. Falls back to 1.0 (the column default) if no
        ScoutSettings row exists yet. EmailNotifier._seed_interval() writes the
        real user.cfg value on first startup, so this fallback is only hit during
        the brief window before the bot finishes initialising.
        """
        session: Session
        with self.db_session() as session:
            row = session.query(ScoutSettings).get(1)
            if row is not None and row.email_interval_hours is not None:
                return float(row.email_interval_hours)
        return 1.0

    def set_email_interval_hours(self, hours: float):
        """Update the email digest interval live. 0 pauses sending."""
        session: Session
        with self.db_session() as session:
            row = session.query(ScoutSettings).get(1)
            if row is None:
                row = ScoutSettings()
                session.add(row)
            row.email_interval_hours = hours

    def get_coin(self, coin: Union[Coin, str]) -> Coin:
        if isinstance(coin, Coin):
            return coin
        session: Session
        with self.db_session() as session:
            coin = session.query(Coin).get(coin)
            session.expunge(coin)
            return coin

    def risk_levels(self, entry_price):
        """Stop-loss and take-profit for a position opened at this price."""
        if not entry_price:
            return None, None
        stop = entry_price * (1 - self.config.STOP_LOSS / 100) if self.config.STOP_LOSS else None
        target = entry_price * (1 + self.config.TAKE_PROFIT / 100) if self.config.TAKE_PROFIT else None
        return stop, target

    def set_current_coin(
        self,
        coin: Union[Coin, str],
        entry_price: float = None,
        stop_loss: float = None,
        take_profit: float = None,
    ):
        coin = self.get_coin(coin)
        # Read the symbol now: merging rebinds `coin` to an instance that is
        # detached and expired once the session closes, so touching it after
        # the block raises DetachedInstanceError.
        symbol = coin.symbol if isinstance(coin, Coin) else str(coin)
        # A manual entry can name its own levels; otherwise they come from the
        # configured percentages.
        if stop_loss is None and take_profit is None:
            stop_loss, take_profit = self.risk_levels(entry_price)

        session: Session
        with self.db_session() as session:
            merged = session.merge(coin) if isinstance(coin, Coin) else coin
            cc = CurrentCoin(merged, entry_price, stop_loss, take_profit)
            session.add(cc)
            self.send_update(cc)

        if entry_price and (stop_loss or take_profit):
            levels = []
            if stop_loss:
                levels.append(f"stop {stop_loss:.8g}")
            if take_profit:
                levels.append(f"target {take_profit:.8g}")
            self.logger.info(f"Opened {symbol} at {entry_price:.8g} ({', '.join(levels)})")

    def get_current_position(self):
        """The open position with its risk levels, or None."""
        session: Session
        with self.db_session() as session:
            position = session.query(CurrentCoin).order_by(CurrentCoin.datetime.desc()).first()
            if position is None:
                return None
            info = position.info()
            return info

    def get_open_positions(self):
        """
        The most recent current_coin_history row for every coin that has one,
        keyed by symbol.

        get_current_position() answers "what is the bot's own rotation seat
        right now", which is one coin. This answers "what risk levels, if any,
        does each coin I have ever held carry" - the dashboard cross-references
        it against live balances to show every coin actually worth protecting,
        not just the bot's single seat. A coin sold back to zero and later
        re-bought still resolves to its latest row, so editing survives a round
        trip through the bridge.
        """
        session: Session
        with self.db_session() as session:
            rows = session.query(CurrentCoin).order_by(CurrentCoin.datetime.asc()).all()
            latest = {}
            for row in rows:
                latest[row.coin_id] = row.info()  # later rows overwrite earlier ones
            return latest

    def set_position_risk(
        self,
        coin: Union[Coin, str],
        stop_loss: float = None,
        take_profit: float = None,
        fallback_entry_price: float = None,
    ):
        """
        Create or update a coin's stop-loss/take-profit in place.

        This is the "edit on the go" path: unlike set_current_coin, it does not
        insert a new current_coin_history row when one already exists for this
        coin - it mutates the existing row's levels, so "opened X ago" keeps
        reading from the real entry instead of resetting to "just now" every
        time someone adjusts a percentage. If the coin has never had a row at
        all (e.g. the bot acquired it before this feature existed), one is
        created using fallback_entry_price - usually the live price - as an
        informational entry, since the true cost basis is not recoverable.

        Levels are passed as absolute prices, already computed by the caller
        from whatever percentage the user typed and whatever entry price is on
        record - this method only ever needs to know where to write them.
        """
        coin = self.get_coin(coin)
        symbol = coin.symbol if isinstance(coin, Coin) else str(coin)

        session: Session
        with self.db_session() as session:
            row: CurrentCoin = (
                session.query(CurrentCoin)
                .filter(CurrentCoin.coin_id == symbol)
                .order_by(CurrentCoin.datetime.desc())
                .first()
            )
            if row is None:
                if fallback_entry_price is None:
                    return None
                merged = session.merge(coin) if isinstance(coin, Coin) else coin
                row = CurrentCoin(merged, fallback_entry_price, stop_loss, take_profit)
                session.add(row)
                old_stop, old_target = None, None
            else:
                old_stop, old_target = row.stop_loss, row.take_profit
                row.stop_loss = stop_loss
                row.take_profit = take_profit
            session.flush()
            self.send_update(row)
            info = row.info()

        def fmt(value):
            return "none" if value is None else f"{value:.8g}"

        self.log_event(
            "risk",
            f"{symbol} risk levels changed: stop {fmt(old_stop)} → {fmt(stop_loss)}, "
            f"target {fmt(old_target)} → {fmt(take_profit)}",
        )
        return info

    def log_event(self, category: str, message: str):
        """
        Record a decision or change for the dashboard's activity log.

        For decisions and changes only - jumps, risk edits, stop/target hits,
        manual trades. Not every scout tick; a price check that decides nothing
        is not an event, and logging one per second would drown the entries
        that actually matter within minutes.
        """
        session: Session
        with self.db_session() as session:
            entry = ActivityLog(category, message)
            session.add(entry)
            session.flush()
            self.send_update(entry)

    def start_cooldown(self, coin: Union[Coin, str], minutes: float):
        """Keep a coin out of consideration after it stopped out."""
        if not minutes:
            return
        symbol = coin.symbol if isinstance(coin, Coin) else coin
        until = datetime.utcnow() + timedelta(minutes=minutes)
        session: Session
        with self.db_session() as session:
            row: Coin = session.query(Coin).get(symbol)
            if row is not None:
                row.cooldown_until = until
        self.logger.info(f"{symbol} on cooldown until {until.isoformat(timespec='seconds')} UTC")

    def is_cooling_down(self, coin: Union[Coin, str]) -> bool:
        symbol = coin.symbol if isinstance(coin, Coin) else coin
        session: Session
        with self.db_session() as session:
            row: Coin = session.query(Coin).get(symbol)
            if row is None or row.cooldown_until is None:
                return False
            return row.cooldown_until > datetime.utcnow()

    def get_current_coin(self) -> Optional[Coin]:
        session: Session
        with self.db_session() as session:
            current_coin = session.query(CurrentCoin).order_by(CurrentCoin.datetime.desc()).first()
            if current_coin is None:
                return None
            coin = current_coin.coin
            session.expunge(coin)
            return coin

    def get_pair(self, from_coin: Union[Coin, str], to_coin: Union[Coin, str]):
        from_coin = self.get_coin(from_coin)
        to_coin = self.get_coin(to_coin)
        session: Session
        with self.db_session() as session:
            pair: Pair = session.query(Pair).filter(Pair.from_coin == from_coin, Pair.to_coin == to_coin).first()
            session.expunge(pair)
            return pair

    def get_pairs_from(self, from_coin: Union[Coin, str], only_enabled=True) -> List[Pair]:
        from_coin = self.get_coin(from_coin)
        session: Session
        with self.db_session() as session:
            pairs = session.query(Pair).filter(Pair.from_coin == from_coin)
            if only_enabled:
                pairs = pairs.filter(Pair.enabled.is_(True))
            pairs = pairs.all()
            session.expunge_all()
            return pairs

    def get_pairs(self, only_enabled=True) -> List[Pair]:
        session: Session
        with self.db_session() as session:
            pairs = session.query(Pair)
            if only_enabled:
                pairs = pairs.filter(Pair.enabled.is_(True))
            pairs = pairs.all()
            session.expunge_all()
            return pairs

    def log_scout(
        self,
        pair: Pair,
        target_ratio: float,
        current_coin_price: float,
        other_coin_price: float,
    ):
        session: Session
        with self.db_session() as session:
            pair = session.merge(pair)
            sh = ScoutHistory(pair, target_ratio, current_coin_price, other_coin_price)
            session.add(sh)
            self.send_update(sh)

    def prune_scout_history(self):
        # ScoutHistory.datetime is written with utcnow(), so the cutoff has to be
        # UTC as well. Using local now() any distance east of UTC puts the cutoff
        # ahead of every stored row and wipes the whole table on each run.
        time_diff = datetime.utcnow() - timedelta(hours=self.config.SCOUT_HISTORY_PRUNE_TIME)
        session: Session
        with self.db_session() as session:
            session.query(ScoutHistory).filter(ScoutHistory.datetime < time_diff).delete()

    def prune_value_history(self):
        session: Session
        with self.db_session() as session:
            # Sets the first entry for each coin for each hour as 'hourly'
            hourly_entries: List[CoinValue] = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.strftime("%H", CoinValue.datetime)).all()
            )
            for entry in hourly_entries:
                entry.interval = Interval.HOURLY

            # Sets the first entry for each coin for each day as 'daily'
            daily_entries: List[CoinValue] = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.date(CoinValue.datetime)).all()
            )
            for entry in daily_entries:
                entry.interval = Interval.DAILY

            # Sets the first entry for each coin for each month as 'weekly'
            # (Sunday is the start of the week)
            weekly_entries: List[CoinValue] = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.strftime("%Y-%W", CoinValue.datetime)).all()
            )
            for entry in weekly_entries:
                entry.interval = Interval.WEEKLY

            # The last 24 hours worth of minutely entries will be kept, so
            # count(coins) * 1440 entries
            time_diff = datetime.now() - timedelta(hours=24)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.MINUTELY, CoinValue.datetime < time_diff
            ).delete()

            # The last 28 days worth of hourly entries will be kept, so count(coins) * 672 entries
            time_diff = datetime.now() - timedelta(days=28)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.HOURLY, CoinValue.datetime < time_diff
            ).delete()

            # The last years worth of daily entries will be kept, so count(coins) * 365 entries
            time_diff = datetime.now() - timedelta(days=365)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.DAILY, CoinValue.datetime < time_diff
            ).delete()

            # All weekly entries will be kept forever

    # (table, column, SQL type) for columns added after this schema first shipped.
    # create_all() only creates missing tables; it never alters an existing one,
    # so anyone with an older database file needs the column added explicitly.
    SCHEMA_MIGRATIONS = (
        ("trade_history", "order_id", "INTEGER"),
        ("trade_history", "decision_price", "FLOAT"),
        ("trade_history", "fill_price", "FLOAT"),
        ("trade_history", "ordered_ts", "DATETIME"),
        ("trade_history", "fill_ts", "DATETIME"),
        ("current_coin_history", "entry_price", "FLOAT"),
        ("current_coin_history", "stop_loss", "FLOAT"),
        ("current_coin_history", "take_profit", "FLOAT"),
        ("coins", "cooldown_until", "DATETIME"),
        ("coins", "scout_margin_override", "FLOAT"),
        ("scout_settings", "parked_in_bridge", "BOOLEAN"),
        ("scout_settings", "email_interval_hours", "FLOAT"),
    )

    def create_database(self):
        Base.metadata.create_all(self.engine)
        self.apply_schema_migrations()

    def apply_schema_migrations(self):
        """Add any missing columns to an existing database. Safe to re-run."""
        with self.engine.begin() as connection:
            for table, column, column_type in self.SCHEMA_MIGRATIONS:
                rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
                if not rows:
                    continue  # table does not exist yet; create_all will make it
                if column in {row[1] for row in rows}:
                    continue
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                self.logger.info(f"Database migration: added {table}.{column}")

    def finish_trade(self, order_id: int, state: TradeState, crypto_trade_amount: float = None):
        """
        Close out the trade row for an order settled outside its original flow.

        Used by restart recovery: the TradeLog that opened the row belonged to a
        process that is gone, so the row is found by order id instead.
        """
        if order_id is None:
            return False
        session: Session
        with self.db_session() as session:
            trade: Trade = session.query(Trade).filter(Trade.order_id == order_id).first()
            if trade is None:
                return False
            if crypto_trade_amount is not None:
                trade.crypto_trade_amount = crypto_trade_amount
            if state == TradeState.COMPLETE:
                trade.fill_ts = datetime.utcnow()
            trade.state = state
            self.send_update(trade)
            return True

    # A symbol missing today can be listed later, so forget what we learned
    # after a while rather than staying blind to it permanently.
    UNKNOWN_TICKER_TTL_DAYS = 7

    def get_unknown_tickers(self) -> set:
        """Symbols known to be absent, still within their re-check window."""
        cutoff = datetime.utcnow() - timedelta(days=self.UNKNOWN_TICKER_TTL_DAYS)
        session: Session
        with self.db_session() as session:
            rows = session.query(UnknownTicker).filter(UnknownTicker.datetime >= cutoff).all()
            return {row.symbol for row in rows}

    def add_unknown_ticker(self, symbol: str):
        session: Session
        with self.db_session() as session:
            row: UnknownTicker = session.query(UnknownTicker).get(symbol)
            if row is None:
                session.add(UnknownTicker(symbol))
            else:
                row.datetime = datetime.utcnow()

    def coin_round_trips(self):
        """
        Pair each completed buy of a coin with the sell that closed it.

        Everything needed is already in trade_history - this walks it in order
        per coin rather than storing a second copy that could drift out of
        step with the trades it describes. An unmatched buy is an open
        position and is returned with no exit.
        """
        session: Session
        with self.db_session() as session:
            trades = (
                session.query(Trade)
                .filter(Trade.state == TradeState.COMPLETE)
                .order_by(Trade.datetime.asc())
                .all()
            )
            rows = [t.info() for t in trades]

        open_leg = {}
        trips = []
        for trade in rows:
            symbol = trade["alt_coin"]["symbol"]
            if not trade["selling"]:
                # A second buy without an intervening sell just adds to the
                # position; keep the first as the entry.
                open_leg.setdefault(symbol, trade)
                continue

            entry = open_leg.pop(symbol, None)
            trips.append(self._round_trip(symbol, entry, trade))

        for symbol, entry in open_leg.items():
            trips.append(self._round_trip(symbol, entry, None))

        trips.sort(key=lambda t: t["opened"] or "", reverse=True)
        return trips

    @staticmethod
    def _round_trip(symbol, entry, exit_trade):
        def price(trade):
            if trade is None:
                return None
            if trade.get("fill_price"):
                return trade["fill_price"]
            amount = trade.get("alt_trade_amount") or 0
            spent = trade.get("crypto_trade_amount") or 0
            return (spent / amount) if amount else None

        entry_price, exit_price = price(entry), price(exit_trade)
        units_in = (entry or {}).get("alt_trade_amount")
        units_out = (exit_trade or {}).get("alt_trade_amount")

        gain = None
        if entry_price and exit_price:
            gain = (exit_price / entry_price - 1) * 100

        held = None
        if entry and exit_trade:
            held = (
                datetime.fromisoformat(exit_trade["datetime"])
                - datetime.fromisoformat(entry["datetime"])
            ).total_seconds()

        return {
            "coin": symbol,
            "opened": (entry or {}).get("datetime"),
            "closed": (exit_trade or {}).get("datetime"),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "units_in": units_in,
            "units_out": units_out,
            "spent": (entry or {}).get("crypto_trade_amount"),
            "received": (exit_trade or {}).get("crypto_trade_amount"),
            "gain_percent": gain,
            "held_seconds": held,
            "open": exit_trade is None,
        }

    def start_trade_log(self, from_coin: Coin, to_coin: Coin, selling: bool):
        return TradeLog(self, from_coin, to_coin, selling)

    # ----------------------------------------------------------- performance

    def get_benchmark_anchor(self) -> Optional[BenchmarkAnchor]:
        session: Session
        with self.db_session() as session:
            anchor = session.query(BenchmarkAnchor).order_by(BenchmarkAnchor.id.asc()).first()
            if anchor is not None:
                session.expunge(anchor)
            return anchor

    def set_benchmark_anchor(self, holdings: dict, total_usdt: float, btc_price: float):
        """
        Fix the point every benchmark is measured from. Only ever set once
        unless deliberately reset, or the comparison moves with the portfolio
        and always flatters it.
        """
        session: Session
        with self.db_session() as session:
            anchor = BenchmarkAnchor(holdings, total_usdt, btc_price)
            session.add(anchor)
            session.flush()
            session.expunge(anchor)
            self.logger.info(
                f"Benchmark anchored at {total_usdt:.2f} {self.config.BRIDGE.symbol} "
                f"across {len(holdings)} asset(s)"
            )
            return anchor

    def clear_benchmark_anchor(self):
        session: Session
        with self.db_session() as session:
            return session.query(BenchmarkAnchor).delete()

    def log_equity(self, snapshot: EquitySnapshot):
        session: Session
        with self.db_session() as session:
            session.add(snapshot)
            self.send_update(snapshot)

    def last_ratchet_units(self, coin: Union[Coin, str]) -> Optional[float]:
        """Units held the last time this coin was entered, if ever."""
        symbol = coin.symbol if isinstance(coin, Coin) else coin
        session: Session
        with self.db_session() as session:
            entry = (
                session.query(RatchetEntry)
                .filter(RatchetEntry.coin_id == symbol)
                .order_by(RatchetEntry.datetime.desc())
                .first()
            )
            return entry.units if entry else None

    def log_ratchet(self, coin: Coin, units: float):
        """Record an entry into a coin against the previous one."""
        previous = self.last_ratchet_units(coin)
        session: Session
        with self.db_session() as session:
            entry = RatchetEntry(session.merge(coin), units, previous)
            session.add(entry)
            self.send_update(entry)
            if entry.ratio is not None:
                verdict = "up" if entry.ratio >= 1 else "DOWN"
                self.logger.info(
                    f"Ratchet {coin.symbol}: {units:.8g} units vs {previous:.8g} last time "
                    f"({entry.ratio:.4f}x, {verdict})"
                )

    def send_update(self, model):
        if not self.socketio_connect():
            return

        self.socketio_client.emit(
            "update",
            {"table": model.__tablename__, "data": model.info()},
            namespace="/backend",
        )

    def migrate_old_state(self):
        """
        For migrating from old dotfile format to SQL db. This method should be removed in
        the future.
        """
        if os.path.isfile(".current_coin"):
            with open(".current_coin") as f:
                coin = f.read().strip()
                self.logger.info(f".current_coin file found, loading current coin {coin}")
                self.set_current_coin(coin)
            os.rename(".current_coin", ".current_coin.old")
            self.logger.info(f".current_coin renamed to .current_coin.old - You can now delete this file")

        if os.path.isfile(".current_coin_table"):
            with open(".current_coin_table") as f:
                self.logger.info(f".current_coin_table file found, loading into database")
                table: dict = json.load(f)
                session: Session
                with self.db_session() as session:
                    for from_coin, to_coin_dict in table.items():
                        for to_coin, ratio in to_coin_dict.items():
                            if from_coin == to_coin:
                                continue
                            pair = session.merge(self.get_pair(from_coin, to_coin))
                            pair.ratio = ratio
                            session.add(pair)

            os.rename(".current_coin_table", ".current_coin_table.old")
            self.logger.info(".current_coin_table renamed to .current_coin_table.old - " "You can now delete this file")


class TradeLog:
    def __init__(self, db: Database, from_coin: Coin, to_coin: Coin, selling: bool):
        self.db = db
        session: Session
        with self.db.db_session() as session:
            from_coin = session.merge(from_coin)
            to_coin = session.merge(to_coin)
            self.trade = Trade(from_coin, to_coin, selling)
            session.add(self.trade)
            # Flush so that SQLAlchemy fills in the id column
            session.flush()
            self.db.send_update(self.trade)

    def set_ordered(
        self,
        alt_starting_balance,
        crypto_starting_balance,
        alt_trade_amount,
        order_id=None,
        decision_price=None,
        ordered_ts=None,
    ):
        session: Session
        with self.db.db_session() as session:
            trade: Trade = session.merge(self.trade)
            trade.alt_starting_balance = alt_starting_balance
            trade.alt_trade_amount = alt_trade_amount
            trade.crypto_starting_balance = crypto_starting_balance
            trade.order_id = order_id
            trade.decision_price = decision_price
            trade.ordered_ts = ordered_ts or datetime.utcnow()
            trade.state = TradeState.ORDERED
            self.db.send_update(trade)

    def set_complete(self, crypto_trade_amount, fill_price=None):
        session: Session
        with self.db.db_session() as session:
            trade: Trade = session.merge(self.trade)
            trade.crypto_trade_amount = crypto_trade_amount
            trade.fill_price = fill_price
            trade.fill_ts = datetime.utcnow()
            trade.state = TradeState.COMPLETE
            self.db.send_update(trade)


if __name__ == "__main__":
    database = Database(Logger(), Config())
    database.create_database()
