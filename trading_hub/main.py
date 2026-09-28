"""Runs three paper-trading bots side by side on shared live data:

- trend: the original JEV ensemble (trend + momentum + RSI reversion)
- mean_reversion: fades extreme deviation from the long SMA
- breakout: trades a decisive break of the prior N-candle high/low channel

They share one Kraken OHLC fetch, one feature computation, one regime
assessment, and one live WebSocket feed per cycle - only the decision
function differs - and each gets its own portfolio, dashboard, and
persisted state (see config.py's *_TEMPLATE paths). dashboard.html is a
combined overview comparing all three.

Pricing: the decision loop reasons over completed 1-minute OHLC candles
(momentum/RSI/SMA are only meaningful at that resolution), but the
dashboard is pushed immediately on every live WebSocket tick - event
driven, not polled - so price/PnL/equity are never stale by more than the
debounce floor. The decision loop also prefers a fresh live tick over the
last candle close when pricing a trade.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
import time
from typing import Callable

from trading_hub import config, dashboard, database, executor, feature_engine, jev_bot, jev_dashboard, jev_engine, leaderboard, reasoning_layer, risk_engine, safety_guard, strategies
from trading_hub.feature_engine import Features
from trading_hub.jev_engine import Decision
from trading_hub.kraken_client import KrakenClient
from trading_hub.live_feed import LiveFeed
from trading_hub.portfolio import Portfolio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("trading_hub")

_DIRECTION_LABELS = {jev_engine.LONG: "LONG", jev_engine.SHORT: "SHORT", jev_engine.NEUTRAL: "NEUTRAL"}

STRATEGIES: dict[str, Callable[[dict[str, Features]], Decision]] = {
    "trend": jev_engine.decide,
    "mean_reversion": strategies.mean_reversion_decide,
    "breakout": strategies.breakout_decide,
}


class Bot:
    """One strategy's independent portfolio, latest decision state, and
    dashboard paths. `lock` guards the portfolio; `state_lock` guards the
    cached decision/risk/regime the fast tick-driven pusher reads."""

    def __init__(self, name: str, decide_fn: Callable[[dict[str, Features]], Decision]):
        self.name = name
        self.decide_fn = decide_fn
        self.html_path = config.DASHBOARD_HTML_PATH_TEMPLATE.format(bot=name)
        self.state_path = config.STATE_JSON_PATH_TEMPLATE.format(bot=name)
        self.portfolio_path = config.PORTFOLIO_STATE_PATH_TEMPLATE.format(bot=name)

        if os.path.exists(self.portfolio_path):
            self.portfolio = Portfolio.load(self.portfolio_path)
            log.info("[%s] resumed portfolio (cash=%.2f)", name, self.portfolio.cash)
        else:
            self.portfolio = Portfolio()

        database.init_db()
        self.session_id = database.get_or_create_active_session(name, config.STARTING_CASH_USD)

        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.decision = Decision(action="HOLD", confidence=0.0, asset=None, direction=jev_engine.NEUTRAL, verdicts={})
        self.risk_verdict = risk_engine.RiskVerdict(approved=False, reason="starting up")
        self.regime: reasoning_layer.RegimeAssessment | None = None

    def push_dashboard(self, prices: dict[str, float]) -> None:
        with self.state_lock:
            decision, risk_verdict, regime = self.decision, self.risk_verdict, self.regime
        with self.lock:
            dashboard.write_dashboard(self.portfolio, prices, decision, risk_verdict, self.html_path, self.state_path, regime, bot_name=self.name)

    def summary(self, prices: dict[str, float]) -> dict:
        with self.lock:
            equity = self.portfolio.equity(prices)
            drawdown = self.portfolio.drawdown_pct(prices)
            open_positions = len(self.portfolio.positions)
            starting_equity = self.portfolio.starting_equity
        with self.state_lock:
            decision = self.decision
        return {
            "name": self.name,
            "html_path": self.html_path,
            "equity": equity,
            "starting_equity": starting_equity,
            "cash": self.portfolio.cash,
            "drawdown_pct": drawdown,
            "open_positions": open_positions,
            "last_action": decision.action,
            "last_confidence": decision.confidence,
        }


def manage_open_positions(bot: "Bot", verdicts: dict, prices: dict[str, float]) -> None:
    """Close any open position whose asset's signal has reversed or gone
    neutral, so positions don't just sit open forever once the edge is gone."""
    portfolio = bot.portfolio
    for asset in list(portfolio.positions.keys()):
        verdict = verdicts.get(asset)
        price = prices.get(asset)
        if verdict is None or price is None:
            continue

        position = portfolio.positions[asset]
        if verdict.direction != position.direction:
            side = "LONG" if position.direction == 1 else "SHORT"
            entry_price, opened_at, notional = position.entry_price, position.opened_at, position.size_usd
            fee = notional * config.TAKER_FEE_FRACTION
            pnl = portfolio.close_position(asset, price, fee_usd=fee, exit_reason="SIGNAL_REVERSAL")
            trade = portfolio.trade_log[-1]
            log.info(
                "[%s] closed %s %s @ %.6f pnl=%.4f (signal now %s)",
                bot.name, side, asset, price, pnl, _DIRECTION_LABELS.get(verdict.direction, "?"),
            )
            database.record_trade(database.TradeRecord(
                bot_name=bot.name, session_id=bot.session_id, asset=asset, side=side, opened_at=opened_at,
                entry_price=entry_price, quantity=notional / entry_price if entry_price else 0.0, notional_usd=notional,
                closed_at=trade.timestamp, exit_price=price, gross_pnl=pnl + fee, fees=fee, net_pnl=pnl,
                holding_time_seconds=trade.holding_time_seconds, exit_reason="SIGNAL_REVERSAL",
                equity_after=portfolio.equity(prices),
            ))


def merge_live_prices(candle_prices: dict[str, float], live_feed: LiveFeed) -> dict[str, float]:
    """Prefer a fresh live tick over the last candle close, which can be up
    to a full poll interval stale by the time a decision fires."""
    prices = dict(candle_prices)
    for asset, price in live_feed.get_prices().items():
        if live_feed.age_seconds(asset) <= config.LIVE_PRICE_MAX_AGE_SECONDS:
            prices[asset] = price
    return prices


def fetch_candles_and_prices(client: KrakenClient) -> tuple[dict, dict[str, float]]:
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
    return candles_by_asset, prices


def run_cycle(client: KrakenClient, bots: list[Bot], live_feed: LiveFeed) -> None:
    candles_by_asset, prices = fetch_candles_and_prices(client)
    prices = merge_live_prices(prices, live_feed)
    features = feature_engine.compute_all_features(candles_by_asset)

    regime = reasoning_layer.assess_regime(features)
    log.info("regime=%s (%s) rationale=%s", regime.regime, regime.source, regime.rationale)

    decisions: dict[str, Decision] = {}
    for bot in bots:
        decision = bot.decide_fn(features)
        direction_label = _DIRECTION_LABELS.get(decision.direction)
        if direction_label is not None:
            adjusted = reasoning_layer.apply_regime_adjustment(decision.confidence, direction_label, regime)
            if adjusted != decision.confidence:
                decision = dataclasses.replace(decision, confidence=adjusted)
        decisions[bot.name] = decision
        log.info("[%s] decision=%s confidence=%.1f%%", bot.name, decision.action, decision.confidence)

    # Fetch each distinct chosen asset's spread once, not once per bot.
    spread_by_asset: dict[str, float] = {}
    for decision in decisions.values():
        if decision.asset and decision.asset not in spread_by_asset:
            try:
                spread_by_asset[decision.asset] = client.get_spread_fraction(config.ASSETS[decision.asset])
            except Exception:
                log.exception("failed to fetch spread for %s", decision.asset)
                spread_by_asset[decision.asset] = 0.0

    for bot in bots:
        decision = decisions[bot.name]
        spread_fraction = spread_by_asset.get(decision.asset, 0.0) if decision.asset else 0.0

        with bot.lock:
            manage_open_positions(bot, decision.verdicts, prices)

            risk_verdict = risk_engine.evaluate(decision, bot.portfolio, prices, spread_fraction)
            log.info("[%s] risk approved=%s reason=%s", bot.name, risk_verdict.approved, risk_verdict.reason)

            if risk_verdict.approved and decision.asset is not None:
                result = executor.execute(decision, risk_verdict, bot.portfolio, prices[decision.asset])
                log.info("[%s] execution executed=%s reason=%s", bot.name, result.executed, result.reason)

            bot.portfolio.record_equity(prices)
            dashboard.write_dashboard(bot.portfolio, prices, decision, risk_verdict, bot.html_path, bot.state_path, regime, bot_name=bot.name)
            bot.portfolio.save(bot.portfolio_path)

        with bot.state_lock:
            bot.decision = decision
            bot.risk_verdict = risk_verdict
            bot.regime = regime

    dashboard.write_overview([b.summary(prices) for b in bots], prices, config.OVERVIEW_HTML_PATH)


def _jev_summary(bot: jev_bot.JevBot, prices: dict[str, float]) -> dict:
    equity = bot.portfolio.equity(prices)
    return {
        "name": bot.name,
        "html_path": bot.html_path,
        "equity": equity,
        "starting_equity": bot.portfolio.starting_equity,
        "cash": bot.portfolio.cash,
        "drawdown_pct": bot.portfolio.drawdown_pct(prices),
        "open_positions": len(bot.portfolio.positions),
        "last_action": bot.last_decision.action if bot.last_decision else "-",
        "last_confidence": (bot.last_decision.winner_probability * 100) if bot.last_decision else 0.0,
    }


class DashboardPusher:
    """Writes every bot's dashboard (plus the overview) immediately on each
    live tick - event-driven, not polled - debounced to
    MIN_DASHBOARD_WRITE_INTERVAL_SECONDS so a tick burst across 10 assets
    doesn't turn into a disk-write storm."""

    def __init__(self, bots: list[Bot], jev: jev_bot.JevBot, live_feed: LiveFeed):
        self._bots = bots
        self._jev = jev
        self._live_feed = live_feed
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
        for bot in self._bots:
            bot.push_dashboard(prices)
        jev_dashboard.write(self._jev, prices)
        summaries = [b.summary(prices) for b in self._bots] + [_jev_summary(self._jev, prices)]
        dashboard.write_overview(summaries, prices, config.OVERVIEW_HTML_PATH)


def heartbeat_loop(pusher: DashboardPusher, stop_event: threading.Event) -> None:
    """Fallback push on a slow timer, only in case the tick feed goes quiet
    for a while - the tick-driven push above is the primary path."""
    while not stop_event.wait(config.DASHBOARD_HEARTBEAT_SECONDS):
        pusher.push()


def main() -> None:
    safety_guard.check()  # fatal on any violation - never caught here

    client = KrakenClient()
    bots = [Bot(name, decide_fn) for name, decide_fn in STRATEGIES.items()]
    jev = jev_bot.JevBot()

    stop_event = threading.Event()
    live_feed = LiveFeed()
    pusher = DashboardPusher(bots, jev, live_feed)
    live_feed.on_tick(pusher.push)
    live_feed.start()

    heartbeat_thread = threading.Thread(target=heartbeat_loop, args=(pusher, stop_event), daemon=True)
    heartbeat_thread.start()

    log.info(
        "starting trading hub: %d legacy bots (%s) + Jev, decisions every %ss, dashboards pushed on every live tick",
        len(bots), ", ".join(b.name for b in bots), config.POLL_INTERVAL_SECONDS,
    )
    try:
        while True:
            try:
                run_cycle(client, bots, live_feed)
            except Exception:
                log.exception("cycle failed")
            try:
                jev.run_cycle(client, live_feed)
                jev_dashboard.write(jev, live_feed.get_prices())
            except Exception:
                log.exception("[jev] cycle failed")
            try:
                leaderboard.write()
            except Exception:
                log.exception("leaderboard write failed")
            time.sleep(config.POLL_INTERVAL_SECONDS)
    finally:
        stop_event.set()
        live_feed.stop()


if __name__ == "__main__":
    main()
