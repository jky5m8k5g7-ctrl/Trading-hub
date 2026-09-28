"""Builds the compact, structured state sent to the Jev AI Decision Bot.

Per the spec: compact, typed, useful - not hundreds of indicators. This
combines per-asset market features with account/position/performance
context so Jev sees enough to reason about a trade without being handed
raw ticks or the whole trade history.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from trading_hub.feature_engine import Features
from trading_hub.live_feed import LiveFeed, MarketSnapshot


@dataclass
class AssetState:
    asset: str
    last_price: float
    mid: float
    microprice: float
    spread_bps: float
    imbalance: float
    momentum_pct: float
    rsi: float
    volatility: float
    volume_change_pct: float
    high_20: float
    low_20: float
    returns: dict[str, float]
    data_age_seconds: float


@dataclass
class PositionState:
    asset: str
    side: str
    entry_price: float
    current_price: float
    unrealized_pnl: float
    time_in_trade_seconds: float


@dataclass
class AccountState:
    equity: float
    cash: float
    realized_pnl: float
    drawdown_pct: float
    consecutive_wins: int
    consecutive_losses: int
    open_positions: list[PositionState]
    recent_win_rate: float | None


@dataclass
class JevState:
    timestamp: float
    regime: str
    regime_confidence: float
    allowed_assets: list[str]
    assets: dict[str, AssetState]
    account: AccountState

    def to_json_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "regime": self.regime,
            "regime_confidence": self.regime_confidence,
            "allowed_assets": self.allowed_assets,
            "assets": {a: asdict(s) for a, s in self.assets.items()},
            "account": asdict(self.account),
        }


def build_asset_state(feature: Features, snapshot: MarketSnapshot | None, live_feed: LiveFeed, now: float) -> AssetState:
    if snapshot is not None:
        mid, microprice, spread_bps, imbalance = snapshot.mid, snapshot.microprice, snapshot.spread_bps, snapshot.imbalance
        age = snapshot.age_seconds(now)
    else:
        mid = microprice = feature.last_price
        spread_bps = 0.0
        imbalance = 0.0
        age = float("inf")

    return AssetState(
        asset=feature.asset,
        last_price=feature.last_price,
        mid=mid,
        microprice=microprice,
        spread_bps=spread_bps,
        imbalance=imbalance,
        momentum_pct=feature.momentum,
        rsi=feature.rsi,
        volatility=feature.volatility,
        volume_change_pct=feature.volume_change,
        high_20=feature.high_20,
        low_20=feature.low_20,
        returns=live_feed.get_returns(feature.asset, now=now),
        data_age_seconds=age,
    )
