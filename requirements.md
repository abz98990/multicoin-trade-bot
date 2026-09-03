# Requirements

## Runtime

| | |
|---|---|
| Python | **3.10 or newer** |
| OS | Windows, macOS, Linux |
| Disk | ~50 MB (the SQLite database grows ~30 MB/year) |

> `runtime.txt` (`python-3.8.11`) and `Dockerfile` (`python:3.8`) are stale
> leftovers from upstream — `eventlet==0.41.2` and `dnspython==2.8.0` declare
> `>=3.10`, so anything built on 3.8 fails at `pip install`. Neither file is
> used by the local setup in `deployment.md`.

```bash
pip install -r requirements.txt
```

`dev-requirements.txt` adds `pylint-sqlalchemy`, used only for linting.

## Binance account

An API key with **trading** permission. Withdrawal permission is not needed and
should not be granted.

- **Testnet** (default): create keys at <https://testnet.binance.vision>. Fake
  funds, resets periodically.
- **Live**: keys from your real Binance account. Testnet keys do **not** work
  against production, and vice versa.

## Files you must create

| File | Purpose |
|---|---|
| `user.cfg` | Keys and tuning. Copy from `.user.cfg.example` — **no leading dot**. Git-ignored. |
| `supported_coin_list` | Coins to rotate between, one per line. `#` disables a line. |

`logs/` and `data/` are created automatically.

## Configuration reference

All keys live in `user.cfg` under `[binance_user_config]`. Every one can be
overridden by an environment variable of the same name in upper case.

### Connection

| Key | Meaning |
|---|---|
| `api_key`, `api_secret_key` | Binance credentials |
| `testnet` | `True` for the sandbox, `false` for real funds |
| `tld` | `com`, or `us` for Binance.US |
| `bridge` | Currency hopped through, normally `USDT` |

### Strategy

| Key | Meaning |
|---|---|
| `strategy` | `default` (hold one coin) or `multiple_coins` |
| `current_coin` | Starting coin. **Must appear in `supported_coin_list`** or the bot exits at startup. Blank picks one at random. |
| `use_margin` | `yes` uses `scout_margin`; `no` uses `scout_multiplier` |
| `scout_multiplier` | Margin over fees a jump must clear. 3–7 is sane. Higher trades less. |
| `scout_margin` | Flat % gain required per jump when `use_margin=yes` |
| `scout_sleep_time` | Seconds between price checks |

### Execution

| Key | Meaning |
|---|---|
| `cross_spread` | `yes` prices orders at the far side of the book so they fill on placement. `no` rests passively and often never fills. |
| `max_spread` | Refuse pairs whose bid/ask spread exceeds this %. Must stay well under your jump threshold. `0` disables. |
| `buy_timeout`, `sell_timeout` | Minutes before an unfilled order is cancelled. `0` never cancels. |

### Risk (off by default)

| Key | Meaning |
|---|---|
| `stop_loss` | % below entry to close the position. `0` disables. |
| `take_profit` | % above entry to close. `0` disables. |
| `stop_cooldown` | Minutes a coin is skipped after stopping out |

> A stop-loss sells at a loss, which the rotation strategy is otherwise built
> never to do. Enabling it is a change of strategy, not just a safety net.
> Levels are fixed when a position opens and are **not** applied retroactively.
