"""Realistic simulated paper execution for the Jev bot.

Does not assume every order fills at the midpoint. Entries try a passive
maker order at the near-touch price first (MAKER_FILL_PROBABILITY chance of
filling before ORDER_TIMEOUT_SECONDS), falling back to a taker order that
crosses the spread with extra slippage - mirroring the two realistic
outcomes a resting limit order actually has. Exits are always modeled as
taker (crossing the spread) since a paper exit shouldn't assume a favorable
passive fill on the way out.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from trading_hub import config
from trading_hub.live_feed import MarketSnapshot


@dataclass
class Fill:
    expected_price: float  # the mid at decision time
    submitted_price: float  # the price the paper order was placed at
    fill_price: float  # the price it actually filled at
    slippage: float  # fill_price vs expected_price, signed against the trader
    fee_fraction: float
    maker_or_taker: str
    latency_ms: float
    fill_time: float


def simulate_entry(snapshot: MarketSnapshot, side: str) -> Fill:
    started = time.monotonic()
    expected_price = snapshot.mid
    maker_price = snapshot.bid if side == "LONG" else snapshot.ask
    taker_price = snapshot.ask if side == "LONG" else snapshot.bid

    if random.random() < config.MAKER_FILL_PROBABILITY:
        fill_price = maker_price
        maker_or_taker = "maker"
        fee_fraction = config.MAKER_FEE_FRACTION
        latency_ms = random.uniform(50, config.ORDER_TIMEOUT_SECONDS * 1000)
    else:
        slip_mult = 1 + (config.SLIPPAGE_BPS_PER_TAKER_ORDER / 10_000) * (1 if side == "LONG" else -1)
        fill_price = taker_price * slip_mult
        maker_or_taker = "taker"
        fee_fraction = config.TAKER_FEE_FRACTION
        latency_ms = random.uniform(20, 300)

    slippage = (fill_price - expected_price) if side == "LONG" else (expected_price - fill_price)
    return Fill(
        expected_price=expected_price,
        submitted_price=maker_price,
        fill_price=fill_price,
        slippage=slippage,
        fee_fraction=fee_fraction,
        maker_or_taker=maker_or_taker,
        latency_ms=(time.monotonic() - started) * 1000 + latency_ms,
        fill_time=time.time(),
    )


def simulate_exit(snapshot: MarketSnapshot, side: str) -> Fill:
    started = time.monotonic()
    expected_price = snapshot.mid
    # Closing a LONG sells into the bid; closing a SHORT buys at the ask.
    taker_price = snapshot.bid if side == "LONG" else snapshot.ask
    slip_mult = 1 - (config.SLIPPAGE_BPS_PER_TAKER_ORDER / 10_000) if side == "LONG" else 1 + (config.SLIPPAGE_BPS_PER_TAKER_ORDER / 10_000)
    fill_price = taker_price * slip_mult
    slippage = (expected_price - fill_price) if side == "LONG" else (fill_price - expected_price)
    return Fill(
        expected_price=expected_price,
        submitted_price=taker_price,
        fill_price=fill_price,
        slippage=slippage,
        fee_fraction=config.TAKER_FEE_FRACTION,
        maker_or_taker="taker",
        latency_ms=(time.monotonic() - started) * 1000 + random.uniform(20, 300),
        fill_time=time.time(),
    )
