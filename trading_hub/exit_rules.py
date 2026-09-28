"""Deterministic exit rules for open positions: stop-loss, take-profit,
time-stop, and a trailing stop that arms after +1R.

Jev may request an early exit (main.py can still close on a signal
reversal), but it can never loosen, widen, or disable these hard exits -
this module is the only thing that decides a stop/target/time limit has
been hit, and it is pure arithmetic with no AI involved.
"""

from __future__ import annotations

from trading_hub import config
from trading_hub.portfolio import Position


def _r_multiple(position: Position, price: float) -> float:
    """How many R (initial risk units) price has moved in the position's favor."""
    if position.stop_price is None or position.entry_price == position.stop_price:
        return 0.0
    risk_per_unit = abs(position.entry_price - position.stop_price)
    favorable_move = (price - position.entry_price) * position.direction
    return favorable_move / risk_per_unit


def maybe_arm_trailing_stop(position: Position, price: float) -> None:
    """Arms the trailing stop once price has moved TRAILING_STOP_ACTIVATE_R
    in the position's favor, then ratchets it - never loosens - as price
    makes new favorable extremes."""
    if position.stop_price is None:
        return

    if not position.trailing_armed and _r_multiple(position, price) >= config.TRAILING_STOP_ACTIVATE_R:
        position.trailing_armed = True

    if not position.trailing_armed:
        return

    risk_per_unit = abs(position.entry_price - position.stop_price)
    trail_distance = risk_per_unit * config.TRAILING_STOP_DISTANCE_R
    candidate = price - trail_distance * position.direction

    if position.trailing_stop_price is None:
        position.trailing_stop_price = candidate
    elif position.direction == 1:
        position.trailing_stop_price = max(position.trailing_stop_price, candidate)
    else:
        position.trailing_stop_price = min(position.trailing_stop_price, candidate)


def check_exit(position: Position, price: float, now: int) -> str | None:
    """Returns an exit reason ("STOP_LOSS", "TRAILING_STOP", "TAKE_PROFIT",
    "TIME_STOP") if a hard exit condition is met this cycle, else None.
    Checked in priority order: a stop overrides a simultaneous target."""
    maybe_arm_trailing_stop(position, price)

    if position.stop_price is not None:
        hit_stop = (position.direction == 1 and price <= position.stop_price) or (
            position.direction == -1 and price >= position.stop_price
        )
        if hit_stop:
            return "STOP_LOSS"

    if position.trailing_armed and position.trailing_stop_price is not None:
        hit_trailing = (position.direction == 1 and price <= position.trailing_stop_price) or (
            position.direction == -1 and price >= position.trailing_stop_price
        )
        if hit_trailing:
            return "TRAILING_STOP"

    if position.take_profit_price is not None:
        hit_tp = (position.direction == 1 and price >= position.take_profit_price) or (
            position.direction == -1 and price <= position.take_profit_price
        )
        if hit_tp:
            return "TAKE_PROFIT"

    if position.time_limit_at is not None and now >= position.time_limit_at:
        return "TIME_STOP"

    return None
