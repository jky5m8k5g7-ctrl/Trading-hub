"""Turns raw Kraken candles into the features the JEV decision layer consumes."""

from __future__ import annotations

from dataclasses import dataclass

from trading_hub import config
from trading_hub.kraken_client import Candle


@dataclass
class Features:
    asset: str
    last_price: float
    sma_short: float
    sma_long: float
    momentum: float  # % change over the short window
    rsi: float  # 0-100
    volatility: float  # stdev of returns over the long window
    volume_change: float  # % change of recent volume vs prior volume


def _sma(closes: list[float], window: int) -> float:
    tail = closes[-window:]
    return sum(tail) / len(tail)


def _rsi(closes: list[float], window: int) -> float:
    if len(closes) < window + 1:
        window = len(closes) - 1
    if window <= 0:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - window, len(closes))]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _volatility(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] != 0]
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return variance ** 0.5


def compute_features(asset: str, candles: list[Candle]) -> Features | None:
    """Compute features for one asset. Returns None if there isn't enough history."""
    if len(candles) < max(config.LONG_WINDOW, config.RSI_WINDOW) + 1:
        return None

    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]

    sma_short = _sma(closes, config.SHORT_WINDOW)
    sma_long = _sma(closes, config.LONG_WINDOW)
    momentum = (closes[-1] - closes[-config.SHORT_WINDOW]) / closes[-config.SHORT_WINDOW] * 100
    rsi = _rsi(closes, config.RSI_WINDOW)
    volatility = _volatility(closes[-config.LONG_WINDOW:])

    recent_vol = sum(volumes[-config.SHORT_WINDOW:])
    prior_vol = sum(volumes[-2 * config.SHORT_WINDOW:-config.SHORT_WINDOW]) or 1e-9
    volume_change = (recent_vol - prior_vol) / prior_vol * 100

    return Features(
        asset=asset,
        last_price=closes[-1],
        sma_short=sma_short,
        sma_long=sma_long,
        momentum=momentum,
        rsi=rsi,
        volatility=volatility,
        volume_change=volume_change,
    )


def compute_all_features(candles_by_asset: dict[str, list[Candle]]) -> dict[str, Features]:
    features = {}
    for asset, candles in candles_by_asset.items():
        f = compute_features(asset, candles)
        if f is not None:
            features[asset] = f
    return features
