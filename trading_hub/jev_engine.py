"""JEV (Joint Ensemble Vote) decision layer.

Runs a small ensemble of independent signal voters per asset (trend,
momentum, RSI mean-reversion), combines their votes into a direction and
a confidence score, then picks the single highest-confidence action
across all assets. Volume acts as a confirmation booster rather than a
voter of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_hub import config
from trading_hub.feature_engine import Features

LONG = 1
SHORT = -1
NEUTRAL = 0

TREND_DEADBAND_PCT = 0.05
MOMENTUM_DEADBAND_PCT = 0.10
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
RSI_REVERSAL_RISK_PENALTY = 15.0
RSI_REVERSION_CONFIDENCE = 75.0
VOLUME_CONFIRM_PCT = 10.0
VOLUME_CONFIRM_BONUS = 5.0
MAX_CONFIDENCE = 99.0
MIN_CONFIDENCE = 0.0


@dataclass
class AssetVerdict:
    asset: str
    direction: int  # LONG / SHORT / NEUTRAL
    confidence: float
    votes: dict[str, int]


@dataclass
class Decision:
    action: str  # one of config.ACTIONS
    confidence: float
    asset: str | None
    direction: int
    verdicts: dict[str, AssetVerdict]


def _trend_vote(f: Features) -> int:
    if f.sma_long == 0:
        return NEUTRAL
    diff_pct = (f.sma_short - f.sma_long) / f.sma_long * 100
    if diff_pct > TREND_DEADBAND_PCT:
        return LONG
    if diff_pct < -TREND_DEADBAND_PCT:
        return SHORT
    return NEUTRAL


def _momentum_vote(f: Features) -> int:
    if f.momentum > MOMENTUM_DEADBAND_PCT:
        return LONG
    if f.momentum < -MOMENTUM_DEADBAND_PCT:
        return SHORT
    return NEUTRAL


def _rsi_vote(f: Features) -> int:
    if f.rsi >= RSI_OVERBOUGHT:
        return SHORT  # overbought -> expect reversion down
    if f.rsi <= RSI_OVERSOLD:
        return LONG  # oversold -> expect reversion up
    return NEUTRAL


def evaluate_asset(f: Features) -> AssetVerdict:
    """Trend and momentum drive direction on a trending market; when both are
    flat, RSI mean-reversion takes over for a range-bound market. RSI also
    acts as a confidence penalty on a trending call when it signals elevated
    reversal risk (e.g. overbought while going long).
    """
    trend_vote = _trend_vote(f)
    momentum_vote = _momentum_vote(f)
    rsi_vote = _rsi_vote(f)
    votes = {"trend": trend_vote, "momentum": momentum_vote, "rsi": rsi_vote}

    core_total = trend_vote + momentum_vote
    if core_total != 0:
        direction = LONG if core_total > 0 else SHORT
        agreement = abs(core_total) / 2
        confidence = 50 + agreement * 50

        reversal_risk = (direction == LONG and f.rsi >= RSI_OVERBOUGHT) or (
            direction == SHORT and f.rsi <= RSI_OVERSOLD
        )
        if reversal_risk:
            confidence -= RSI_REVERSAL_RISK_PENALTY
    elif rsi_vote != NEUTRAL:
        direction = rsi_vote
        confidence = RSI_REVERSION_CONFIDENCE
    else:
        return AssetVerdict(asset=f.asset, direction=NEUTRAL, confidence=0.0, votes=votes)

    volume_matches_direction = (
        (direction == LONG and f.volume_change > VOLUME_CONFIRM_PCT)
        or (direction == SHORT and f.volume_change > VOLUME_CONFIRM_PCT and f.momentum < 0)
    )
    if volume_matches_direction:
        confidence += VOLUME_CONFIRM_BONUS

    confidence = max(MIN_CONFIDENCE, min(confidence, MAX_CONFIDENCE))
    return AssetVerdict(asset=f.asset, direction=direction, confidence=confidence, votes=votes)


def decide(features_by_asset: dict[str, Features]) -> Decision:
    """Evaluate every asset and pick the single best action, or HOLD."""
    verdicts = {asset: evaluate_asset(f) for asset, f in features_by_asset.items()}

    best: AssetVerdict | None = None
    for verdict in verdicts.values():
        if verdict.direction == NEUTRAL:
            continue
        if best is None or verdict.confidence > best.confidence:
            best = verdict

    if best is None or best.confidence < config.MIN_CONFIDENCE_PCT:
        confidence = best.confidence if best else 0.0
        return Decision(action="HOLD", confidence=confidence, asset=None, direction=NEUTRAL, verdicts=verdicts)

    suffix = "LONG" if best.direction == LONG else "SHORT"
    action = f"{best.asset}_{suffix}"
    return Decision(action=action, confidence=best.confidence, asset=best.asset, direction=best.direction, verdicts=verdicts)
