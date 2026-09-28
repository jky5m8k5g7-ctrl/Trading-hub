"""Orchestrates the pipeline on two loops:

- The decision loop (this module's main cycle): reasoning layer -> Kraken
  OHLC candles -> feature engine -> JEV decision -> hard risk engine ->
  paper execution. Runs every POLL_INTERVAL_SECONDS, since momentum/RSI/SMA
  are only meaningful over completed candles.
- The live feed: Kraken's WebSocket ticker stream pushes a tick, and
  DashboardPusher redraws the dashboard right then - event-driven, not on a
  polling timer - so price/PnL/equity are as fresh as the last tick off the
  wire, debounced only enough to avoid a disk-write storm on a multi-asset
  tick burst. The decision loop also prefers this live price over the last
  candle close when pricing a trade, so fills aren't priced off data up to
  a full poll interval old.
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


class DashboardPusher:
    """Writes the dashboard immediately on each live tick - event-driven,
    not polled - debounced to MIN_DASHBOARD_WRITE_INTERVAL_SECONDS so a
    tick burst across 5 assets doesn't turn into a disk-write storm."""

    def __init__(self, portfolio: Portfolio, live_feed: LiveFeed, portfolio_lock: threading.Lock, shared: SharedState):
        self._portfolio = portfolio
        self._live_feed = live_feed
        self._portfolio_lock = portfolio_lock
        self._shared = shared
        self._write_lock = threading.Lock()
        self._last_write = 0.0

    def push(self) -> None:
        now = time.monotonic()
        with self._write_lock:
            if now - self._last_write < config.MIN_DASHBOARD_WRITE_INTERVAL_SECONDS:
                return
            self._last_write = now

        prices = self._live_feed.get_prices()
        if not prices:
            return
        with self._shared.lock:
            decision, risk_verdict, regime = self._shared.decision, self._shared.risk_verdict, self._shared.regime
        with self._portfolio_lock:
            dashboard.write_dashboard(self._portfolio, prices, decision, risk_verdict, regime)


def heartbeat_loop(pusher: DashboardPusher, stop_event: threading.Event) -> None:
    """Fallback push on a slow timer, only in case the tick feed goes quiet
    for a while - the tick-driven push above is the primary path."""
    while not stop_event.wait(config.DASHBOARD_HEARTBEAT_SECONDS):
        pusher.push()


def main() -> None:
    client = KrakenClient()

    if os.path.exists(config.PORTFOLIO_STATE_PATH):
        portfolio = Portfolio.load(config.PORTFOLIO_STATE_PATH)
        log.info("resumed portfolio from %s (cash=%.2f)", config.PORTFOLIO_STATE_PATH, portfolio.cash)
    else:
        portfolio = Portfolio()

    portfolio_lock = threading.Lock()
    shared = SharedState()
    stop_event = threading.Event()

    live_feed = LiveFeed()
    pusher = DashboardPusher(portfolio, live_feed, portfolio_lock, shared)
    live_feed.on_tick(pusher.push)
    live_feed.start()

    heartbeat_thread = threading.Thread(target=heartbeat_loop, args=(pusher, stop_event), daemon=True)
    heartbeat_thread.start()

    log.info(
        "starting trading hub: decisions every %ss, dashboard pushed on every live tick (debounced %ss)",
        config.POLL_INTERVAL_SECONDS, config.MIN_DASHBOARD_WRITE_INTERVAL_SECONDS,
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
