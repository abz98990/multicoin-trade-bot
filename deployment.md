# Deployment

Prerequisites are in `requirements.md`. Day-to-day operation is in `runbook.md`.

## Local

```bash
pip install -r requirements.txt
cp .user.cfg.example user.cfg     # then add your API keys
python run.py --check             # validate without starting anything
python run.py
```

`run.py` starts the bot and the dashboard, waits for the dashboard to answer,
opens a browser, and stops both on Ctrl+C.

| Flag | Effect |
|---|---|
| `--no-bot` | Dashboard only. Places no orders. |
| `--no-dashboard` | Bot only, headless |
| `--no-browser` | Don't open a browser |
| `--port 8000` | Serve the dashboard elsewhere (default 5123) |
| `--check` | Run preflight and exit |

Preflight refuses to start if the port is taken, `user.cfg` is missing, or
`current_coin` is not in `supported_coin_list` — so a bad setup never leaves
the bot half-running.

## Docker

```bash
docker compose up -d
```

Three services: `crypto-trading` (the bot), `api` (dashboard on 5123),
`sqlitebrowser` (port 3000).

**The image does not currently build.** The builder stage is `python:3.8`, but
`eventlet==0.41.2` and `dnspython==2.8.0` require Python 3.10+. Fix before
deploying:

```dockerfile
FROM --platform=$BUILDPLATFORM python:3.13 as builder
```

`runtime.txt` (`python-3.8.11`, used by Heroku-style buildpacks) needs the same
correction.

`docker-compose.yml` mounts `./user.cfg`, `./supported_coin_list`, `./data` and
`./logs`. It expects `user.cfg` at the repo root.

## Ports

| Port | Service |
|---|---|
| 5123 | Dashboard and JSON API |
| 3000 | sqlitebrowser (Docker only) |

The dashboard binds `127.0.0.1` by default. It exposes account balances, trade
history and an **order-cancellation endpoint** — do not expose it to a network
you do not control. Cancellation is same-origin guarded, but the read
endpoints are not authenticated.

## Persistence

Everything lives in `data/crypto_trading.db`: coins, ratios, trades, equity
history, benchmark anchor. Back it up before upgrading; delete it and the bot
starts from scratch with fresh ratios.

Schema changes are applied automatically at startup by
`Database.apply_schema_migrations()`. Migrations are additive and idempotent,
so a database from an older revision upgrades in place.

## Going live

1. Replace the testnet keys with production keys in `user.cfg`.
2. Set `testnet=false`.
3. Confirm the dashboard header reads **LIVE — REAL FUNDS** instead of `TESTNET`.

Before you do, read the *Known limits* section of `runbook.md`. The production
path has not been exercised — everything in this repository was verified
against the Binance spot testnet.
