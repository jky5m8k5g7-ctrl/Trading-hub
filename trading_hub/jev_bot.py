"""The Jev AI Decision Bot: wires jev_state -> jev_decision -> risk_governor
-> jev_execution -> portfolio -> database into one bot, run each cycle by
main.py alongside the three legacy strategies.

Ordering per cycle:
1. Hard deterministic exits (stop/take-profit/time-stop/trailing) on any
   open position, checked first and unconditionally - these never wait on
   Jev or the regime layer.
2. A strictly-optional Jev-requested early exit: only honored when Jev's
   own decision-quality gates are already met for the opposite side of an
   open position. This can never loosen or remove a hard stop - it only
   ever closes a position sooner than its hard exits would have.
3. Regime assessment, on its own slow cadence (config.REGIME_INTERVAL),
   cached between refreshes.
4. A new-entry decision from Jev, gated entirely by risk_governor.py.

Every decision (including HOLD and every rejection) and every closed trade
is written to the database - this is the audit trail the dashboard reads.
"""

from __future__ import annotations

import logging
import os
import time

from trading_hub import config, database, exit_rules, feature_engine, jev_decision, jev_execution, jev_state, regime_engine, risk_governor
from trading_hub.jev_state import AccountState, JevState, PositionState
from trading_hub.kraken_client import KrakenClient
from trading_hub.live_feed import LiveFeed
from trading_hub.portfolio import Portfolio
from trading_hub.regime_engine import JevRegime

log = logging.getLogger("trading_hub.jev")

BOT_NAME = "jev"


class JevBot:
    def __init__(self):
        self.name = BOT_NAME
        self.html_path = config.DASHBOARD_HTML_PATH_TEMPLATE.format(bot=BOT_NAME)
        self.state_path = config.STATE_JSON_PATH_TEMPLATE.format(bot=BOT_NAME)
        self.portfolio_path = config.PORTFOLIO_STATE_PATH_TEMPLATE.format(bot=BOT_NAME)

        database.init_db()
        if os.path.exists(self.portfolio_path):
            self.portfolio = Portfolio.load(self.portfolio_path)
            log.info("resumed Jev portfolio (cash=%.2f, %d open positions)", self.portfolio.cash, len(self.portfolio.positions))
        else:
            self.portfolio = Portfolio(cash=config.JEV_STARTING_BALANCE, starting_equity=config.JEV_STARTING_BALANCE, peak_equity=config.JEV_STARTING_BALANCE)

        self.session_id = database.get_or_create_active_session(BOT_NAME, config.JEV_STARTING_BALANCE)

        self._cached_regime: JevRegime | None = None
        self._regime_checked_at: float = 0.0

        # Latest decision/verdict, exposed to the dashboard.
        self.last_decision: jev_decision.JevDecision | None = None
        self.last_verdict: risk_governor.RiskVerdict | None = None
        self.last_regime: JevRegime | None = None
        self.decision_stream: list[dict] = []

    def start_new_session(self, session_name: str, duration_hours: float | None = None) -> None:
        """Explicit reset: fresh $200 balance, fresh session id. Never called
        automatically - only on an operator's request."""
        self.portfolio = Portfolio(cash=config.JEV_STARTING_BALANCE, starting_equity=config.JEV_STARTING_BALANCE, peak_equity=config.JEV_STARTING_BALANCE)
        self.session_id = database.start_new_session(BOT_NAME, config.JEV_STARTING_BALANCE, session_name, duration_hours)
        self.portfolio.save(self.portfolio_path)
        log.info("started new Jev session %r (session_id=%s)", session_name, self.session_id)

    def _build_account_state(self, prices: dict[str, float]) -> AccountState:
        now = time.time()
        open_positions = []
        for asset, pos in self.portfolio.positions.items():
            price = prices.get(asset, pos.entry_price)
            open_positions.append(PositionState(
                asset=asset, side="LONG" if pos.direction == 1 else "SHORT", entry_price=pos.entry_price,
                current_price=price, unrealized_pnl=pos.unrealized_pnl(price), time_in_trade_seconds=now - pos.opened_at,
            ))

        recent = self.portfolio.trade_log[-20:]
        closed_recent = [t for t in recent if t.action.endswith("_CLOSE")]
        recent_win_rate = (sum(1 for t in closed_recent if t.pnl_usd > 0) / len(closed_recent)) if closed_recent else None
        realized_pnl = sum(t.pnl_usd for t in self.portfolio.trade_log)

        return AccountState(
            equity=self.portfolio.equity(prices),
            cash=self.portfolio.cash,
            realized_pnl=realized_pnl,
            drawdown_pct=self.portfolio.drawdown_pct(prices),
            consecutive_wins=self.portfolio.consecutive_wins,
            consecutive_losses=self.portfolio.consecutive_losses,
            open_positions=open_positions,
            recent_win_rate=recent_win_rate,
        )

    def _manage_hard_exits(self, live_feed: LiveFeed, decision: jev_decision.JevDecision | None) -> None:
        now = time.time()
        for asset in list(self.portfolio.positions.keys()):
            position = self.portfolio.positions[asset]
            snapshot = live_feed.get_snapshot(asset)
            if snapshot is None:
                continue
            self.portfolio.update_excursion(asset, snapshot.last)

            side = "LONG" if position.direction == 1 else "SHORT"
            reason = exit_rules.check_exit(position, snapshot.last, int(now))

            if reason is None and decision is not None:
                opposite_action = f"{asset}_{'SHORT' if side == 'LONG' else 'LONG'}"
                if (
                    decision.action == opposite_action
                    and decision.winner_probability >= config.MIN_JEV_CONFIDENCE
                    and decision.probability_gap >= config.MIN_PROBABILITY_GAP
                ):
                    reason = "JEV_EARLY_EXIT"

            if reason is None:
                continue

            fill = jev_execution.simulate_exit(snapshot, side)
            fee = position.size_usd * fill.fee_fraction
            entry_price, opened_at, stop_price, tp_price, regime, jev_confidence, notional_usd = (
                position.entry_price, position.opened_at, position.stop_price,
                position.take_profit_price, position.regime, position.jev_confidence, position.size_usd,
            )
            quantity = notional_usd / entry_price if entry_price else 0.0

            pnl = self.portfolio.close_position(asset, fill.fill_price, fee_usd=fee, now=int(now), exit_reason=reason, slippage_usd=fill.slippage)
            trade = self.portfolio.trade_log[-1]
            log.info("[jev] CLOSE %s %s @ %.6f reason=%s net_pnl=%.4f", side, asset, fill.fill_price, reason, pnl)

            database.record_trade(database.TradeRecord(
                bot_name=BOT_NAME, session_id=self.session_id, asset=asset, side=side, opened_at=opened_at,
                entry_price=entry_price, quantity=quantity, notional_usd=notional_usd,
                stop_price=stop_price, take_profit_price=tp_price, closed_at=int(now), exit_price=fill.fill_price,
                gross_pnl=pnl + fee, fees=fee, slippage=fill.slippage, net_pnl=pnl,
                holding_time_seconds=trade.holding_time_seconds, mfe=trade.mfe_usd, mae=trade.mae_usd,
                exit_reason=reason, regime=regime, jev_confidence=jev_confidence,
                equity_after=self.portfolio.equity(live_feed.get_prices()), maker_or_taker=fill.maker_or_taker,
            ))
            self._push_stream_row(f"{asset}_{'SHORT' if side == 'LONG' else 'LONG'}" if reason == "JEV_EARLY_EXIT" else "EXIT", 0.0, f"{reason} @ {fill.fill_price:.4f} pnl={pnl:+.4f}")

    def _push_stream_row(self, action: str, confidence: float, note: str) -> None:
        self.decision_stream.append({"timestamp": time.time(), "action": action, "confidence": confidence, "note": note})
        if len(self.decision_stream) > config.DECISION_STREAM_MAX_ROWS:
            self.decision_stream = self.decision_stream[-config.DECISION_STREAM_MAX_ROWS:]

    def run_cycle(self, client: KrakenClient, live_feed: LiveFeed) -> None:
        now = time.time()

        # 1. Fetch candles/features for Jev's asset universe only.
        candles_by_asset = {}
        for asset in config.JEV_ASSETS:
            pair = config.ASSETS[asset]
            try:
                candles = client.get_ohlc(pair, interval_minutes=config.OHLC_INTERVAL_MINUTES)
                candles_by_asset[asset] = candles[-config.OHLC_LOOKBACK_CANDLES:]
            except Exception:
                log.exception("[jev] failed to fetch OHLC for %s (%s)", asset, pair)
        features = feature_engine.compute_all_features(candles_by_asset)

        # 2. Hard exits run first, unconditionally - no decision needed.
        self._manage_hard_exits(live_feed, self.last_decision)

        # 3. Regime, on its own slow cadence.
        if self._cached_regime is None or (now - self._regime_checked_at) >= config.REGIME_INTERVAL:
            spreads = {a: s.spread_bps for a, s in live_feed.get_snapshots().items() if a in config.JEV_ASSETS}
            self._cached_regime = regime_engine.assess(features, spreads)
            self._regime_checked_at = now
            log.info("[jev] regime=%s confidence=%.2f allowed=%s reasoning=%s", self._cached_regime.regime, self._cached_regime.confidence, self._cached_regime.allowed_assets, self._cached_regime.reasoning_summary)
        regime = self._cached_regime
        self.last_regime = regime

        # 4. Build structured state and ask Jev.
        asset_states = {
            asset: jev_state.build_asset_state(f, live_feed.get_snapshot(asset), live_feed, now)
            for asset, f in features.items()
        }
        prices = {a: s.last_price for a, s in asset_states.items()}
        account = self._build_account_state(prices)
        state = JevState(timestamp=now, regime=regime.regime, regime_confidence=regime.confidence, allowed_assets=regime.allowed_assets, assets=asset_states, account=account)

        decision = jev_decision.decide(state)
        self.last_decision = decision
        verdict = risk_governor.evaluate(decision, state, self.portfolio, live_feed, now=now)
        self.last_verdict = verdict

        database.record_decision(
            BOT_NAME, self.session_id, decision.action, verdict.asset, decision.winner_probability,
            decision.winner_probability, decision.second_probability, decision.probabilities,
            regime.regime, verdict.outcome, verdict.reason, decision.latency_ms,
        )
        log.info("[jev] decision=%s conf=%.2f gap=%.2f -> %s (%s)", decision.action, decision.winner_probability, decision.probability_gap, verdict.outcome, verdict.reason)
        self._push_stream_row(decision.action, decision.winner_probability, f"{verdict.outcome} - {verdict.reason}")

        if verdict.approved and verdict.asset is not None:
            snapshot = live_feed.get_snapshot(verdict.asset)
            if snapshot is None:
                log.warning("[jev] approved trade but no live snapshot for %s - skipping fill", verdict.asset)
            else:
                fill = jev_execution.simulate_entry(snapshot, verdict.side)
                fee = verdict.notional_usd * fill.fee_fraction
                direction = 1 if verdict.side == "LONG" else -1
                time_limit_at = int(now) + config.TIME_STOP_SECONDS
                self.portfolio.open_position(
                    verdict.asset, direction, verdict.notional_usd, fill.fill_price, fee,
                    action=decision.action, now=int(now), stop_price=verdict.stop_price,
                    take_profit_price=verdict.take_profit_price, time_limit_at=time_limit_at,
                    maker_or_taker=fill.maker_or_taker, regime=regime.regime, jev_confidence=decision.winner_probability,
                )
                log.info("[jev] OPEN %s %s $%.2f @ %.6f (%s) stop=%.6f tp=%.6f", verdict.side, verdict.asset, verdict.notional_usd, fill.fill_price, fill.maker_or_taker, verdict.stop_price, verdict.take_profit_price)

        self.portfolio.record_equity(prices)
        self.portfolio.save(self.portfolio_path)
