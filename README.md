# Binance Trade Bot

> An automated cryptocurrency trading bot for Binance, with a live dashboard.

A fork of [binance-trade-bot](https://github.com/edeng23/binance-trade-bot) by
**Eden Gaon**, repaired against the current Binance API and extended with a web
dashboard, benchmark-based performance tracking, and conventional risk controls.

```bash
pip install -r requirements.txt
cp .user.cfg.example user.cfg     # add your API keys
python run.py
```

That starts the bot and the dashboard together and opens
<http://localhost:5123>. `Ctrl+C` stops both.

| Guide | |
|---|---|
| [requirements.md](requirements.md) | Prerequisites and the full `user.cfg` reference |
| [deployment.md](deployment.md) | Local, Docker, ports, going live |
| [runbook.md](runbook.md) | Daily operation, troubleshooting, known limits |

## The idea

Cryptocurrencies mostly move together — when one spikes they all spike — but
with a phase offset. If coins oscillate against each other, it pays to trade the
risen coin for the fallen one and trade back when the ratio reverses.

Binance has no market for most altcoin pairs, so trades route through a bridge
currency, normally USDT:

<p align="center"><b>Coin A</b> → USDT → Coin B → USDT → Coin C → … → <b>Coin A</b></p>

For every ordered pair the bot stores the price ratio from the last time it held
the target coin. It jumps only when the live ratio beats that stored one by
enough to clear fees — so it never returns to a coin holding fewer units than
before.

**The risk is the mirror of the reward.** A coin in genuine free-fall looks like
a bargain to this rule, and the ratchet has no concept of being wrong: there is
no exit to cash unless you configure one. If every coin falls together, no ratio
moves, nothing triggers, and you hold through it.

## The dashboard

| Panel | Answers |
|---|---|
| **Jump Watch** | How close is each candidate to triggering, and at what price? |
| **Position** | Where is price between my stop and my target? |
| **Exit at** | What must my coin reach for *any* jump to fire? |
| **Open orders** | What is resting on the exchange holding my funds? (with Cancel) |
| **Performance** | Am I beating simply holding, or just riding the market? |
| **Ratchet** | Did I really end up with more units each time? |

The **vs Holding** figure is the one that matters. Every sample records the
value of the untouched starting basket alongside your actual portfolio, so a
rising market cannot be mistaken for a working strategy.

## What this fork changes

**Repairs** — the upstream bot no longer runs against Binance as it exists today:

- Spot testnet websockets moved host; the pinned library still used the retired one.
- `POST /api/v3/userDataStream` is **410 Gone**, so order fills were never
  reported and every trade hung forever. Fills are now polled over REST.
- Order quantities ignored `LOT_SIZE` `minQty`/`maxQty`, deadlocking on a
  rejection that retried unchanged forever.
- Orders abandoned across a restart kept their funds locked, invisibly.
- Timezone mismatches silently emptied scout history and every `?period=` filter.
- Dependencies moved to a set that installs and runs (Python 3.10+).

**Additions:**

- `run.py` — one command, with preflight checks that refuse to start a broken setup.
- Dashboard and JSON API, including order cancellation.
- Equity curve with hold/BTC benchmarks, and a ratchet ledger that tests the
  strategy's own claim.
- Stop-loss and take-profit, off by default — enabling them deliberately breaks
  the ratchet invariant.
- Marketable limit orders (`cross_spread`) with a `max_spread` guard; roughly
  half of all orders previously expired unfilled.

## Backtesting

```bash
python backtest.py
```

Edit the dates first — see [runbook.md](runbook.md#backtesting).

## Developing

Add a strategy in `binance_trade_bot/strategies/` — any file ending
`_strategy.py` that subclasses `AutoTrader` and implements `scout()` is picked
up automatically. See the README in that directory.

Linting config lives in `.pylintrc`, with `pylint-sqlalchemy` from
`dev-requirements.txt`.

> `.pre-commit-config.yaml` is still present but its `sort-supported-coin-list`
> hook points at `.pre-commit-hooks/sort-coins-file.py`, which was removed.
> Drop that hook or restore the script before running `pre-commit`.

## Licence and credit

GPL-3.0, inherited from the upstream project. Original work © Eden Gaon and
contributors. See [LICENSE](LICENSE).

## Disclaimer

This software is for informational purposes only and is not financial advice.
It has been exercised against the Binance **testnet**; the production path is
untested. Cryptocurrency trading carries substantial risk of loss.

**If you use real money, you do so entirely at your own risk.** No warranty is
given, and no liability is accepted for any loss arising from its use.
