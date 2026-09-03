# Runbook

Setup is in `requirements.md`. Install and deploy are in `deployment.md`.

## Start and stop

```bash
python run.py          # bot + dashboard, Ctrl+C stops both
python run.py --no-bot # look without trading
```

Dashboard: <http://localhost:5123>

**Cancel any open orders before stopping.** The bot forgets orders placed by a
previous process; recovery adopts them on the next start, but between runs
their funds stay locked.

## What the bot does

Holds one coin. Every `scout_sleep_time` seconds it compares the price ratio of
that coin against every other, and jumps when a ratio beats the one recorded
last time it held the target — by enough to clear fees. Selling only ever
happens as the first leg of a jump.

## Reading the dashboard

| Panel | What it tells you |
|---|---|
| **Position** | Entry, stop, target, and where price sits between them. Says "off" unless `stop_loss`/`take_profit` are set. |
| **Open orders** | Anything resting on the exchange, with a Cancel button. These hold your funds. |
| **Jump Watch** | Every candidate, its gap to triggering, and the price it must reach. This is the panel that matters. |
| **Exit at** | The price your held coin must reach for *any* jump to fire. |
| **Performance** | Strategy vs holding vs BTC, indexed to 100. Period selector top-right. |
| **vs Holding** | The only number that separates strategy from market. `+0.000%` means it hasn't traded. |
| **Ratchet** | Units held each time a coin is re-entered. `ratio < 1` means the core claim failed. |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `port 5123 is already in use` | Another `run.py` is running. Stop it or use `--port`. |
| `current_coin=… is not in supported_coin_list` | Add the coin to the list, or pick one from it. |
| `user.cfg not found` | You have `.user.cfg` with the dot. Copy to `user.cfg`. |
| `BUY QTY 0.0` / `Couldn't place a buy order` | Bridge funds locked in unfilled orders. Cancel them from the Open orders panel. |
| `Ticker does not exist: …BNB` / `…BTC` | Normal. Fee-discount and charting lookups only; trading is unaffected. Remembered for 7 days. |
| `Capping … to LOT_SIZE maxQty` | Normal. Balance exceeds the pair's largest allowed order; the remainder stays in the bridge. |
| `Skipping …: spread … exceeds max_spread` | Normal. That pair is too wide to trade through profitably. |
| Orders never fill | Set `cross_spread=yes`. Passive orders priced at the last trade can sit below the bid indefinitely. |
| Dashboard shows `Failed to fetch` | The bot or API server stopped. Check the terminal. |
| Chart looks flat / two periods identical | The note says `(only N recorded)` when a window holds less data than it asks for. |

## Backtesting

```bash
python backtest.py
```

Edit the dates first. The file starts at January 2021, which predates several
listed coins, so it refetches empty data forever. Use a recent window, and make
the start date **UTC and on an exact minute** or every price lookup misses.

## Known limits

- **No exit to cash.** Without `stop_loss`/`take_profit` the only way out of a
  coin is a jump into another. If everything falls together, nothing fires.
- **Restarting abandons open orders** until the next start adopts them.
- **Limit orders, so partial fills are routine.** Historically about half of all
  orders timed out unfilled before `cross_spread` was added.
- **The BTC portfolio figure is incomplete** wherever a coin's BTC pair is
  delisted; the panel says how many are counted. Trust the USD figure.
- **A falling coin looks like a bargain** to the ratchet, and you can end up
  holding it. There is no stop unless you configure one.
- **Only the testnet is exercised.** The production path is untested.
