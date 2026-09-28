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
    # Optional - only set by bots that use volatility-adjusted risk management
    # (the Jev bot). Left None for the simpler legacy strategies.
    stop_price: float | None = None
    take_profit_price: float | None = None
    time_limit_at: int | None = None
    trailing_armed: bool = False
    trailing_stop_price: float | None = None
    peak_favorable_price: float | None = None  # for trailing-stop and MFE tracking
    trough_adverse_price: float | None = None  # for MAE tracking
    maker_or_taker: str | None = None
    regime: str | None = None
    jev_confidence: float | None = None

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
    slippage_usd: float = 0.0
    holding_time_seconds: float | None = None
    mfe_usd: float | None = None
    mae_usd: float | None = None
    exit_reason: str | None = None
    maker_or_taker: str | None = None
    regime: str | None = None
    jev_confidence: float | None = None


@dataclass
class Portfolio:
    cash: float = config.STARTING_CASH_USD
    starting_equity: float = config.STARTING_CASH_USD
    peak_equity: float = config.STARTING_CASH_USD
    positions: dict[str, Position] = field(default_factory=dict)
    trade_log: list[Trade] = field(default_factory=list)
    last_trade_at: dict[str, int] = field(default_factory=dict)
    equity_history: list[dict] = field(default_factory=list)
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    trading_paused_until: int = 0
    halted: bool = False
    halted_reason: str = ""

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

    def open_position(
        self, asset: str, direction: int, size_usd: float, price: float, fee_usd: float, action: str, now: int | None = None,
        stop_price: float | None = None, take_profit_price: float | None = None, time_limit_at: int | None = None,
        maker_or_taker: str | None = None, regime: str | None = None, jev_confidence: float | None = None,
    ) -> None:
        now = now if now is not None else int(time.time())
        existing = self.positions.get(asset)
        if existing is not None and existing.direction != direction:
            self.close_position(asset, price, fee_usd=0.0, now=now)

        self.cash -= size_usd + fee_usd
        self.positions[asset] = Position(
            asset=asset, direction=direction, size_usd=size_usd, entry_price=price, opened_at=now,
            stop_price=stop_price, take_profit_price=take_profit_price, time_limit_at=time_limit_at,
            peak_favorable_price=price, trough_adverse_price=price,
            maker_or_taker=maker_or_taker, regime=regime, jev_confidence=jev_confidence,
        )
        self.last_trade_at[asset] = now
        self.trade_log.append(Trade(
            timestamp=now, action=action, asset=asset, direction=direction, size_usd=size_usd, price=price, fee_usd=fee_usd,
            maker_or_taker=maker_or_taker, regime=regime, jev_confidence=jev_confidence,
        ))

    def update_excursion(self, asset: str, current_price: float) -> None:
        """Track the best/worst price seen while a position is open, for MFE/MAE
        and to drive a trailing stop. Call this every cycle a price is known."""
        pos = self.positions.get(asset)
        if pos is None:
            return
        if pos.direction == 1:
            pos.peak_favorable_price = max(pos.peak_favorable_price or current_price, current_price)
            pos.trough_adverse_price = min(pos.trough_adverse_price or current_price, current_price)
        else:
            pos.peak_favorable_price = min(pos.peak_favorable_price or current_price, current_price)
            pos.trough_adverse_price = max(pos.trough_adverse_price or current_price, current_price)

    def close_position(
        self, asset: str, price: float, fee_usd: float, now: int | None = None,
        exit_reason: str | None = None, slippage_usd: float = 0.0,
    ) -> float:
        now = now if now is not None else int(time.time())
        pos = self.positions.pop(asset, None)
        if pos is None:
            return 0.0
        pnl = pos.unrealized_pnl(price)
        self.cash += pos.size_usd + pnl - fee_usd
        self.last_trade_at[asset] = now

        mfe_usd = pos.unrealized_pnl(pos.peak_favorable_price) if pos.peak_favorable_price is not None else None
        mae_usd = pos.unrealized_pnl(pos.trough_adverse_price) if pos.trough_adverse_price is not None else None

        self.trade_log.append(Trade(
            timestamp=now, action=f"{asset}_CLOSE", asset=asset, direction=pos.direction, size_usd=pos.size_usd,
            price=price, fee_usd=fee_usd, pnl_usd=pnl, slippage_usd=slippage_usd,
            holding_time_seconds=now - pos.opened_at, mfe_usd=mfe_usd, mae_usd=mae_usd,
            exit_reason=exit_reason, maker_or_taker=pos.maker_or_taker, regime=pos.regime, jev_confidence=pos.jev_confidence,
        ))
        self.register_trade_result(pnl, now=now)
        return pnl

    # --- Circuit breakers: consecutive losses, pause, halt. Generic to any
    # bot; risk_governor.py is the only caller that reads/acts on these for
    # the Jev bot, but they're harmless no-ops for the legacy strategies. ---

    def register_trade_result(self, net_pnl: float, now: int | None = None) -> None:
        now = now if now is not None else int(time.time())
        if net_pnl < 0:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            if self.consecutive_losses >= config.LOSS_STREAK_LIMIT:
                self.trading_paused_until = now + config.LOSS_COOLDOWN_SECONDS
        else:
            self.consecutive_wins += 1
            self.consecutive_losses = 0

    def is_paused(self, now: int | None = None) -> bool:
        now = now if now is not None else int(time.time())
        return now < self.trading_paused_until

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halted_reason = reason

    def reset_halt(self) -> None:
        """Manual reset - the only way to resume after a hard-drawdown halt."""
        self.halted = False
        self.halted_reason = ""

    def to_dict(self) -> dict:
        return {
            "cash": self.cash,
            "starting_equity": self.starting_equity,
            "peak_equity": self.peak_equity,
            "positions": {asset: asdict(pos) for asset, pos in self.positions.items()},
            "trade_log": [asdict(t) for t in self.trade_log],
            "last_trade_at": self.last_trade_at,
            "equity_history": self.equity_history,
            "consecutive_wins": self.consecutive_wins,
            "consecutive_losses": self.consecutive_losses,
            "trading_paused_until": self.trading_paused_until,
            "halted": self.halted,
            "halted_reason": self.halted_reason,
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
        portfolio.consecutive_wins = data.get("consecutive_wins", 0)
        portfolio.consecutive_losses = data.get("consecutive_losses", 0)
        portfolio.trading_paused_until = data.get("trading_paused_until", 0)
        portfolio.halted = data.get("halted", False)
        portfolio.halted_reason = data.get("halted_reason", "")
        return portfolio

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Portfolio":
        with open(path) as f:
            return cls.from_dict(json.load(f))
