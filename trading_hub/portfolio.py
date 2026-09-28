"""Paper trading portfolio: cash, open positions, and equity/drawdown tracking."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

from trading_hub import config


@dataclass
class Position:
    asset: str
    direction: int  # 1 = long, -1 = short
    size_usd: float
    entry_price: float
    opened_at: int

    def unrealized_pnl(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        pct_move = (current_price - self.entry_price) / self.entry_price
        return self.size_usd * pct_move * self.direction


@dataclass
class Trade:
    timestamp: int
    action: str
    asset: str
    direction: int
    size_usd: float
    price: float
    fee_usd: float
    pnl_usd: float = 0.0


@dataclass
class Portfolio:
    cash: float = config.STARTING_CASH_USD
    starting_equity: float = config.STARTING_CASH_USD
    peak_equity: float = config.STARTING_CASH_USD
    positions: dict[str, Position] = field(default_factory=dict)
    trade_log: list[Trade] = field(default_factory=list)
    last_trade_at: dict[str, int] = field(default_factory=dict)
    equity_history: list[dict] = field(default_factory=list)

    def record_equity(self, prices: dict[str, float], now: int | None = None) -> None:
        now = now if now is not None else int(time.time())
        self.equity_history.append({"timestamp": now, "equity": self.equity(prices)})
        if len(self.equity_history) > config.MAX_EQUITY_HISTORY_POINTS:
            self.equity_history = self.equity_history[-config.MAX_EQUITY_HISTORY_POINTS :]

    def equity(self, prices: dict[str, float]) -> float:
        # Opening a position moves its notional out of self.cash, so it has
        # to be added back here alongside unrealized PnL, or equity looks
        # like it drops by the full trade size the instant a position opens.
        notional = sum(pos.size_usd for pos in self.positions.values())
        unrealized = sum(
            pos.unrealized_pnl(prices[asset])
            for asset, pos in self.positions.items()
            if asset in prices
        )
        return self.cash + notional + unrealized

    def drawdown_pct(self, prices: dict[str, float]) -> float:
        equity = self.equity(prices)
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - equity) / self.peak_equity * 100)

    def seconds_since_last_trade(self, asset: str, now: int | None = None) -> float:
        now = now if now is not None else int(time.time())
        last = self.last_trade_at.get(asset)
        if last is None:
            return float("inf")
        return now - last

    def open_position(self, asset: str, direction: int, size_usd: float, price: float, fee_usd: float, action: str, now: int | None = None) -> None:
        now = now if now is not None else int(time.time())
        existing = self.positions.get(asset)
        if existing is not None and existing.direction != direction:
            self.close_position(asset, price, fee_usd=0.0, now=now)

        self.cash -= size_usd + fee_usd
        self.positions[asset] = Position(asset=asset, direction=direction, size_usd=size_usd, entry_price=price, opened_at=now)
        self.last_trade_at[asset] = now
        self.trade_log.append(Trade(timestamp=now, action=action, asset=asset, direction=direction, size_usd=size_usd, price=price, fee_usd=fee_usd))

    def close_position(self, asset: str, price: float, fee_usd: float, now: int | None = None) -> float:
        now = now if now is not None else int(time.time())
        pos = self.positions.pop(asset, None)
        if pos is None:
            return 0.0
        pnl = pos.unrealized_pnl(price)
        self.cash += pos.size_usd + pnl - fee_usd
        self.last_trade_at[asset] = now
        self.trade_log.append(Trade(timestamp=now, action=f"{asset}_CLOSE", asset=asset, direction=pos.direction, size_usd=pos.size_usd, price=price, fee_usd=fee_usd, pnl_usd=pnl))
        return pnl

    def to_dict(self) -> dict:
        return {
            "cash": self.cash,
            "starting_equity": self.starting_equity,
            "peak_equity": self.peak_equity,
            "positions": {asset: asdict(pos) for asset, pos in self.positions.items()},
            "trade_log": [asdict(t) for t in self.trade_log],
            "last_trade_at": self.last_trade_at,
            "equity_history": self.equity_history,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio":
        portfolio = cls(
            cash=data["cash"],
            starting_equity=data["starting_equity"],
            peak_equity=data["peak_equity"],
        )
        portfolio.positions = {asset: Position(**pos) for asset, pos in data["positions"].items()}
        portfolio.trade_log = [Trade(**t) for t in data["trade_log"]]
        portfolio.last_trade_at = data["last_trade_at"]
        portfolio.equity_history = data.get("equity_history", [])
        return portfolio

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Portfolio":
        with open(path) as f:
            return cls.from_dict(json.load(f))
