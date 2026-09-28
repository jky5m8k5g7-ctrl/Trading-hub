from trading_hub import config, risk_engine
from trading_hub.jev_engine import Decision
from trading_hub.portfolio import Portfolio


def make_decision(action="BTC_LONG", confidence=90.0, asset="BTC", direction=1):
    return Decision(action=action, confidence=confidence, asset=asset, direction=direction, verdicts={})


def test_hold_decision_is_never_approved():
    portfolio = Portfolio()
    decision = make_decision(action="HOLD", asset=None, direction=0)
    verdict = risk_engine.evaluate(decision, portfolio, {"BTC": 100.0}, spread_fraction=0.0)
    assert not verdict.approved


def test_low_confidence_is_rejected():
    portfolio = Portfolio()
    decision = make_decision(confidence=60.0)
    verdict = risk_engine.evaluate(decision, portfolio, {"BTC": 100.0}, spread_fraction=0.0)
    assert not verdict.approved
    assert "confidence" in verdict.reason


def test_high_confidence_passes_and_caps_trade_size():
    portfolio = Portfolio()
    decision = make_decision(confidence=95.0)
    verdict = risk_engine.evaluate(decision, portfolio, {"BTC": 100.0}, spread_fraction=0.0)
    assert verdict.approved
    assert verdict.size_usd == config.MAX_TRADE_USD


def test_drawdown_breach_blocks_trading():
    portfolio = Portfolio(cash=100.0, starting_equity=1000.0, peak_equity=1000.0)
    decision = make_decision(confidence=95.0)
    verdict = risk_engine.evaluate(decision, portfolio, {"BTC": 100.0}, spread_fraction=0.0)
    assert not verdict.approved
    assert "drawdown" in verdict.reason


def test_cooldown_blocks_repeat_trade_on_same_asset():
    portfolio = Portfolio()
    now = 1_700_000_000
    portfolio.last_trade_at["BTC"] = now
    decision = make_decision(confidence=95.0)
    verdict = risk_engine.evaluate(decision, portfolio, {"BTC": 100.0}, spread_fraction=0.0, now=now + 10)
    assert not verdict.approved
    assert "cooldown" in verdict.reason


def test_wide_spread_relative_to_edge_is_rejected():
    portfolio = Portfolio()
    decision = make_decision(confidence=73.0)
    verdict = risk_engine.evaluate(decision, portfolio, {"BTC": 100.0}, spread_fraction=0.20)
    assert not verdict.approved
    assert "fee/spread" in verdict.reason
