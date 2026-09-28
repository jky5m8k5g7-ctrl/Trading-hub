import pytest

from trading_hub import executor
from trading_hub.jev_engine import Decision
from trading_hub.portfolio import Portfolio
from trading_hub.risk_engine import RiskVerdict


def test_execute_opens_position_and_debits_cash():
    portfolio = Portfolio(cash=1000.0)
    decision = Decision(action="BTC_LONG", confidence=90.0, asset="BTC", direction=1, verdicts={})
    verdict = RiskVerdict(approved=True, reason="ok", size_usd=50.0)

    result = executor.execute(decision, verdict, portfolio, price=100.0, now=1_700_000_000)

    assert result.executed
    assert "BTC" in portfolio.positions
    assert portfolio.positions["BTC"].size_usd == 50.0
    assert portfolio.cash < 1000.0 - 50.0 + 1e-6  # cash reduced by size + fee


def test_execute_skips_when_risk_rejected():
    portfolio = Portfolio(cash=1000.0)
    decision = Decision(action="BTC_LONG", confidence=90.0, asset="BTC", direction=1, verdicts={})
    verdict = RiskVerdict(approved=False, reason="blocked")

    result = executor.execute(decision, verdict, portfolio, price=100.0)

    assert not result.executed
    assert "BTC" not in portfolio.positions


def test_unrealized_pnl_tracks_price_moves_long_and_short():
    portfolio = Portfolio(cash=1000.0)
    portfolio.open_position("BTC", direction=1, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)
    assert portfolio.positions["BTC"].unrealized_pnl(110.0) > 0

    portfolio.open_position("ETH", direction=-1, size_usd=50.0, price=100.0, fee_usd=0.0, action="ETH_SHORT", now=1)
    assert portfolio.positions["ETH"].unrealized_pnl(90.0) > 0
    assert portfolio.positions["ETH"].unrealized_pnl(110.0) < 0


def test_close_position_realizes_pnl_into_cash():
    portfolio = Portfolio(cash=1000.0)
    portfolio.open_position("BTC", direction=1, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)
    cash_after_open = portfolio.cash
    pnl = portfolio.close_position("BTC", price=110.0, fee_usd=0.0, now=2)

    assert pnl > 0
    assert portfolio.cash == cash_after_open + 50.0 + pnl
    assert "BTC" not in portfolio.positions


def test_save_and_load_round_trips_full_state(tmp_path):
    portfolio = Portfolio(cash=1000.0)
    portfolio.open_position("BTC", direction=1, size_usd=50.0, price=100.0, fee_usd=0.13, action="BTC_LONG", now=1)
    portfolio.close_position("BTC", price=110.0, fee_usd=0.13, now=2)
    portfolio.open_position("ETH", direction=-1, size_usd=50.0, price=2000.0, fee_usd=0.13, action="ETH_SHORT", now=3)

    path = str(tmp_path / "portfolio_state.json")
    portfolio.save(path)
    restored = Portfolio.load(path)

    assert restored.cash == portfolio.cash
    assert restored.peak_equity == portfolio.peak_equity
    assert restored.last_trade_at == portfolio.last_trade_at
    assert list(restored.positions.keys()) == ["ETH"]
    assert restored.positions["ETH"] == portfolio.positions["ETH"]
    assert len(restored.trade_log) == len(portfolio.trade_log) == 3


def test_opening_a_position_does_not_by_itself_show_as_a_loss():
    """Regression test: equity() must count a position's notional, not just
    its unrealized PnL, or opening a $50 position looks like a $50 loss."""
    portfolio = Portfolio(cash=1000.0, starting_equity=1000.0, peak_equity=1000.0)
    portfolio.open_position("BTC", direction=1, size_usd=50.0, price=100.0, fee_usd=0.13, action="BTC_LONG", now=1)

    equity = portfolio.equity({"BTC": 100.0})
    assert equity == pytest.approx(1000.0 - 0.13, abs=1e-6)
    assert portfolio.drawdown_pct({"BTC": 100.0}) < 0.1
