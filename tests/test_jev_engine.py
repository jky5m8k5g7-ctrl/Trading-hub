from trading_hub import feature_engine, jev_engine
from tests.conftest import make_candles, uptrend_prices, flat_prices


def _features_for(prices, volumes=None):
    candles = make_candles(prices, volumes)
    return feature_engine.compute_features("BTC", candles)


def test_strong_uptrend_yields_long_decision_above_threshold():
    f = _features_for(uptrend_prices(40, step_pct=1.0))
    decision = jev_engine.decide({"BTC": f})
    assert decision.action == "BTC_LONG"
    assert decision.confidence > 72.0


def test_flat_market_yields_hold():
    f = _features_for(flat_prices(40))
    decision = jev_engine.decide({"BTC": f})
    assert decision.action == "HOLD"


def test_downtrend_yields_short_decision():
    prices = list(reversed(uptrend_prices(40, step_pct=1.0)))
    f = _features_for(prices)
    decision = jev_engine.decide({"BTC": f})
    assert decision.action == "BTC_SHORT"


def test_picks_highest_confidence_across_assets():
    weak = _features_for(uptrend_prices(40, step_pct=0.05))
    strong = _features_for(uptrend_prices(40, step_pct=1.0))
    decision = jev_engine.decide({"ETH": weak, "BTC": strong})
    assert decision.asset in (None, "BTC")
    if decision.asset:
        assert decision.action == "BTC_LONG"
