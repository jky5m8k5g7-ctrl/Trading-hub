from trading_hub import feature_engine, regime_engine
from tests.conftest import make_candles, uptrend_prices, flat_prices


def _features_for(prices, volumes=None):
    candles = make_candles(prices, volumes)
    return feature_engine.compute_features("BTC", candles)


def test_no_data_yields_uncertain_and_disables_everything():
    regime = regime_engine.assess({})
    assert regime.regime == "UNCERTAIN"
    assert regime.allowed_assets == []
    assert regime.risk_multiplier == 0.0


def test_strong_uptrend_classified_as_trend_up():
    f = _features_for(uptrend_prices(40, step_pct=0.3))
    regime = regime_engine.assess({"BTC": f})
    assert regime.regime == "TREND_UP"
    assert "BTC" in regime.allowed_assets
    assert regime.risk_multiplier > 0


def test_flat_market_is_low_volatility_or_range_bound():
    f = _features_for(flat_prices(40))
    regime = regime_engine.assess({"BTC": f})
    assert regime.regime in ("LOW_VOLATILITY", "RANGE_BOUND")


def test_abnormal_spread_disables_the_asset():
    f = _features_for(flat_prices(40))
    regime = regime_engine.assess({"BTC": f}, spreads_bps={"BTC": 1000.0})
    assert regime.regime == "ABNORMAL_SPREAD"
    assert "BTC" in regime.disabled_assets
    assert "BTC" not in regime.allowed_assets
    assert regime.risk_multiplier == 0.0


def test_reasoning_summary_mentions_each_asset():
    f = _features_for(uptrend_prices(40, step_pct=0.3))
    regime = regime_engine.assess({"BTC": f})
    assert "BTC=" in regime.reasoning_summary
