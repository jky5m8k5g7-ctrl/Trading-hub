"""Deterministic risk governor for the Jev AI Decision Bot.

This is the ONLY code that can approve a trade Jev proposes. Jev never has
direct control over position size, leverage, stops, exposure, or whether
trading continues after losses - every one of those is computed and
enforced here in ordinary Python, independent of any AI call, per the
architecture principle: Jev chooses the potential trade; this module
decides whether it is allowed to execute.

Checked in order; the first failing check wins:
1. Hard halt / loss-streak pause / feed disconnected (can't safely evaluate)
2. Jev didn't actually answer (unavailable/timeout/invalid) -> HOLD
3. No market data / regime disables this asset / stale data / extreme spread
4. Jev's own decision-quality gates: confidence, probability gap
5. Position/exposure state: already holding it, opposing position, max
   simultaneous positions, max total exposure
6. Daily drawdown circuit breakers (soft de-risks, hard halts)
7. Position sizing and volatility-adjusted stop distance
8. Expected edge vs. round-trip trading cost
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from trading_hub import config
from trading_hub.jev_decision import JevDecision
from trading_hub.jev_state import JevState
from trading_hub.live_feed import LiveFeed
from trading_hub.portfolio import Portfolio


@dataclass
class RiskVerdict:
    approved: bool
    outcome: str  # "APPROVED", "REJECTED", or "HOLD"
    reason: str
    asset: str | None = None
    side: str | None = None
    notional_usd: float = 0.0
    stop_price: float | None = None
    take_profit_price: float | None = None
    stop_distance_pct: float | None = None
    size_multiplier: float = 1.0


def parse_action(action: str) -> tuple[str | None, str | None]:
    if action == "HOLD" or "_" not in action:
        return None, None
    asset, side = action.rsplit("_", 1)
    return asset, side


def estimate_round_trip_cost_fraction(spread_bps: float) -> float:
    """Round-trip cost as a fraction of notional: two-way taker fee + the
    spread you'd cross entering and (worst case) exiting."""
    return config.TAKER_FEE_FRACTION * 2 + spread_bps / 10_000


def compute_stop_distance_pct(volatility: float) -> float:
    """Volatility-adjusted stop distance, bounded to [MIN_STOP_PCT,
    MAX_STOP_PCT]. `volatility` is feature_engine's stdev-of-returns proxy
    for short-term ATR. Never a single fixed percentage."""
    raw = config.ATR_STOP_MULTIPLIER * volatility
    return max(config.MIN_STOP_PCT, min(raw, config.MAX_STOP_PCT))


def compute_position_size(equity: float, stop_distance_pct: float) -> float:
    """Position notional in USD: the smaller of the risk-budget-implied size
    (risk_budget / stop_distance) and the hard $30 / 15%-of-equity cap."""
    if stop_distance_pct <= 0:
        return 0.0
    risk_budget = equity * config.MAX_RISK_PER_TRADE_PCT
    risk_based_notional = risk_budget / stop_distance_pct
    hard_cap = min(config.DEFAULT_POSITION_NOTIONAL, equity * config.MAX_POSITION_PERCENT)
    return max(0.0, min(risk_based_notional, hard_cap))


def expected_edge_fraction(winner_probability: float, stop_distance_pct: float) -> float:
    """Expected value of the trade as a fraction of price, given Jev's own
    win probability, the volatility-adjusted stop, and the configured R
    multiple for the take-profit target: p*reward - (1-p)*risk."""
    reward = stop_distance_pct * config.TAKE_PROFIT_R_MULTIPLE
    risk = stop_distance_pct
    return winner_probability * reward - (1 - winner_probability) * risk


def evaluate(decision: JevDecision, state: JevState, portfolio: Portfolio, live_feed: LiveFeed, now: float | None = None) -> RiskVerdict:
    now = now if now is not None else time.time()

    if portfolio.halted:
        return RiskVerdict(False, "HOLD", f"bot halted: {portfolio.halted_reason}")

    if portfolio.is_paused(int(now)):
        remaining = portfolio.trading_paused_until - now
        return RiskVerdict(False, "HOLD", f"loss-streak cooldown, {remaining:.0f}s remaining")

    if not live_feed.is_connected():
        return RiskVerdict(False, "HOLD", "WebSocket disconnected - no new trades")

    if decision.source != "typesafe":
        return RiskVerdict(False, "HOLD", f"Jev unavailable: {decision.reason}")

    asset, side = parse_action(decision.action)
    if asset is None:
        return RiskVerdict(False, "HOLD", "Jev decided HOLD")

    asset_state = state.assets.get(asset)
    if asset_state is None:
        return RiskVerdict(False, "HOLD", f"no market data for {asset}")

    if asset not in state.allowed_assets:
        return RiskVerdict(False, "REJECTED", f"{asset} disabled by current regime ({state.regime})")

    if asset_state.data_age_seconds > config.DATA_STALE_THRESHOLD_SECONDS:
        return RiskVerdict(False, "HOLD", f"stale data for {asset} ({asset_state.data_age_seconds:.1f}s old)")

    if asset_state.spread_bps > config.EXTREME_SPREAD_BPS:
        return RiskVerdict(False, "REJECTED", f"extreme spread {asset_state.spread_bps:.1f}bps on {asset}")

    if decision.winner_probability < config.MIN_JEV_CONFIDENCE:
        return RiskVerdict(False, "REJECTED", f"confidence {decision.winner_probability:.2f} < {config.MIN_JEV_CONFIDENCE}")

    if decision.probability_gap < config.MIN_PROBABILITY_GAP:
        return RiskVerdict(False, "REJECTED", f"probability gap {decision.probability_gap:.2f} < {config.MIN_PROBABILITY_GAP}")

    existing = portfolio.positions.get(asset)
    if existing is not None:
        existing_side = "LONG" if existing.direction == 1 else "SHORT"
        if existing_side == side:
            return RiskVerdict(False, "HOLD", f"{asset} {side} position already open")
        return RiskVerdict(False, "HOLD", f"{asset} has an opposing {existing_side} position open - close it first")

    if len(portfolio.positions) >= config.MAX_SIMULTANEOUS_POSITIONS:
        return RiskVerdict(False, "REJECTED", f"max simultaneous positions ({config.MAX_SIMULTANEOUS_POSITIONS}) reached")

    prices = {a: s.last_price for a, s in state.assets.items()}
    equity = portfolio.equity(prices)
    if equity <= 0:
        return RiskVerdict(False, "HOLD", "non-positive equity")

    open_notional = sum(p.size_usd for p in portfolio.positions.values())
    if (open_notional + config.DEFAULT_POSITION_NOTIONAL) / equity > config.MAX_TOTAL_EXPOSURE_PERCENT:
        return RiskVerdict(False, "REJECTED", f"would exceed max total exposure {config.MAX_TOTAL_EXPOSURE_PERCENT:.0%}")

    drawdown_pct = portfolio.drawdown_pct(prices)
    if drawdown_pct >= config.DAILY_HARD_DRAWDOWN_PCT:
        portfolio.halt(f"daily drawdown {drawdown_pct:.2f}% >= hard limit {config.DAILY_HARD_DRAWDOWN_PCT}%")
        return RiskVerdict(False, "HOLD", portfolio.halted_reason)

    size_multiplier = config.DAILY_SOFT_DRAWDOWN_SIZE_MULTIPLIER if drawdown_pct >= config.DAILY_SOFT_DRAWDOWN_PCT else 1.0

    stop_distance_pct = compute_stop_distance_pct(asset_state.volatility)
    notional = compute_position_size(equity, stop_distance_pct) * size_multiplier
    if notional <= 0:
        return RiskVerdict(False, "REJECTED", "computed position size is zero")

    cost_fraction = estimate_round_trip_cost_fraction(asset_state.spread_bps)
    edge_fraction = expected_edge_fraction(decision.winner_probability, stop_distance_pct)
    if edge_fraction < config.EDGE_COST_MULTIPLIER * cost_fraction:
        return RiskVerdict(
            False, "REJECTED",
            f"expected edge {edge_fraction:.4f} < {config.EDGE_COST_MULTIPLIER}x round-trip cost {cost_fraction:.4f}",
        )

    entry_price = asset_state.last_price
    if side == "LONG":
        stop_price = entry_price * (1 - stop_distance_pct)
        take_profit_price = entry_price * (1 + stop_distance_pct * config.TAKE_PROFIT_R_MULTIPLE)
    else:
        stop_price = entry_price * (1 + stop_distance_pct)
        take_profit_price = entry_price * (1 - stop_distance_pct * config.TAKE_PROFIT_R_MULTIPLE)

    return RiskVerdict(
        approved=True,
        outcome="APPROVED",
        reason="passed all deterministic risk checks",
        asset=asset,
        side=side,
        notional_usd=notional,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        stop_distance_pct=stop_distance_pct,
        size_multiplier=size_multiplier,
    )
