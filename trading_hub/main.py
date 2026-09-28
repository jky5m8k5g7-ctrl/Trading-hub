"""Orchestrates one full cycle of the pipeline, on a loop:

Kraken live data -> feature engine -> JEV decision -> hard risk engine
-> paper execution -> dashboard.
"""

from __future__ import annotations

import logging
import time

from trading_hub import config, dashboard, executor, feature_engine, jev_engine, risk_engine
from trading_hub.kraken_client import KrakenClient
from trading_hub.portfolio import Portfolio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("trading_hub")


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
    decision = jev_engine.decide(features)
    log.info("decision=%s confidence=%.1f%%", decision.action, decision.confidence)

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

    dashboard.write_dashboard(portfolio, prices, decision, risk_verdict)


def main() -> None:
    client = KrakenClient()
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
