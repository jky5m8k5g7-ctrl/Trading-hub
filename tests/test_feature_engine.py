from trading_hub import feature_engine
from tests.conftest import make_candles, uptrend_prices, flat_prices


def test_returns_none_with_insufficient_history():
    candles = make_candles(flat_prices(5))
    assert feature_engine.compute_features("BTC", candles) is None


def test_uptrend_produces_positive_momentum_and_bullish_sma():
    candles = make_candles(uptrend_prices(40))
    f = feature_engine.compute_features("BTC", candles)
    assert f is not None
    assert f.momentum > 0
    assert f.sma_short > f.sma_long


def test_flat_market_has_near_zero_momentum_and_volatility():
    candles = make_candles(flat_prices(40))
    f = feature_engine.compute_features("BTC", candles)
    assert f is not None
    assert abs(f.momentum) < 1e-9
    assert f.volatility == 0.0
