"""Market regime / reasoning layer for the Jev bot.

Meant to run on its own slow cadence (config.REGIME_INTERVAL, ~5-10 min) -
regimes don't meaningfully change every decision cycle. Deterministic
statistics for now; the interface (assess() -> JevRegime) is stable so an
LLM/reasoning model can later replace the classification body without
touching any caller.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from trading_hub import config
from trading_hub.feature_engine import Features

REGIMES = [
    "TREND_UP", "TREND_DOWN", "MEAN_REVERTING", "RANGE_BOUND",
    "HIGH_VOLATILITY", "LOW_VOLATILITY", "BREAKOUT", "LIQUIDATION_EVENT",
    "ABNORMAL_SPREAD", "RISK_OFF", "UNCERTAIN",
]

ALLOWED_STRATEGIES_BY_REGIME: dict[str, list[str]] = {
    "TREND_UP": ["momentum", "breakout"],
    "TREND_DOWN": ["momentum", "breakout"],
    "MEAN_REVERTING": ["mean_reversion"],
    "RANGE_BOUND": ["mean_reversion"],
    "HIGH_VOLATILITY": ["breakout"],
    "LOW_VOLATILITY": ["mean_reversion", "momentum"],
    "BREAKOUT": ["breakout", "momentum"],
    "LIQUIDATION_EVENT": [],
    "ABNORMAL_SPREAD": [],
    "RISK_OFF": [],
    "UNCERTAIN": ["mean_reversion"],
}

RISK_MULTIPLIER_BY_REGIME: dict[str, float] = {
    "TREND_UP": 1.0,
    "TREND_DOWN": 1.0,
    "MEAN_REVERTING": 0.9,
    "RANGE_BOUND": 0.8,
    "HIGH_VOLATILITY": 0.6,
    "LOW_VOLATILITY": 1.0,
    "BREAKOUT": 0.8,
    "LIQUIDATION_EVENT": 0.0,
    "ABNORMAL_SPREAD": 0.0,
    "RISK_OFF": 0.3,
    "UNCERTAIN": 0.5,
}


@dataclass
class JevRegime:
    regime: str
    confidence: float
    allowed_assets: list[str]
    disabled_assets: list[str]
    allowed_strategies: list[str]
    risk_multiplier: float
    max_position_multiplier: float
    reasoning_summary: str
    timestamp: float


def _classify_asset(f: Features, spread_bps: float | None) -> tuple[str, float]:
    """Classify a single asset's regime from deterministic statistics.
    Returns (regime, confidence)."""
    if spread_bps is not None and spread_bps > config.EXTREME_SPREAD_BPS * 2:
        return "ABNORMAL_SPREAD", 0.9

    if f.volatility > 0.02:
        # A very large volatility spike alongside a sharp move looks like a
        # liquidation cascade rather than an orderly trend.
        if abs(f.momentum) > 3.0:
            return "LIQUIDATION_EVENT", 0.7
        return "HIGH_VOLATILITY", 0.75

    # A clear, consistent trend takes priority over a low-volatility label -
    # a smooth steady move IS low-noise, that's what makes it a good trend.
    trend_pct = (f.sma_short - f.sma_long) / f.sma_long * 100 if f.sma_long else 0.0
    if f.last_price > f.high_20 and f.volume_change > 10:
        return "BREAKOUT", 0.7
    if trend_pct > 0.15 and f.momentum > 0.1:
        return "TREND_UP", 0.75
    if trend_pct < -0.15 and f.momentum < -0.1:
        return "TREND_DOWN", 0.75

    if f.volatility < 0.002:
        return "LOW_VOLATILITY", 0.7

    if f.rsi >= 65 or f.rsi <= 35:
        return "MEAN_REVERTING", 0.6
    if abs(trend_pct) < 0.1:
        return "RANGE_BOUND", 0.6

    return "UNCERTAIN", 0.4


def assess(features_by_asset: dict[str, Features], spreads_bps: dict[str, float] | None = None) -> JevRegime:
    """Aggregate per-asset regime votes into one overall market regime."""
    spreads_bps = spreads_bps or {}
    if not features_by_asset:
        return JevRegime(
            regime="UNCERTAIN", confidence=0.0, allowed_assets=[], disabled_assets=list(config.JEV_ASSETS),
            allowed_strategies=[], risk_multiplier=0.0, max_position_multiplier=0.0,
            reasoning_summary="no market data available", timestamp=time.time(),
        )

    votes: dict[str, int] = {}
    per_asset: dict[str, tuple[str, float]] = {}
    for asset, f in features_by_asset.items():
        regime, confidence = _classify_asset(f, spreads_bps.get(asset))
        per_asset[asset] = (regime, confidence)
        votes[regime] = votes.get(regime, 0) + 1

    overall_regime = max(votes, key=votes.get)
    overall_confidence = votes[overall_regime] / len(features_by_asset)

    disabled_assets = [a for a, (r, _) in per_asset.items() if r in ("LIQUIDATION_EVENT", "ABNORMAL_SPREAD")]
    allowed_assets = [a for a in features_by_asset if a not in disabled_assets]
    reasoning_summary = f"{overall_regime} ({votes[overall_regime]}/{len(features_by_asset)} assets): " + ", ".join(
        f"{a}={r}" for a, (r, _) in sorted(per_asset.items())
    )

    return JevRegime(
        regime=overall_regime,
        confidence=overall_confidence,
        allowed_assets=allowed_assets,
        disabled_assets=disabled_assets,
        allowed_strategies=ALLOWED_STRATEGIES_BY_REGIME.get(overall_regime, []),
        risk_multiplier=RISK_MULTIPLIER_BY_REGIME.get(overall_regime, 0.5),
        max_position_multiplier=RISK_MULTIPLIER_BY_REGIME.get(overall_regime, 0.5),
        reasoning_summary=reasoning_summary,
        timestamp=time.time(),
    )
