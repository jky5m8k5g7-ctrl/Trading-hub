# jev_paper_bot.py

import time
import requests
from dataclasses import dataclass
from dotenv import load_dotenv
from typesafe_sdk import Choice, TypeSafeClient

load_dotenv()  # picks up TYPESAFE_API_KEY from a local .env, see .env.example

STARTING_BALANCE = 200.00
MAX_NOTIONAL = 50.00
FEE_RATE = 0.0025       # 0.25%
SLIPPAGE = 0.0002       # 2 bps
MIN_CONFIDENCE = 0.72
MAX_DRAWDOWN = 0.05
LOOP_SECONDS = 10

SYMBOLS = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "DOGE/USD",
    "XRP/USD",
]

# Kraken's public REST API wants its own pair codes, not the "BASE/QUOTE"
# form used everywhere else in this file.
KRAKEN_PAIRS = {
    "BTC/USD": "XXBTZUSD",
    "ETH/USD": "XETHZUSD",
    "SOL/USD": "SOLUSD",
    "DOGE/USD": "XDGUSD",
    "XRP/USD": "XXRPZUSD",
}

ACTIONS = ["LONG", "SHORT", "HOLD"]

client = TypeSafeClient()


@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry: float


class PaperAccount:
    def __init__(self):
        self.cash = STARTING_BALANCE
        self.position = None
        self.realized_pnl = 0.0
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.peak_equity = STARTING_BALANCE

    def unrealized(self, prices):
        if not self.position:
            return 0.0

        p = self.position
        last = prices[p.symbol]["last"]

        if p.side == "LONG":
            return (last - p.entry) * p.qty

        return (p.entry - last) * p.qty

    def equity(self, prices):
        return self.cash + self.unrealized(prices)

    def drawdown(self, prices):
        equity = self.equity(prices)
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - equity) / self.peak_equity

    def open_position(self, symbol, side, market):
        if side == "LONG":
            fill = market["ask"] * (1 + SLIPPAGE)
        else:
            fill = market["bid"] * (1 - SLIPPAGE)

        qty = MAX_NOTIONAL / fill
        fee = MAX_NOTIONAL * FEE_RATE

        self.cash -= fee
        self.realized_pnl -= fee

        self.position = Position(
            symbol=symbol,
            side=side,
            qty=qty,
            entry=fill,
        )

        self.trades += 1

        print(
            f"OPEN {side} {symbol} "
            f"@ {fill:.6f} qty={qty:.6f}"
        )

    def close_position(self, market):
        if not self.position:
            return

        p = self.position

        if p.side == "LONG":
            fill = market["bid"] * (1 - SLIPPAGE)
            gross = (fill - p.entry) * p.qty
        else:
            fill = market["ask"] * (1 + SLIPPAGE)
            gross = (p.entry - fill) * p.qty

        exit_notional = fill * p.qty
        fee = exit_notional * FEE_RATE

        net = gross - fee

        self.cash += net
        self.realized_pnl += net

        if net > 0:
            self.wins += 1
        else:
            self.losses += 1

        print(
            f"CLOSE {p.side} {p.symbol} "
            f"@ {fill:.6f} net={net:+.4f}"
        )

        self.position = None


def get_market(symbol):
    response = requests.get(
        "https://api.kraken.com/0/public/Ticker",
        params={
            "pair": KRAKEN_PAIRS[symbol],
        },
        timeout=10,
    )

    response.raise_for_status()

    payload = response.json()

    if payload["error"]:
        raise RuntimeError(payload["error"])

    row = next(iter(payload["result"].values()))

    ask = float(row["a"][0])
    bid = float(row["b"][0])
    last = float(row["c"][0])
    low = float(row["l"][1])
    high = float(row["h"][1])
    open_price = float(row["o"])

    spread_bps = ((ask - bid) / last) * 10000

    day_return = (
        ((last / open_price) - 1) * 100
        if open_price
        else 0
    )

    return {
        "last": last,
        "bid": bid,
        "ask": ask,
        "high": high,
        "low": low,
        "open": open_price,
        "day_return_pct": day_return,
        "spread_bps": spread_bps,
    }


def decide(symbol, market):
    """Ask the JEV reasoning layer (TypeSafe) for a directional call on one symbol.

    Returns (action, confidence) where action is one of ACTIONS and
    confidence is TypeSafe's own 0-1 confidence for that choice.
    """
    response = client.system_one(
        state={
            "symbol": symbol,
            "last": market["last"],
            "day_return_pct": round(market["day_return_pct"], 3),
            "spread_bps": round(market["spread_bps"], 2),
            "high": market["high"],
            "low": market["low"],
        },
        questions={
            "action": Choice(
                instructions=(
                    "Given this crypto pair's intraday move, spread, and "
                    "high/low range, should a small paper position go "
                    "LONG, SHORT, or stay flat (HOLD)?"
                ),
                criteria={
                    "LONG": "Price action favors buying.",
                    "SHORT": "Price action favors selling/shorting.",
                    "HOLD": "No clear edge; stay flat.",
                },
            ),
        },
    )
    answer = response.choices["action"]
    return answer.choice, answer.confidence


def fetch_markets():
    prices = {}
    for symbol in SYMBOLS:
        try:
            prices[symbol] = get_market(symbol)
        except Exception as exc:
            print(f"market fetch failed for {symbol}: {exc}")
    return prices


def manage_open_position(account, prices):
    symbol = account.position.symbol
    market = prices.get(symbol)
    if market is None:
        return

    action, confidence = decide(symbol, market)
    if confidence < MIN_CONFIDENCE:
        return

    flips = (action == "SHORT" and account.position.side == "LONG") or (
        action == "LONG" and account.position.side == "SHORT"
    )
    if action == "HOLD" or flips:
        account.close_position(market)


def open_new_position(account, prices):
    best = None  # (symbol, action, confidence)

    for symbol, market in prices.items():
        try:
            action, confidence = decide(symbol, market)
        except Exception as exc:
            print(f"JEV decision failed for {symbol}: {exc}")
            continue

        if action == "HOLD" or confidence < MIN_CONFIDENCE:
            continue

        if best is None or confidence > best[2]:
            best = (symbol, action, confidence)

    if best is None:
        return

    symbol, action, confidence = best
    print(f"JEV {action} {symbol} confidence={confidence:.2f}")
    account.open_position(symbol, action, prices[symbol])


def main():
    account = PaperAccount()
    print(f"starting paper account with ${STARTING_BALANCE:.2f}")

    while True:
        try:
            prices = fetch_markets()
            if not prices:
                time.sleep(LOOP_SECONDS)
                continue

            drawdown = account.drawdown(prices)
            if drawdown >= MAX_DRAWDOWN:
                print(f"MAX DRAWDOWN HIT ({drawdown:.1%}) - closing position and halting")
                if account.position and account.position.symbol in prices:
                    account.close_position(prices[account.position.symbol])
                break

            if account.position:
                manage_open_position(account, prices)
            else:
                open_new_position(account, prices)

            equity = account.equity(prices)
            print(
                f"equity=${equity:.2f} cash=${account.cash:.2f} "
                f"realized_pnl={account.realized_pnl:+.2f} "
                f"trades={account.trades} wins={account.wins} losses={account.losses}"
            )
        except Exception as exc:
            print(f"cycle failed: {exc}")

        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
