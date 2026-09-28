"""Backtesting harness: replays historical 1-minute OHLC candles bar-by-bar
through the real feature engine, risk governor, exit rules, and paper
execution simulator used live.

No-lookahead guarantee: at bar i, only candles[:i+1] are ever passed to the
feature engine, and a position opened on bar i is priced/gated using only
that bar's data. Stops/targets are checked against each subsequent bar's
high/low (not just its close), so a wick that touches a stop intrabar is
caught the same way it would be live - this avoids the "unrealistic
perfect fills" failure mode of only checking closes.

This does NOT call the live TypeSafe API once per historical bar (slow,
costly, and not how backtests should validate an LLM signal anyway). It
takes a pluggable `decide_fn(features_by_asset) -> jev_engine.Decision`
and defaults to the deterministic trend ensemble (jev_engine.decide),
which is a fair, real, fast signal source for exercising the risk/
execution machinery. Swapping in a different deterministic strategy (see
strategies.py) tests that strategy's behavior under the same risk rules.

Supports a train/test split (config.BACKTEST_TRAIN_WINDOW_CANDLES /
BACKTEST_TEST_WINDOW_CANDLES) so a caller can calibrate on one window and
evaluate out-of-sample on a disjoint later one - this module does not
automate the roll-forward loop itself (repeatedly calibrating and testing)
but the split it returns is not overlapping, which is what makes that loop
uncorrupted by lookahead when a caller does drive it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from trading_hub import config, feature_engine, jev_engine, risk_engine
from trading_hub.jev_engine import Decision
from trading_hub.kraken_client import Candle
from trading_hub.portfolio import Portfolio


@dataclass
class BacktestResult:
    portfolio: Portfolio
    bars_processed: int = 0
    decisions_made: int = 0
    trades_opened: int = 0


def _synthetic_spread_fraction(candle: Candle) -> float:
    """No bid/ask in historical OHLC data - approximate the spread from the
    bar's own high/low range, floored at a realistic minimum. This is a
    deliberate simplification, not a claim of order-book accuracy."""
    if candle.close <= 0:
        return config.TAKER_FEE_FRACTION
    range_fraction = (candle.high - candle.low) / candle.close
    return max(range_fraction * 0.1, 0.0002)


def run_backtest(
    candles_by_asset: dict[str, list[Candle]],
    starting_cash: float = config.STARTING_CASH_USD,
    decide_fn=jev_engine.decide,
    start_index: int | None = None,
    end_index: int | None = None,
    cooldown_seconds: int = config.COOLDOWN_SECONDS,
    seed: int | None = 0,
) -> BacktestResult:
    """Replay candles_by_asset (same-length, aligned by index across assets)
    bar-by-bar. start_index/end_index (in bar units) let a caller carve out
    a train or test window without overlap."""
    if seed is not None:
        random.seed(seed)

    portfolio = Portfolio(cash=starting_cash, starting_equity=starting_cash, peak_equity=starting_cash)
    lengths = {len(c) for c in candles_by_asset.values()}
    if not lengths:
        return BacktestResult(portfolio=portfolio)
    n = min(lengths)

    min_history = max(config.LONG_WINDOW, config.RSI_WINDOW) + 1
    start_index = max(start_index or 0, min_history)
    end_index = min(end_index if end_index is not None else n, n)

    result = BacktestResult(portfolio=portfolio)

    for i in range(start_index, end_index):
        now = i * 60  # synthetic 1-minute-per-bar clock, in seconds

        # No-lookahead: only bars up to and including i are visible.
        candles_so_far = {asset: candles[: i + 1] for asset, candles in candles_by_asset.items()}
        features = feature_engine.compute_all_features(candles_so_far)
        prices = {asset: candles_so_far[asset][-1].close for asset in candles_by_asset}

        # Check hard exits against THIS bar's high/low range, not just its
        # close, so an intrabar stop/target touch is caught realistically.
        for asset in list(portfolio.positions.keys()):
            position = portfolio.positions[asset]
            candle = candles_by_asset[asset][i]
            direction = position.direction
            hit_stop = position.stop_price is not None and (
                (direction == 1 and candle.low <= position.stop_price) or (direction == -1 and candle.high >= position.stop_price)
            )
            hit_target = position.take_profit_price is not None and (
                (direction == 1 and candle.high >= position.take_profit_price) or (direction == -1 and candle.low <= position.take_profit_price)
            )
            if hit_stop:
                fee = position.size_usd * config.TAKER_FEE_FRACTION
                portfolio.close_position(asset, position.stop_price, fee_usd=fee, now=now, exit_reason="STOP_LOSS")
            elif hit_target:
                fee = position.size_usd * config.TAKER_FEE_FRACTION
                portfolio.close_position(asset, position.take_profit_price, fee_usd=fee, now=now, exit_reason="TAKE_PROFIT")

        decision: Decision = decide_fn(features)
        result.decisions_made += 1

        if decision.asset is not None:
            candle = candles_by_asset[decision.asset][i]
            spread_fraction = _synthetic_spread_fraction(candle)
            verdict = risk_engine.evaluate(decision, portfolio, prices, spread_fraction, now=now)
            if verdict.approved:
                direction = 1 if decision.direction == jev_engine.LONG else -1
                stop_pct = max(config.MIN_STOP_PCT, min(feature_engine.compute_features(decision.asset, candles_so_far[decision.asset]).volatility * config.ATR_STOP_MULTIPLIER, config.MAX_STOP_PCT))
                entry_price = candle.close
                stop_price = entry_price * (1 - stop_pct) if direction == 1 else entry_price * (1 + stop_pct)
                take_profit_price = entry_price * (1 + stop_pct * config.TAKE_PROFIT_R_MULTIPLE) if direction == 1 else entry_price * (1 - stop_pct * config.TAKE_PROFIT_R_MULTIPLE)
                fee = verdict.size_usd * config.TAKER_FEE_FRACTION
                portfolio.open_position(
                    decision.asset, direction, verdict.size_usd, entry_price, fee, decision.action, now=now,
                    stop_price=stop_price, take_profit_price=take_profit_price,
                )
                result.trades_opened += 1

        portfolio.record_equity(prices, now=now)
        result.bars_processed += 1

    return result
