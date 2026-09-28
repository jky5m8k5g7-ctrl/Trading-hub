import os

from trading_hub import feature_engine, reasoning_layer
from tests.conftest import make_candles, uptrend_prices, flat_prices


def _features_for(prices):
    candles = make_candles(prices)
    return feature_engine.compute_features("BTC", candles)


def test_no_features_yields_ranging_heuristic():
    assessment = reasoning_layer.assess_regime({})
    assert assessment.regime == "ranging"
    assert assessment.source == "heuristic"


def test_uptrend_without_api_key_yields_trending_up(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    f = _features_for(uptrend_prices(40, step_pct=1.0))
    assessment = reasoning_layer.assess_regime({"BTC": f})
    assert assessment.source == "heuristic"
    assert assessment.regime == "trending_up"


def test_flat_market_without_api_key_yields_ranging(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    f = _features_for(flat_prices(40))
    assessment = reasoning_layer.assess_regime({"BTC": f})
    assert assessment.regime == "ranging"


def test_apply_regime_adjustment_dampens_mismatched_direction():
    assessment = reasoning_layer.RegimeAssessment(regime="trending_up", rationale="test", source="heuristic")
    adjusted = reasoning_layer.apply_regime_adjustment(90.0, "SHORT", assessment)
    assert adjusted < 90.0


def test_apply_regime_adjustment_leaves_aligned_direction_untouched():
    assessment = reasoning_layer.RegimeAssessment(regime="trending_up", rationale="test", source="heuristic")
    adjusted = reasoning_layer.apply_regime_adjustment(90.0, "LONG", assessment)
    assert adjusted == 90.0
