"""
Rank USDT pairs by how much they actually move, for picking scout candidates.

Two different Binance endpoints, on purpose: testnet's own price data is
synthetic and not representative of real market behaviour, but it's the only
thing that says what this bot can actually trade right now. Mainnet's 24hr
stats are real, so the split is: "tradable on testnet" (a filter) and
"how much does it really move" (mainnet numbers, for ranking).

Usage:
    .venv/Scripts/python.exe rank_volatility.py [--top N] [--list-only]

--list-only restricts the ranking to symbols already in supported_coin_list,
instead of the full testnet-tradable USDT universe.
"""
import argparse
import json
import urllib.request

TESTNET_EXCHANGE_INFO = "https://testnet.binance.vision/api/v3/exchangeInfo"
MAINNET_24HR = "https://api.binance.com/api/v3/ticker/24hr"


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())


def testnet_tradable_usdt_symbols():
    data = fetch_json(TESTNET_EXCHANGE_INFO)
    out = {}
    for s in data["symbols"]:
        if s["quoteAsset"] == "USDT" and s["status"] == "TRADING":
            out[s["baseAsset"]] = s["symbol"]
    return out


def mainnet_24hr_stats():
    return {row["symbol"]: row for row in fetch_json(MAINNET_24HR)}


def load_supported_coin_list():
    try:
        with open("supported_coin_list", encoding="utf-8") as f:
            return {
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            }
    except FileNotFoundError:
        return set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    tradable = testnet_tradable_usdt_symbols()
    stats = mainnet_24hr_stats()
    configured = load_supported_coin_list()

    if args.list_only:
        tradable = {base: sym for base, sym in tradable.items() if base in configured}

    rows = []
    for base, symbol in tradable.items():
        row = stats.get(symbol)
        if row is None:
            continue
        try:
            high, low = float(row["highPrice"]), float(row["lowPrice"])
            change_pct = float(row["priceChangePercent"])
            quote_volume = float(row["quoteVolume"])
        except (TypeError, ValueError):
            continue
        if low <= 0 or quote_volume <= 0:
            continue
        range_pct = (high - low) / low * 100
        rows.append((base, range_pct, change_pct, quote_volume, base in configured))

    rows.sort(key=lambda r: r[1], reverse=True)

    print(f"{'Coin':<8}{'24h range %':>13}{'24h change %':>14}{'24h volume (USDT)':>20}  In list?")
    for base, range_pct, change_pct, volume, in_list in rows[: args.top]:
        marker = "yes" if in_list else ""
        print(f"{base:<8}{range_pct:>12.2f}%{change_pct:>13.2f}%{volume:>20,.0f}  {marker}")

    print()
    print(f"{len(rows)} testnet-tradable USDT pairs considered (mainnet 24h stats, real market data).")
    print("'24h range %' = (high-low)/low - how far it actually swung, regardless of where it ended up.")


if __name__ == "__main__":
    main()
