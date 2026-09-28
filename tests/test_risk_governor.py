from trading_hub import config, risk_governor
from trading_hub.jev_decision import JevDecision
from trading_hub.jev_state import AccountState, AssetState, JevState
from trading_hub.portfolio import Portfolio


class FakeLiveFeed:
    def __init__(self, connected=True):
        self._connected = connected

    def is_connected(self):
        return self._connected


def make_asset_state(asset="BTC", last_price=100.0, spread_bps=5.0, volatility=0.005, data_age_seconds=1.0, high_20=101.0, low_20=99.0):
    return AssetState(
        asset=asset, last_price=last_price, mid=last_price, microprice=last_price,
        spread_bps=spread_bps, imbalance=0.0, momentum_pct=0.5, rsi=55.0, volatility=volatility,
        volume_change_pct=0.0, high_20=high_20, low_20=low_20, returns={}, data_age_seconds=data_age_seconds,
    )


def make_state(assets=None, allowed_assets=None, regime="TREND_UP"):
    assets = assets or {"BTC": make_asset_state()}
    allowed_assets = allowed_assets if allowed_assets is not None else list(assets.keys())
    account = AccountState(equity=200.0, cash=200.0, realized_pnl=0.0, drawdown_pct=0.0, consecutive_wins=0, consecutive_losses=0, open_positions=[], recent_win_rate=None)
    return JevState(timestamp=0.0, regime=regime, regime_confidence=0.8, allowed_assets=allowed_assets, assets=assets, account=account)


def make_decision(action="BTC_LONG", winner=0.85, second=0.10, source="typesafe", reason="ok"):
    probs = {action: winner, "HOLD": second}
    return JevDecision(action=action, winner_probability=winner, second_probability=second, probabilities=probs, source=source, reason=reason, timestamp=0.0, latency_ms=10.0)


def test_hold_decision_is_never_approved():
    verdict = risk_governor.evaluate(make_decision(action="HOLD", winner=0.0, second=0.0, source="unavailable", reason="x"), make_state(), Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert not verdict.approved
    assert verdict.outcome == "HOLD"


def test_jev_unavailable_holds():
    decision = make_decision(source="unavailable", reason="no api key")
    verdict = risk_governor.evaluate(decision, make_state(), Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert not verdict.approved
    assert "unavailable" in verdict.reason


def test_low_confidence_is_rejected():
    decision = make_decision(winner=0.60, second=0.10)
    verdict = risk_governor.evaluate(decision, make_state(), Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert not verdict.approved
    assert verdict.outcome == "REJECTED"
    assert "confidence" in verdict.reason


def test_small_probability_gap_is_rejected():
    decision = make_decision(winner=0.80, second=0.75)
    verdict = risk_governor.evaluate(decision, make_state(), Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert not verdict.approved
    assert "probability gap" in verdict.reason


def test_stale_data_holds():
    state = make_state(assets={"BTC": make_asset_state(data_age_seconds=999.0)})
    verdict = risk_governor.evaluate(make_decision(), state, Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert not verdict.approved
    assert "stale" in verdict.reason


def test_disconnected_feed_holds():
    verdict = risk_governor.evaluate(make_decision(), make_state(), Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed(connected=False))
    assert not verdict.approved
    assert "disconnected" in verdict.reason


def test_extreme_spread_rejected():
    state = make_state(assets={"BTC": make_asset_state(spread_bps=100.0)})
    verdict = risk_governor.evaluate(make_decision(), state, Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert not verdict.approved
    assert "spread" in verdict.reason


def test_regime_disabled_asset_rejected():
    state = make_state(allowed_assets=[])
    verdict = risk_governor.evaluate(make_decision(), state, Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert not verdict.approved
    assert "disabled by current regime" in verdict.reason


def test_existing_same_side_position_holds():
    portfolio = Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0)
    portfolio.open_position("BTC", direction=1, size_usd=30.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)
    verdict = risk_governor.evaluate(make_decision(), make_state(), portfolio, FakeLiveFeed())
    assert not verdict.approved
    assert "already open" in verdict.reason


def test_max_simultaneous_positions_rejected():
    portfolio = Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0)
    portfolio.open_position("ETH", direction=1, size_usd=30.0, price=2000.0, fee_usd=0.0, action="ETH_LONG", now=1)
    portfolio.open_position("SOL", direction=1, size_usd=30.0, price=100.0, fee_usd=0.0, action="SOL_LONG", now=1)
    verdict = risk_governor.evaluate(make_decision(), make_state(), portfolio, FakeLiveFeed())
    assert not verdict.approved
    assert "max simultaneous positions" in verdict.reason


def test_hard_drawdown_halts_bot():
    portfolio = Portfolio(cash=100.0, starting_equity=200.0, peak_equity=200.0)
    verdict = risk_governor.evaluate(make_decision(), make_state(), portfolio, FakeLiveFeed())
    assert not verdict.approved
    assert portfolio.halted
    assert "hard limit" in portfolio.halted_reason


def test_halted_bot_stays_halted():
    portfolio = Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0)
    portfolio.halt("test halt")
    verdict = risk_governor.evaluate(make_decision(), make_state(), portfolio, FakeLiveFeed())
    assert not verdict.approved
    assert "test halt" in verdict.reason


def test_loss_streak_cooldown_holds():
    portfolio = Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0)
    portfolio.trading_paused_until = 9_999_999_999
    verdict = risk_governor.evaluate(make_decision(), make_state(), portfolio, FakeLiveFeed(), now=0.0)
    assert not verdict.approved
    assert "cooldown" in verdict.reason


def test_low_edge_relative_to_cost_is_rejected():
    # Wide spread relative to a modest confidence: cost eats the edge.
    state = make_state(assets={"BTC": make_asset_state(spread_bps=20.0, volatility=0.001)})
    decision = make_decision(winner=0.73, second=0.10)
    verdict = risk_governor.evaluate(decision, state, Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert not verdict.approved
    assert "edge" in verdict.reason


def test_approved_trade_has_sized_position_and_stops():
    state = make_state(assets={"BTC": make_asset_state(spread_bps=1.0, volatility=0.01)})
    decision = make_decision(winner=0.90, second=0.05)
    verdict = risk_governor.evaluate(decision, state, Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert verdict.approved
    assert verdict.outcome == "APPROVED"
    assert verdict.notional_usd > 0
    assert verdict.notional_usd <= config.DEFAULT_POSITION_NOTIONAL + 1e-6
    assert verdict.stop_price < 100.0  # LONG stop below entry
    assert verdict.take_profit_price > 100.0  # LONG target above entry


def test_short_side_stop_and_target_are_on_the_correct_sides():
    state = make_state(assets={"BTC": make_asset_state(spread_bps=1.0, volatility=0.01)})
    decision = make_decision(action="BTC_SHORT", winner=0.90, second=0.05)
    verdict = risk_governor.evaluate(decision, state, Portfolio(cash=200.0, starting_equity=200.0, peak_equity=200.0), FakeLiveFeed())
    assert verdict.approved
    assert verdict.stop_price > 100.0
    assert verdict.take_profit_price < 100.0


def test_position_size_respects_hard_cap_over_risk_budget():
    # Very tight stop -> risk-budget formula would suggest a huge notional;
    # the hard $30/15% cap must win.
    size = risk_governor.compute_position_size(equity=200.0, stop_distance_pct=0.0001)
    assert size <= config.DEFAULT_POSITION_NOTIONAL + 1e-6


def test_position_size_respects_risk_budget_over_hard_cap():
    # Wide stop -> risk-budget formula caps it below the $30 default.
    size = risk_governor.compute_position_size(equity=200.0, stop_distance_pct=0.10)
    risk_budget = 200.0 * config.MAX_RISK_PER_TRADE_PCT
    assert size == risk_budget / 0.10
    assert size < config.DEFAULT_POSITION_NOTIONAL


def test_stop_distance_is_bounded():
    assert risk_governor.compute_stop_distance_pct(0.0) == config.MIN_STOP_PCT
    assert risk_governor.compute_stop_distance_pct(10.0) == config.MAX_STOP_PCT


def test_daily_soft_drawdown_halves_size():
    # 3.5% drawdown from a 200 peak -> soft threshold crossed, not hard.
    portfolio = Portfolio(cash=193.0, starting_equity=200.0, peak_equity=200.0)
    state = make_state(assets={"BTC": make_asset_state(spread_bps=1.0, volatility=0.01)})
    decision = make_decision(winner=0.90, second=0.05)
    verdict = risk_governor.evaluate(decision, state, portfolio, FakeLiveFeed())
    assert verdict.approved
    assert verdict.size_multiplier == config.DAILY_SOFT_DRAWDOWN_SIZE_MULTIPLIER
