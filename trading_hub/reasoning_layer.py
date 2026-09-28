"""Reasoning layer: classifies market regime / strategy context ahead of the JEV decision.

Uses TypeSafe AI's classification API (typesafe-sdk) to label the current
cross-asset regime from aggregated features when `TYPESAFE_API_KEY` is
configured. Falls back to a local volatility/momentum heuristic when the
key is missing or the API call fails, so a reasoning-layer outage never
stalls the trading loop.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from trading_hub.feature_engine import Features

log = logging.getLogger(__name__)

REGIMES = ["trending_up", "trending_down", "ranging", "volatile"]

HEURISTIC_VOLATILITY_THRESHOLD = 0.01
HEURISTIC_MOMENTUM_THRESHOLD_PCT = 0.3

# Multiplier applied to a JEV verdict's confidence when its direction
# fights the assessed regime (e.g. going short while the market is
# trending up). Directions absent from a regime's map are left untouched.
REGIME_DIRECTION_PENALTY: dict[str, dict[str, float]] = {
    "trending_up": {"SHORT": 0.85},
    "trending_down": {"LONG": 0.85},
    "ranging": {"LONG": 0.9, "SHORT": 0.9},
    "volatile": {"LONG": 0.8, "SHORT": 0.8},
}


@dataclass
class RegimeAssessment:
    regime: str
    rationale: str
    source: str  # "typesafe" or "heuristic"


def _heuristic_regime(features_by_asset: dict[str, Features]) -> RegimeAssessment:
    if not features_by_asset:
        return RegimeAssessment(regime="ranging", rationale="no feature data available", source="heuristic")

    avg_momentum = sum(f.momentum for f in features_by_asset.values()) / len(features_by_asset)
    avg_volatility = sum(f.volatility for f in features_by_asset.values()) / len(features_by_asset)

    if avg_volatility > HEURISTIC_VOLATILITY_THRESHOLD:
        regime = "volatile"
    elif avg_momentum > HEURISTIC_MOMENTUM_THRESHOLD_PCT:
        regime = "trending_up"
    elif avg_momentum < -HEURISTIC_MOMENTUM_THRESHOLD_PCT:
        regime = "trending_down"
    else:
        regime = "ranging"

    rationale = f"avg_momentum={avg_momentum:.2f}% avg_volatility={avg_volatility:.4f}"
    return RegimeAssessment(regime=regime, rationale=rationale, source="heuristic")


def _build_state(features_by_asset: dict[str, Features]) -> dict:
    return {
        asset: {
            "last_price": f.last_price,
            "momentum_pct": round(f.momentum, 3),
            "rsi": round(f.rsi, 1),
            "volatility": round(f.volatility, 5),
            "volume_change_pct": round(f.volume_change, 1),
        }
        for asset, f in features_by_asset.items()
    }


def assess_regime(features_by_asset: dict[str, Features]) -> RegimeAssessment:
    """Classify the current cross-asset market regime.

    Tries the TypeSafe AI classifier when `TYPESAFE_API_KEY` is set;
    otherwise (or on any failure) falls back to `_heuristic_regime`.
    """
    if not features_by_asset or not os.environ.get("TYPESAFE_API_KEY"):
        return _heuristic_regime(features_by_asset)

    try:
        from typesafe_sdk import Choice, TypeSafeClient

        with TypeSafeClient() as client:
            response = client.system_one(
                state=_build_state(features_by_asset),
                questions={
                    "regime": Choice(
                        instructions=(
                            "Given per-asset momentum, RSI, volatility, and volume change, "
                            "classify the overall crypto market regime right now."
                        ),
                        criteria={
                            "trending_up": "Broad, consistent upward momentum across assets.",
                            "trending_down": "Broad, consistent downward momentum across assets.",
                            "ranging": "Low momentum, prices oscillating without a clear trend.",
                            "volatile": "High volatility or erratic moves without a clear direction.",
                        },
                    ),
                },
            )
        choice = response.choices["regime"]
        regime = choice.choice if choice.choice in REGIMES else "ranging"
        rationale = getattr(choice, "rationale", None) or "typesafe classification"
        return RegimeAssessment(regime=regime, rationale=rationale, source="typesafe")
    except Exception:
        log.exception("TypeSafe regime classification failed, falling back to heuristic")
        return _heuristic_regime(features_by_asset)


def apply_regime_adjustment(confidence: float, direction_label: str, assessment: RegimeAssessment) -> float:
    """Dampen confidence when the JEV direction fights the assessed regime."""
    penalty = REGIME_DIRECTION_PENALTY.get(assessment.regime, {}).get(direction_label)
    if penalty is None:
        return confidence
    return confidence * penalty
