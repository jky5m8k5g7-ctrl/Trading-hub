"""Paper execution: applies a risk-approved decision to the paper portfolio."""

from __future__ import annotations

from dataclasses import dataclass

from trading_hub import config
from trading_hub.jev_engine import Decision
from trading_hub.portfolio import Portfolio
from trading_hub.risk_engine import RiskVerdict


@dataclass
class ExecutionResult:
    executed: bool
    reason: str
    trade_size_usd: float = 0.0


def execute(decision: Decision, risk_verdict: RiskVerdict, portfolio: Portfolio, price: float, now: int | None = None) -> ExecutionResult:
    if not risk_verdict.approved or decision.asset is None:
        return ExecutionResult(executed=False, reason=risk_verdict.reason)

    fee_usd = risk_verdict.size_usd * config.TAKER_FEE_FRACTION
    portfolio.open_position(
        asset=decision.asset,
        direction=decision.direction,
        size_usd=risk_verdict.size_usd,
        price=price,
        fee_usd=fee_usd,
        action=decision.action,
        now=now,
    )
    return ExecutionResult(executed=True, reason="order filled (paper)", trade_size_usd=risk_verdict.size_usd)
