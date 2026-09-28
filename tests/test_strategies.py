from trading_hub import feature_engine, strategies
from tests.conftest import make_candles


def _features_for(prices, volumes=None):
    candles = make_candles(prices, volumes)
    return feature_engine.compute_features("BTC", candles)


def test_mean_reversion_longs_a_deep_oversold_dip():
    # Flat run then a sharp final drop: far below SMA, RSI oversold.
    prices = [100.0] * 25 + [100.0, 99.0, 98.0, 96.0, 93.0]
    f = _features_for(prices)
    decision = strategies.mean_reversion_decide({"BTC": f})
    assert decision.action == "BTC_LONG"
    assert decision.confidence >= 50.0


def test_mean_reversion_holds_on_flat_market():
    prices = [100.0] * 30
    f = _features_for(prices)
    decision = strategies.mean_reversion_decide({"BTC": f})
    assert decision.action == "HOLD"


def test_breakout_longs_a_new_high_above_prior_channel():
    # Range-bound around 100 for the window, then a decisive breakout candle.
    prices = [100.0 + (i % 3) * 0.1 for i in range(24)] + [103.0]
    f = _features_for(prices)
    assert f.last_price > f.high_20
    decision = strategies.breakout_decide({"BTC": f})
    assert decision.action == "BTC_LONG"


def test_breakout_shorts_a_new_low_below_prior_channel():
    prices = [100.0 - (i % 3) * 0.1 for i in range(24)] + [97.0]
    f = _features_for(prices)
    assert f.last_price < f.low_20
    decision = strategies.breakout_decide({"BTC": f})
    assert decision.action == "BTC_SHORT"


def test_breakout_holds_inside_the_channel():
    prices = [100.0 + (i % 3) * 0.1 for i in range(25)]
    f = _features_for(prices)
    decision = strategies.breakout_decide({"BTC": f})
    assert decision.action == "HOLD"
