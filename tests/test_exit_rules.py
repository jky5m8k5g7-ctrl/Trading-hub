from trading_hub import exit_rules
from trading_hub.portfolio import Position


def make_long(entry=100.0, stop=98.0, tp=104.0, time_limit=None):
    return Position(
        asset="BTC", direction=1, size_usd=30.0, entry_price=entry, opened_at=0,
        stop_price=stop, take_profit_price=tp, time_limit_at=time_limit,
        peak_favorable_price=entry, trough_adverse_price=entry,
    )


def make_short(entry=100.0, stop=102.0, tp=96.0, time_limit=None):
    return Position(
        asset="BTC", direction=-1, size_usd=30.0, entry_price=entry, opened_at=0,
        stop_price=stop, take_profit_price=tp, time_limit_at=time_limit,
        peak_favorable_price=entry, trough_adverse_price=entry,
    )


def test_long_stop_loss_triggers():
    pos = make_long()
    assert exit_rules.check_exit(pos, price=97.5, now=10) == "STOP_LOSS"


def test_long_take_profit_triggers():
    pos = make_long()
    assert exit_rules.check_exit(pos, price=104.5, now=10) == "TAKE_PROFIT"


def test_short_stop_loss_triggers():
    pos = make_short()
    assert exit_rules.check_exit(pos, price=102.5, now=10) == "STOP_LOSS"


def test_short_take_profit_triggers():
    pos = make_short()
    assert exit_rules.check_exit(pos, price=95.5, now=10) == "TAKE_PROFIT"


def test_no_exit_when_price_is_between_stop_and_target():
    pos = make_long()
    assert exit_rules.check_exit(pos, price=101.0, now=10) is None


def test_time_stop_triggers_after_limit():
    pos = make_long(time_limit=100)
    assert exit_rules.check_exit(pos, price=101.0, now=99) is None
    assert exit_rules.check_exit(pos, price=101.0, now=100) == "TIME_STOP"


def test_trailing_stop_arms_after_plus_1r_and_then_triggers():
    # Risk per unit = 100 - 98 = 2. +1R = 102.
    pos = make_long(entry=100.0, stop=98.0, tp=200.0)  # push TP far out of reach
    assert not pos.trailing_armed

    exit_rules.check_exit(pos, price=102.0, now=1)
    assert pos.trailing_armed
    assert pos.trailing_stop_price is not None

    # Trail distance = 2 * 0.5 = 1, so trailing stop should sit at 101.
    assert pos.trailing_stop_price == 101.0

    # Price advances further, trailing stop should ratchet up, never down.
    exit_rules.check_exit(pos, price=110.0, now=2)
    assert pos.trailing_stop_price == 109.0

    # Price pulls back below the trailing stop -> exit.
    reason = exit_rules.check_exit(pos, price=108.5, now=3)
    assert reason == "TRAILING_STOP"


def test_trailing_stop_never_loosens_on_a_pullback_that_does_not_break_it():
    pos = make_long(entry=100.0, stop=98.0, tp=200.0)
    exit_rules.check_exit(pos, price=110.0, now=1)
    trailed_at_110 = pos.trailing_stop_price

    # A pullback that doesn't breach the trailing stop must not move it down.
    exit_rules.check_exit(pos, price=109.5, now=2)
    assert pos.trailing_stop_price == trailed_at_110


def test_stop_loss_checked_even_after_trailing_armed():
    # If price gaps straight through the hard stop, STOP_LOSS still fires -
    # the trailing stop only ever tightens risk, never removes the floor.
    pos = make_long(entry=100.0, stop=98.0, tp=200.0)
    exit_rules.check_exit(pos, price=102.0, now=1)  # arms trailing
    assert exit_rules.check_exit(pos, price=97.0, now=2) == "STOP_LOSS"
