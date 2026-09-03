# Local setup

Prerequisites are in `requirements.md`. Day-to-day operation is in `runbook.md`.

## 1. Python

You need **3.10 or newer** — `eventlet` and `dnspython` both require it.

```bash
python --version
```

> `runtime.txt` still says `python-3.8.11` and `Dockerfile` builds on
> `python:3.8`. Both are stale leftovers and would fail at `pip install`.
> Neither is used by the local setup below.

## 2. Virtual environment

Keeps these pinned versions out of your system Python.

```bash
python -m venv .venv
```

Activate it — you'll need this in every new shell:

```bash
.venv\Scripts\activate        # Windows (PowerShell / cmd)
source .venv/bin/activate     # macOS / Linux
```

`.venv/` is git-ignored.

## 3. Install

```bash
pip install -r requirements.txt
```

Roughly 40 packages including `python-binance`, `Flask`, `SQLAlchemy` and
`eventlet`. Add `dev-requirements.txt` only if you intend to lint.

## 4. Configure

```bash
cp .user.cfg.example user.cfg      # Windows: copy .user.cfg.example user.cfg
```

Edit `user.cfg` and set at minimum:

- `api_key` / `api_secret_key` — from <https://testnet.binance.vision> while
  `testnet=True`
- `current_coin` — **must** appear in `supported_coin_list`, or the bot exits
  at startup

Then check `supported_coin_list` holds coins that actually trade against your
bridge. Every option is documented in `requirements.md`.

## 5. Verify before running

```bash
python run.py --check
```

Validates the config, confirms the port is free, creates `logs/` and `data/`,
and exits without starting anything or touching your account. Fix whatever it
reports before continuing.

## 6. Run

```bash
python run.py
```

Starts the bot and the dashboard, waits for the dashboard to answer, opens
<http://localhost:5123>, and stops both on `Ctrl+C`.

| Flag | Effect |
|---|---|
| `--no-bot` | Dashboard only. Places no orders — use this to look around safely. |
| `--no-dashboard` | Bot only, headless |
| `--no-browser` | Don't open a browser |
| `--port 8000` | Serve the dashboard elsewhere (default 5123) |
| `--check` | Preflight and exit |

Output is tagged `[bot]` and `[web]` so you can tell the two apart.

## What is listening

Only **5123**, bound to `127.0.0.1`.

The dashboard exposes balances, trade history and an **order-cancellation
endpoint**. Read endpoints are unauthenticated, so do not bind it to a public
interface. Cancellation is same-origin guarded.

## Data and backups

Everything lives in `data/crypto_trading.db` — coins, ratios, trades, equity
history, the benchmark anchor. Back it up before upgrading:

```bash
cp data/crypto_trading.db data/crypto_trading.db.bak
```

Deleting it starts over with fresh ratios and a new benchmark anchor.
`logs/crypto_trading.log` holds the bot's own log at DEBUG level.

## Upgrading

Pull, reinstall dependencies, restart. Schema changes are applied at startup by
`Database.apply_schema_migrations()` — additive and idempotent, so an older
database upgrades in place. Back up first anyway.

## Going live

1. Replace the testnet keys in `user.cfg` with production keys.
2. Set `testnet=false`.
3. Confirm the dashboard header reads **LIVE — REAL FUNDS**, not `TESTNET`.

Read *Known limits* in `runbook.md` first. Everything here has been exercised
against the Binance spot testnet only; the production path is untested.
