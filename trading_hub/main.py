"""Orchestrates one full cycle of the pipeline, on a loop:

Reasoning layer -> Kraken live data -> feature engine -> JEV decision
-> hard risk engine -> paper execution -> dashboard.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time

from trading_hub import config, dashboard, executor, feature_engine, jev_engine, reasoning_layer, risk_engine
from trading_hub.kraken_client import KrakenClient
from trading_hub.portfolio import Portfolio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("trading_hub")

_DIRECTION_LABELS = {jev_engine.LONG: "LONG", jev_engine.SHORT: "SHORT", jev_engine.NEUTRAL: "NEUTRAL"}


def manage_open_positions(portfolio: Portfolio, verdicts: dict, prices: dict[str, float]) -> None:
    """Close any open position whose asset's JEV signal has reversed or gone
    neutral, so positions don't just sit open forever once the edge is gone."""
    for asset in list(portfolio.positions.keys()):
        verdict = verdicts.get(asset)
        price = prices.get(asset)
        if verdict is None or price is None:
            continue

        position = portfolio.positions[asset]
        if verdict.direction != position.direction:
            fee = position.size_usd * config.TAKER_FEE_FRACTION
            pnl = portfolio.close_position(asset, price, fee_usd=fee)
            log.info(
                "closed %s %s @ %.6f pnl=%.4f (signal now %s)",
                _DIRECTION_LABELS[position.direction], asset, price, pnl, _DIRECTION_LABELS.get(verdict.direction, "?"),
            )


def run_cycle(client: KrakenClient, portfolio: Portfolio) -> None:
    candles_by_asset = {}
    prices: dict[str, float] = {}
    for asset, pair in config.ASSETS.items():
        try:
            candles = client.get_ohlc(pair, interval_minutes=config.OHLC_INTERVAL_MINUTES)
            candles_by_asset[asset] = candles[-config.OHLC_LOOKBACK_CANDLES:]
            if candles:
                prices[asset] = candles[-1].close
        except Exception:
            log.exception("failed to fetch OHLC for %s (%s)", asset, pair)

    features = feature_engine.compute_all_features(candles_by_asset)

    regime = reasoning_layer.assess_regime(features)
    log.info("regime=%s (%s) rationale=%s", regime.regime, regime.source, regime.rationale)

    decision = jev_engine.decide(features)
    direction_label = _DIRECTION_LABELS.get(decision.direction)
    if direction_label is not None:
        adjusted_confidence = reasoning_layer.apply_regime_adjustment(decision.confidence, direction_label, regime)
        if adjusted_confidence != decision.confidence:
            log.info("regime adjustment: %.1f%% -> %.1f%%", decision.confidence, adjusted_confidence)
            decision = dataclasses.replace(decision, confidence=adjusted_confidence)
    log.info("decision=%s confidence=%.1f%%", decision.action, decision.confidence)

    manage_open_positions(portfolio, decision.verdicts, prices)

    spread_fraction = 0.0
    if decision.asset is not None:
        try:
            spread_fraction = client.get_spread_fraction(config.ASSETS[decision.asset])
        except Exception:
            log.exception("failed to fetch spread for %s", decision.asset)

    risk_verdict = risk_engine.evaluate(decision, portfolio, prices, spread_fraction)
    log.info("risk approved=%s reason=%s", risk_verdict.approved, risk_verdict.reason)

    if risk_verdict.approved and decision.asset is not None:
        result = executor.execute(decision, risk_verdict, portfolio, prices[decision.asset])
        log.info("execution executed=%s reason=%s", result.executed, result.reason)

    portfolio.record_equity(prices)
    dashboard.write_dashboard(portfolio, prices, decision, risk_verdict, regime)
    portfolio.save(config.PORTFOLIO_STATE_PATH)


def main() -> None:
    client = KrakenClient()

    if os.path.exists(config.PORTFOLIO_STATE_PATH):
        portfolio = Portfolio.load(config.PORTFOLIO_STATE_PATH)
        log.info("resumed portfolio from %s (cash=%.2f)", config.PORTFOLIO_STATE_PATH, portfolio.cash)
    else:
        portfolio = Portfolio()

    log.info("starting trading hub paper loop, poll interval=%ss", config.POLL_INTERVAL_SECONDS)
    while True:
        try:
            run_cycle(client, portfolio)
        except Exception:
            log.exception("cycle failed")
        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
