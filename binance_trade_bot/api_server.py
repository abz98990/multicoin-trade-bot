import math
import re
import threading
from datetime import datetime, timedelta
from itertools import groupby
from typing import List, Tuple
from urllib.parse import urlparse

from binance.client import Client
from binance.exceptions import BinanceAPIException
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import Config
from .database import Database
from .logger import Logger
from .models import (
    Coin,
    CoinValue,
    CurrentCoin,
    EquitySnapshot,
    Pair,
    RatchetEntry,
    ScoutHistory,
    Trade,
    TradeState,
)

app = Flask(__name__)
# Re-read dashboard.html when it changes on disk. Jinja otherwise caches the
# compiled template whenever debug is off, and restarting this process to pick
# up a UI edit also restarts the bot -- which makes it forget any order it has
# resting on the exchange. A stat() per render is a cheap way to avoid that.
app.config["TEMPLATES_AUTO_RELOAD"] = True
cors = CORS(app, resources={r"/api/*": {"origins": "*"}})

socketio = SocketIO(app, cors_allowed_origins="*")


logger = Logger("api_server")
config = Config()
db = Database(logger, config)


# These models timestamp with datetime.utcnow(); CoinValue uses datetime.now().
# Comparing a UTC column against local now() silently returns nothing for any
# timezone east of UTC, so the cutoff has to match how each model stores time.
# The real fix is to make every model agree, but that needs a data migration.
_UTC_MODELS = (CurrentCoin, EquitySnapshot, RatchetEntry, ScoutHistory, Trade)

_PERIOD_UNITS = {
    "s": lambda n: timedelta(seconds=n),
    "h": lambda n: timedelta(hours=n),
    "d": lambda n: timedelta(days=n),
    "w": lambda n: timedelta(weeks=n),
    "m": lambda n: timedelta(days=28 * n),
}


def filter_period(query, model):
    """Filter a query to a ?period= window, e.g. 30s / 6h / 7d / 2w / 1m."""
    period = request.args.get("period", "all")

    if period == "all":
        return query

    match = re.fullmatch(r"\s*(\d*)\s*([shdwm])\s*", period)
    if match is None:
        return query

    num = float(match.group(1) or 1)
    now = datetime.utcnow() if model in _UTC_MODELS else datetime.now()
    return query.filter(model.datetime >= now - _PERIOD_UNITS[match.group(2)](num))


# Explicit allowlist rather than a blocklist: a field added to Config later
# must be opted in here, so an API key can never leak by default.
_PUBLIC_CONFIG_FIELDS = (
    "BRIDGE_SYMBOL",
    "BINANCE_TLD",
    "TESTNET",
    "STRATEGY",
    "USE_MARGIN",
    "SCOUT_MULTIPLIER",
    "SCOUT_MARGIN",
    "SCOUT_SLEEP_TIME",
    "BUY_TIMEOUT",
    "SELL_TIMEOUT",
)


# Built on first use rather than at import: constructing a Client pings
# Binance, and a key problem should not stop the dashboard from serving.
_binance_client = None
_binance_lock = threading.Lock()


def binance_client() -> Client:
    global _binance_client  # pylint: disable=global-statement
    with _binance_lock:
        if _binance_client is None:
            _binance_client = Client(
                config.BINANCE_API_KEY,
                config.BINANCE_API_SECRET_KEY,
                tld=config.BINANCE_TLD,
                testnet=config.TESTNET,
            )
        return _binance_client


def is_cross_origin() -> bool:
    """
    True when the browser says this request came from another site.

    CORS on /api/* is wide open, which is fine for reads but would otherwise let
    any page you happen to have open cancel your orders. Browsers always attach
    Origin to a cross-site POST, so anything that does not match the host we were
    reached on gets refused.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return False
    return urlparse(origin).netloc != request.host


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/performance")
def performance():
    """
    The equity curve with the benchmarks it is measured against.

    Portfolio value alone cannot separate a working strategy from a rising
    market, so every sample carries the value of the untouched starting basket
    and of the same money held as BTC, priced at the same instant.
    """
    session: Session
    with db.db_session() as session:
        query = session.query(EquitySnapshot).order_by(EquitySnapshot.datetime.asc())
        query = filter_period(query, EquitySnapshot)
        series = [snapshot.info() for snapshot in query.all()]

    anchor = db.get_benchmark_anchor()
    return jsonify({"anchor": anchor.info() if anchor else None, "series": series})


@app.route("/api/ratchet")
def ratchet():
    """Units held at each entry into a coin, against the previous entry."""
    session: Session
    with db.db_session() as session:
        query = session.query(RatchetEntry).order_by(RatchetEntry.datetime.desc())
        query = filter_period(query, RatchetEntry)
        return jsonify([entry.info() for entry in query.all()])


def _symbol_filter(symbol: str, filter_type: str):
    info = binance_client().get_symbol_info(symbol)
    if not info:
        return None
    for entry in info["filters"]:
        if entry["filterType"] == filter_type:
            return entry
    return None


def _step_decimals(step: str) -> int:
    """Decimal places allowed by a stepSize like '0.10000000'."""
    step = step.rstrip("0")
    if "." not in step:
        return 0
    return len(step.split(".")[1])


@app.route("/api/round_trips")
def round_trips():
    """Every entry paired with the exit that closed it, newest first."""
    return jsonify(db.coin_round_trips())


@app.route("/api/holdings/buy", methods=["POST"])
def buy_holding():
    """
    Buy a coin manually for a given amount of bridge currency, with its own
    stop-loss and take-profit.

    This is the only way to attach risk levels to a position on demand: levels
    are fixed when a position opens, and the bot only opens one when its own
    signal fires.
    """
    if is_cross_origin():
        return jsonify({"error": "Holdings can only be bought from the dashboard itself."}), 403

    payload = request.get_json(silent=True) or {}
    symbol = str(payload.get("symbol", "")).upper().strip()
    bridge = config.BRIDGE.symbol

    if symbol not in config.SUPPORTED_COIN_LIST:
        return jsonify(
            {"error": f"{symbol or '(none)'} is not in supported_coin_list, so the bot could not manage it."}
        ), 400

    try:
        amount = float(payload.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"error": f"Enter how much {bridge} to spend."}), 400
    if amount <= 0:
        return jsonify({"error": "Amount must be greater than zero."}), 400

    def optional_percent(key):
        raw = payload.get(key)
        if raw in (None, ""):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return "bad"
        return value if value > 0 else None

    stop_pct, target_pct = optional_percent("stop_loss"), optional_percent("take_profit")
    if "bad" in (stop_pct, target_pct):
        return jsonify({"error": "Stop-loss and take-profit must be numbers."}), 400

    pair = symbol + bridge
    client = binance_client()

    try:
        free = float(client.get_asset_balance(asset=bridge)["free"])
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": "Could not reach Binance: {}".format(exc)}), 502

    if amount > free:
        return jsonify({"error": f"Only {free:,.2f} {bridge} available."}), 400

    lot = _symbol_filter(pair, "LOT_SIZE")
    if lot is None:
        return jsonify({"error": f"{pair} is not a tradable pair."}), 400

    try:
        price = float(client.get_symbol_ticker(symbol=pair)["price"])
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": "Could not price {}: {}".format(pair, exc)}), 502

    decimals = _step_decimals(lot["stepSize"])
    factor = 10 ** decimals
    quantity = math.floor(amount / price * factor) / factor
    quantity = min(quantity, float(lot["maxQty"]))
    quantity = math.floor(quantity * factor) / factor

    if quantity < float(lot["minQty"]):
        return jsonify(
            {"error": f"{amount:g} {bridge} buys less than the minimum order size of {lot['minQty']} {symbol}."}
        ), 400

    notional = _symbol_filter(pair, "NOTIONAL")
    if notional and quantity * price < float(notional["minNotional"]):
        return jsonify(
            {"error": f"That is below the {notional['minNotional']} {bridge} minimum order value."}
        ), 400

    quantity_s = "{:0.0{}f}".format(quantity, decimals)
    logger.warning(f"Manual buy requested from the dashboard: {quantity_s} {symbol} for ~{amount:g} {bridge}")

    trade_log = db.start_trade_log(db.get_coin(symbol), db.get_coin(bridge), False)
    try:
        order = client.order_market_buy(symbol=pair, quantity=quantity_s)
    except BinanceAPIException as exc:
        logger.warning(f"Manual buy of {symbol} rejected: {exc.message}")
        return jsonify({"error": exc.message}), 400
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": "Could not reach Binance: {}".format(exc)}), 502

    filled = float(order["executedQty"])
    spent = float(order["cummulativeQuoteQty"])
    fill_price = (spent / filled) if filled else price

    trade_log.set_ordered(0.0, free, quantity, int(order["orderId"]), price)
    trade_log.set_complete(spent, fill_price)

    stop = fill_price * (1 - stop_pct / 100) if stop_pct else None
    target = fill_price * (1 + target_pct / 100) if target_pct else None
    db.set_current_coin(symbol, fill_price, stop, target)

    logger.info(
        f"Manual buy filled: {filled:g} {symbol} for {spent:g} {bridge} at {fill_price:.8g}"
        + (f", stop {stop:.8g}" if stop else "")
        + (f", target {target:.8g}" if target else "")
    )
    return jsonify(
        {
            "symbol": symbol,
            "bought": filled,
            "spent": spent,
            "price": fill_price,
            "stop_loss": stop,
            "take_profit": target,
            "status": order["status"],
        }
    )


@app.route("/api/holdings/<symbol>/sell", methods=["POST"])
def sell_holding(symbol: str):
    """
    Sell the whole free balance of one coin into the bridge, at market.

    Deliberately self-contained rather than reusing BinanceAPIManager.sell_alt:
    that path needs the websocket stream manager and the REST order poller,
    which belong to the bot process. A market order fills on placement, so
    there is nothing here to poll.
    """
    if is_cross_origin():
        return jsonify({"error": "Holdings can only be sold from the dashboard itself."}), 403

    symbol = symbol.upper()
    bridge = config.BRIDGE.symbol
    if symbol == bridge:
        return jsonify({"error": f"{bridge} is the bridge currency; there is nothing to sell it into."}), 400

    pair = symbol + bridge
    client = binance_client()

    try:
        holding = client.get_asset_balance(asset=symbol)
    except BinanceAPIException as exc:
        return jsonify({"error": exc.message}), 400
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": "Could not reach Binance: {}".format(exc)}), 502

    # Binance answers with null rather than an error for an unknown asset.
    if not holding:
        return jsonify({"error": f"{symbol} is not an asset on this account."}), 400

    balance = float(holding["free"])

    if not balance:
        return jsonify({"error": f"No free {symbol} to sell."}), 400

    lot = _symbol_filter(pair, "LOT_SIZE")
    if lot is None:
        return jsonify({"error": f"{pair} is not a tradable pair."}), 400

    decimals = _step_decimals(lot["stepSize"])
    factor = 10 ** decimals
    quantity = math.floor(balance * factor) / factor
    quantity = min(quantity, float(lot["maxQty"]))
    quantity = math.floor(quantity * factor) / factor

    if quantity < float(lot["minQty"]):
        return jsonify(
            {"error": f"{balance:g} {symbol} is below the minimum order size of {lot['minQty']}."}
        ), 400

    notional = _symbol_filter(pair, "NOTIONAL")
    price = None
    try:
        price = float(client.get_symbol_ticker(symbol=pair)["price"])
    except Exception:  # pylint: disable=broad-except
        pass
    if notional and price and quantity * price < float(notional["minNotional"]):
        return jsonify(
            {"error": f"That would be worth less than the {notional['minNotional']} {bridge} minimum."}
        ), 400

    quantity_s = "{:0.0{}f}".format(quantity, decimals)
    logger.warning(f"Manual sell requested from the dashboard: {quantity_s} {symbol} -> {bridge}")

    trade_log = db.start_trade_log(db.get_coin(symbol), db.get_coin(bridge), True)
    try:
        order = client.order_market_sell(symbol=pair, quantity=quantity_s)
    except BinanceAPIException as exc:
        db.finish_trade(None, TradeState.CANCELED)
        logger.warning(f"Manual sell of {symbol} rejected: {exc.message}")
        return jsonify({"error": exc.message}), 400
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": "Could not reach Binance: {}".format(exc)}), 502

    filled = float(order["executedQty"])
    received = float(order["cummulativeQuoteQty"])
    fill_price = (received / filled) if filled else None

    trade_log.set_ordered(balance, None, quantity, int(order["orderId"]), price)
    trade_log.set_complete(received, fill_price)

    logger.info(f"Manual sell filled: {filled:g} {symbol} for {received:g} {bridge}")
    return jsonify(
        {
            "symbol": symbol,
            "sold": filled,
            "received": received,
            "bridge": bridge,
            "price": fill_price,
            "status": order["status"],
        }
    )


@app.route("/api/open_orders")
def open_orders():
    """
    Orders currently resting on the exchange.

    Read live from Binance, not from the database: the bot only tracks orders it
    placed in the current process, so anything left over from a previous run is
    invisible locally while still holding your funds.
    """
    try:
        orders = binance_client().get_open_orders()
    except BinanceAPIException as exc:
        return jsonify({"error": "Binance rejected the request: {}".format(exc.message)}), 502
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": "Could not reach Binance: {}".format(exc)}), 502

    return jsonify(
        [
            {
                "order_id": order["orderId"],
                "symbol": order["symbol"],
                "side": order["side"],
                "type": order["type"],
                "status": order["status"],
                "price": float(order["price"]),
                "quantity": float(order["origQty"]),
                "filled": float(order["executedQty"]),
                "time": order["time"],
            }
            for order in orders
        ]
    )


@app.route("/api/open_orders/<int:order_id>/cancel", methods=["POST"])
def cancel_open_order(order_id: int):
    """Cancel one resting order. Any filled portion is kept."""
    if is_cross_origin():
        return jsonify({"error": "Orders can only be cancelled from the dashboard itself."}), 403

    payload = request.get_json(silent=True) or {}
    symbol = payload.get("symbol") or request.args.get("symbol")
    if not symbol:
        return jsonify({"error": "symbol is required to cancel an order"}), 400

    try:
        result = binance_client().cancel_order(symbol=symbol, orderId=order_id)
    except BinanceAPIException as exc:
        return jsonify({"error": exc.message}), 400
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"error": "Could not reach Binance: {}".format(exc)}), 502

    return jsonify(
        {
            "order_id": result["orderId"],
            "symbol": result["symbol"],
            "status": result["status"],
            "filled": float(result["executedQty"]),
            "quantity": float(result["origQty"]),
        }
    )


@app.route("/api/config")
def bot_config():
    """Scouting parameters the dashboard needs to compute the jump threshold."""
    return jsonify({field: getattr(config, field, None) for field in _PUBLIC_CONFIG_FIELDS})


@app.route("/api/value_history/<coin>")
@app.route("/api/value_history")
def value_history(coin: str = None):
    session: Session
    with db.db_session() as session:
        query = session.query(CoinValue).order_by(CoinValue.coin_id.asc(), CoinValue.datetime.asc())

        query = filter_period(query, CoinValue)

        if coin:
            values: List[CoinValue] = query.filter(CoinValue.coin_id == coin).all()
            return jsonify([entry.info() for entry in values])

        coin_values = groupby(query.all(), key=lambda cv: cv.coin)
        return jsonify({coin.symbol: [entry.info() for entry in history] for coin, history in coin_values})


@app.route("/api/total_value_history")
def total_value_history():
    session: Session
    with db.db_session() as session:
        query = session.query(
            CoinValue.datetime,
            func.sum(CoinValue.btc_value),
            func.sum(CoinValue.usd_value),
        ).group_by(CoinValue.datetime)

        query = filter_period(query, CoinValue)

        total_values: List[Tuple[datetime, float, float]] = query.all()
        return jsonify([{"datetime": tv[0], "btc": tv[1], "usd": tv[2]} for tv in total_values])


@app.route("/api/trade_history")
def trade_history():
    session: Session
    with db.db_session() as session:
        query = session.query(Trade).order_by(Trade.datetime.asc())

        query = filter_period(query, Trade)

        trades: List[Trade] = query.all()
        return jsonify([trade.info() for trade in trades])


@app.route("/api/scouting_history")
def scouting_history():
    _current_coin = db.get_current_coin()
    coin = _current_coin.symbol if _current_coin is not None else None
    session: Session
    with db.db_session() as session:
        query = (
            session.query(ScoutHistory)
            .join(ScoutHistory.pair)
            .filter(Pair.from_coin_id == coin)
            .order_by(ScoutHistory.datetime.asc())
        )

        query = filter_period(query, ScoutHistory)

        scouts: List[ScoutHistory] = query.all()
        return jsonify([scout.info() for scout in scouts])


@app.route("/api/current_coin")
def current_coin():
    coin = db.get_current_coin()
    return coin.info() if coin else None


@app.route("/api/current_coin_history")
def current_coin_history():
    session: Session
    with db.db_session() as session:
        query = session.query(CurrentCoin)

        query = filter_period(query, CurrentCoin)

        current_coins: List[CurrentCoin] = query.all()
        return jsonify([cc.info() for cc in current_coins])


@app.route("/api/coins")
def coins():
    session: Session
    with db.db_session() as session:
        _current_coin = session.merge(db.get_current_coin())
        _coins: List[Coin] = session.query(Coin).all()
        return jsonify([{**coin.info(), "is_current": coin == _current_coin} for coin in _coins])


@app.route("/api/pairs")
def pairs():
    session: Session
    with db.db_session() as session:
        all_pairs: List[Pair] = session.query(Pair).all()
        return jsonify([pair.info() for pair in all_pairs])


@socketio.on("update", namespace="/backend")
def handle_my_custom_event(json):
    emit("update", json, namespace="/frontend", broadcast=True)


if __name__ == "__main__":
    socketio.run(app, debug=True, port=5123)
