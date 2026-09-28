"""Hard risk engine: the non-negotiable gate between a JEV decision and execution.

Every rule here is a hard stop, not a suggestion. If any check fails the
trade is rejected outright and the pipeline falls back to HOLD.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_hub import config
from trading_hub.jev_engine import Decision
from trading_hub.portfolio import Portfolio


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
    size_usd: float = 0.0


def evaluate(
    decision: Decision,
    portfolio: Portfolio,
    prices: dict[str, float],
    spread_fraction: float,
    now: int | None = None,
) -> RiskVerdict:
    if decision.action == "HOLD" or decision.asset is None:
        return RiskVerdict(approved=False, reason="no actionable decision (HOLD)")

    if decision.confidence <= config.MIN_CONFIDENCE_PCT:
        return RiskVerdict(
            approved=False,
            reason=f"confidence {decision.confidence:.1f}% <= required {config.MIN_CONFIDENCE_PCT}%",
        )

    drawdown = portfolio.drawdown_pct(prices)
    if drawdown >= config.MAX_DRAWDOWN_PCT:
        return RiskVerdict(
            approved=False,
            reason=f"drawdown {drawdown:.2f}% >= max {config.MAX_DRAWDOWN_PCT}%",
        )

    cooldown_remaining = config.COOLDOWN_SECONDS - portfolio.seconds_since_last_trade(decision.asset, now=now)
    if cooldown_remaining > 0:
        return RiskVerdict(
            approved=False,
            reason=f"{decision.asset} in cooldown for {cooldown_remaining:.0f}s more",
        )

    size_usd = min(config.MAX_TRADE_USD, portfolio.cash)
    if size_usd <= 0:
        return RiskVerdict(approved=False, reason="insufficient cash for a trade")

    if not config.ALLOW_LEVERAGE and size_usd > portfolio.cash:
        return RiskVerdict(approved=False, reason="trade size would require leverage")

    est_fee_fraction = config.TAKER_FEE_FRACTION * 2 + spread_fraction  # round trip
    edge_fraction = (decision.confidence - 50) / 50  # 0 at 50% confidence, 1 at 100%
    if edge_fraction <= 0 or est_fee_fraction / max(edge_fraction, 1e-9) > config.MAX_FEE_SPREAD_FRACTION:
        return RiskVerdict(
            approved=False,
            reason=(
                f"fee/spread cost {est_fee_fraction * 100:.3f}% too large relative to edge "
                f"({edge_fraction * 100:.1f}%)"
            ),
        )

    return RiskVerdict(approved=True, reason="passed all hard risk checks", size_usd=size_usd)
