"""Alternative decision strategies, run as separate paper bots alongside the
original JEV trend/momentum ensemble (jev_engine.py). Each strategy consumes
the same Features computed once per cycle and produces a jev_engine.Decision
the same way, so they all pass through the identical hard risk engine,
portfolio, and dashboard machinery - only the signal differs.
"""

from __future__ import annotations

from trading_hub.feature_engine import Features
from trading_hub.jev_engine import LONG, NEUTRAL, SHORT, AssetVerdict, Decision, pick_best_action

MAX_CONFIDENCE = 99.0

# --- Mean reversion: fade extreme deviation from the long SMA, confirmed by RSI ---
MEANREV_DEVIATION_PCT = 1.5
MEANREV_RSI_OVERBOUGHT = 70.0
MEANREV_RSI_OVERSOLD = 30.0


def _mean_reversion_verdict(f: Features) -> AssetVerdict:
    votes = {"deviation_pct": 0, "rsi": f.rsi}
    if f.sma_long == 0:
        return AssetVerdict(asset=f.asset, direction=NEUTRAL, confidence=0.0, votes=votes)

    deviation_pct = (f.last_price - f.sma_long) / f.sma_long * 100
    votes["deviation_pct"] = round(deviation_pct, 3)

    if deviation_pct <= -MEANREV_DEVIATION_PCT and f.rsi <= MEANREV_RSI_OVERSOLD:
        direction = LONG
    elif deviation_pct >= MEANREV_DEVIATION_PCT and f.rsi >= MEANREV_RSI_OVERBOUGHT:
        direction = SHORT
    else:
        return AssetVerdict(asset=f.asset, direction=NEUTRAL, confidence=0.0, votes=votes)

    magnitude = min(abs(deviation_pct) / MEANREV_DEVIATION_PCT, 2.0) / 2.0
    confidence = min(50 + magnitude * 50, MAX_CONFIDENCE)
    return AssetVerdict(asset=f.asset, direction=direction, confidence=confidence, votes=votes)


def mean_reversion_decide(features_by_asset: dict[str, Features]) -> Decision:
    verdicts = {asset: _mean_reversion_verdict(f) for asset, f in features_by_asset.items()}
    return pick_best_action(verdicts)


# --- Breakout: trade a decisive break of the prior N-candle high/low channel ---
BREAKOUT_THRESHOLD_PCT = 0.3
BREAKOUT_VOLUME_CONFIRM_PCT = 10.0
BREAKOUT_VOLUME_BONUS = 5.0


def _breakout_verdict(f: Features) -> AssetVerdict:
    votes = {"high_20": f.high_20, "low_20": f.low_20}
    if f.high_20 <= 0 or f.low_20 <= 0:
        return AssetVerdict(asset=f.asset, direction=NEUTRAL, confidence=0.0, votes=votes)

    pct_above = (f.last_price - f.high_20) / f.high_20 * 100
    pct_below = (f.low_20 - f.last_price) / f.low_20 * 100

    if pct_above > 0:
        direction, magnitude = LONG, pct_above
    elif pct_below > 0:
        direction, magnitude = SHORT, pct_below
    else:
        return AssetVerdict(asset=f.asset, direction=NEUTRAL, confidence=0.0, votes=votes)

    confidence = min(50 + min(magnitude / BREAKOUT_THRESHOLD_PCT, 1.0) * 50, MAX_CONFIDENCE)
    if f.volume_change > BREAKOUT_VOLUME_CONFIRM_PCT:
        confidence = min(confidence + BREAKOUT_VOLUME_BONUS, MAX_CONFIDENCE)
    return AssetVerdict(asset=f.asset, direction=direction, confidence=confidence, votes=votes)


def breakout_decide(features_by_asset: dict[str, Features]) -> Decision:
    verdicts = {asset: _breakout_verdict(f) for asset, f in features_by_asset.items()}
    return pick_best_action(verdicts)
