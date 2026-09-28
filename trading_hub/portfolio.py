"""Paper trading portfolio: cash, open positions, and equity/drawdown tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

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

    def equity(self, prices: dict[str, float]) -> float:
        unrealized = sum(
            pos.unrealized_pnl(prices[asset])
            for asset, pos in self.positions.items()
            if asset in prices
        )
        return self.cash + unrealized

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
