from trading_hub import jev_engine, main
from trading_hub.jev_engine import AssetVerdict
from trading_hub.portfolio import Portfolio


def test_manage_open_positions_closes_on_signal_flip():
    portfolio = Portfolio(cash=1000.0)
    portfolio.open_position("BTC", direction=jev_engine.LONG, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)

    verdicts = {"BTC": AssetVerdict(asset="BTC", direction=jev_engine.SHORT, confidence=80.0, votes={})}
    main.manage_open_positions(portfolio, verdicts, {"BTC": 105.0})

    assert "BTC" not in portfolio.positions
    assert portfolio.trade_log[-1].action == "BTC_CLOSE"


def test_manage_open_positions_closes_on_neutral_signal():
    portfolio = Portfolio(cash=1000.0)
    portfolio.open_position("BTC", direction=jev_engine.LONG, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)

    verdicts = {"BTC": AssetVerdict(asset="BTC", direction=jev_engine.NEUTRAL, confidence=0.0, votes={})}
    main.manage_open_positions(portfolio, verdicts, {"BTC": 105.0})

    assert "BTC" not in portfolio.positions


def test_manage_open_positions_leaves_aligned_position_open():
    portfolio = Portfolio(cash=1000.0)
    portfolio.open_position("BTC", direction=jev_engine.LONG, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)

    verdicts = {"BTC": AssetVerdict(asset="BTC", direction=jev_engine.LONG, confidence=90.0, votes={})}
    main.manage_open_positions(portfolio, verdicts, {"BTC": 105.0})

    assert "BTC" in portfolio.positions


def test_manage_open_positions_skips_assets_with_no_verdict_or_price():
    portfolio = Portfolio(cash=1000.0)
    portfolio.open_position("BTC", direction=jev_engine.LONG, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)

    main.manage_open_positions(portfolio, {}, {})

    assert "BTC" in portfolio.positions
