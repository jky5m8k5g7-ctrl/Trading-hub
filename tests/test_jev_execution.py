import random

from trading_hub import config, jev_execution
from trading_hub.live_feed import MarketSnapshot


def make_snapshot(bid=99.9, ask=100.1):
    return MarketSnapshot(asset="BTC", bid=bid, ask=ask, bid_qty=1.0, ask_qty=1.0, last=100.0, vwap=100.0, volume=1000.0, timestamp=0.0)


def test_maker_entry_fills_at_near_touch(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)  # force maker path
    fill = jev_execution.simulate_entry(make_snapshot(), side="LONG")
    assert fill.maker_or_taker == "maker"
    assert fill.fill_price == 99.9  # bid, for a LONG entry
    assert fill.fee_fraction == config.MAKER_FEE_FRACTION


def test_taker_entry_crosses_spread_with_slippage(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.999)  # force taker path
    fill = jev_execution.simulate_entry(make_snapshot(), side="LONG")
    assert fill.maker_or_taker == "taker"
    assert fill.fill_price > 100.1  # ask, plus adverse slippage
    assert fill.fee_fraction == config.TAKER_FEE_FRACTION


def test_short_maker_entry_fills_at_ask(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 0.0)
    fill = jev_execution.simulate_entry(make_snapshot(), side="SHORT")
    assert fill.fill_price == 100.1


def test_exit_always_crosses_the_spread():
    fill = jev_execution.simulate_exit(make_snapshot(), side="LONG")
    assert fill.maker_or_taker == "taker"
    assert fill.fill_price < 99.9  # sells below the bid due to slippage


def test_slippage_is_signed_against_the_trader():
    fill = jev_execution.simulate_exit(make_snapshot(), side="LONG")
    assert fill.slippage > 0  # a positive number = cost to the trader

    fill_short = jev_execution.simulate_exit(make_snapshot(), side="SHORT")
    assert fill_short.slippage > 0
