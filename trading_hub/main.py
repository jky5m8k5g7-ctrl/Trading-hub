"""Orchestrates the pipeline on two loops:

- The decision loop (this module's main cycle): reasoning layer -> Kraken
  OHLC candles -> feature engine -> JEV decision -> hard risk engine ->
  paper execution. Runs every POLL_INTERVAL_SECONDS, since momentum/RSI/SMA
  are only meaningful over completed candles.
- The live feed + dashboard refresh loop: Kraken's WebSocket ticker stream
  keeps a live last-trade price per asset, and a fast background thread
  redraws the dashboard from it every DASHBOARD_REFRESH_SECONDS, so
  price/PnL/equity are never more than a couple seconds stale even between
  decision cycles. The decision loop also prefers this live price over the
  last candle close when pricing a trade, so fills aren't priced off data
  up to a full poll interval old.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
import time

from trading_hub import config, dashboard, executor, feature_engine, jev_engine, reasoning_layer, risk_engine
from trading_hub.kraken_client import KrakenClient
from trading_hub.live_feed import LiveFeed
from trading_hub.portfolio import Portfolio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("trading_hub")

_DIRECTION_LABELS = {jev_engine.LONG: "LONG", jev_engine.SHORT: "SHORT", jev_engine.NEUTRAL: "NEUTRAL"}


class SharedState:
    """Latest decision/risk/regime, shared between the decision loop and the
    fast dashboard-refresh loop under a lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.decision = jev_engine.Decision(action="HOLD", confidence=0.0, asset=None, direction=jev_engine.NEUTRAL, verdicts={})
        self.risk_verdict = risk_engine.RiskVerdict(approved=False, reason="starting up")
        self.regime: reasoning_layer.RegimeAssessment | None = None


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


def merge_live_prices(candle_prices: dict[str, float], live_feed: LiveFeed) -> dict[str, float]:
    """Prefer a fresh live tick over the last candle close, which can be up
    to a full poll interval stale by the time a decision fires."""
    prices = dict(candle_prices)
    for asset, price in live_feed.get_prices().items():
        if live_feed.age_seconds(asset) <= config.LIVE_PRICE_MAX_AGE_SECONDS:
            prices[asset] = price
    return prices


def run_cycle(client: KrakenClient, portfolio: Portfolio, live_feed: LiveFeed, portfolio_lock: threading.Lock, shared: SharedState) -> None:
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

    prices = merge_live_prices(prices, live_feed)

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

    spread_fraction = 0.0
    if decision.asset is not None:
        try:
            spread_fraction = client.get_spread_fraction(config.ASSETS[decision.asset])
        except Exception:
            log.exception("failed to fetch spread for %s", decision.asset)

    with portfolio_lock:
        manage_open_positions(portfolio, decision.verdicts, prices)

        risk_verdict = risk_engine.evaluate(decision, portfolio, prices, spread_fraction)
        log.info("risk approved=%s reason=%s", risk_verdict.approved, risk_verdict.reason)

        if risk_verdict.approved and decision.asset is not None:
            result = executor.execute(decision, risk_verdict, portfolio, prices[decision.asset])
            log.info("execution executed=%s reason=%s", result.executed, result.reason)

        portfolio.record_equity(prices)
        dashboard.write_dashboard(portfolio, prices, decision, risk_verdict, regime)
        portfolio.save(config.PORTFOLIO_STATE_PATH)

    with shared.lock:
        shared.decision = decision
        shared.risk_verdict = risk_verdict
        shared.regime = regime


def dashboard_refresh_loop(portfolio: Portfolio, live_feed: LiveFeed, portfolio_lock: threading.Lock, shared: SharedState, stop_event: threading.Event) -> None:
    """Redraws the dashboard from live tick prices between decision cycles,
    so equity/PnL don't sit stale for a full poll interval."""
    while not stop_event.wait(config.DASHBOARD_REFRESH_SECONDS):
        prices = live_feed.get_prices()
        if not prices:
            continue
        with shared.lock:
            decision, risk_verdict, regime = shared.decision, shared.risk_verdict, shared.regime
        with portfolio_lock:
            dashboard.write_dashboard(portfolio, prices, decision, risk_verdict, regime)


def main() -> None:
    client = KrakenClient()

    if os.path.exists(config.PORTFOLIO_STATE_PATH):
        portfolio = Portfolio.load(config.PORTFOLIO_STATE_PATH)
        log.info("resumed portfolio from %s (cash=%.2f)", config.PORTFOLIO_STATE_PATH, portfolio.cash)
    else:
        portfolio = Portfolio()

    live_feed = LiveFeed()
    live_feed.start()

    portfolio_lock = threading.Lock()
    shared = SharedState()
    stop_event = threading.Event()
    refresh_thread = threading.Thread(
        target=dashboard_refresh_loop, args=(portfolio, live_feed, portfolio_lock, shared, stop_event), daemon=True,
    )
    refresh_thread.start()

    log.info(
        "starting trading hub: decisions every %ss, dashboard refresh every %ss",
        config.POLL_INTERVAL_SECONDS, config.DASHBOARD_REFRESH_SECONDS,
    )
    try:
        while True:
            try:
                run_cycle(client, portfolio, live_feed, portfolio_lock, shared)
            except Exception:
                log.exception("cycle failed")
            time.sleep(config.POLL_INTERVAL_SECONDS)
    finally:
        stop_event.set()
        live_feed.stop()


if __name__ == "__main__":
    main()
