"""Automated walk-forward validation: repeatedly calibrate on a train
window, evaluate out-of-sample on the next disjoint test window, roll
forward by the test window's width, and repeat until the data runs out.

This is what config.BACKTEST_TRAIN_WINDOW_CANDLES / BACKTEST_TEST_WINDOW_CANDLES
exist for. backtest.py provides the single-window, no-lookahead replay;
this module drives it repeatedly and never lets a test window's outcome
feed back into that same window's own train result - each window's train
and test slices are disjoint, and a later window's train slice never
overlaps an earlier window's test slice either (it starts exactly where
that test slice ended). This is a purely paper/historical exercise: it
places no live orders, spends no real capital, and exists only to build
confidence in a strategy or in the risk governor's limits before anything
is trusted to run "for real" (which, in this codebase, still only ever
means the existing paper account - see safety_guard.py).

The strategies under test today (trend/mean_reversion/breakout, or Jev via
a supplied decide_fn) are fixed rule sets, not numerically fitted models,
so "calibration" here means choosing/confirming a decision function and
its fixed config, not searching a parameter grid. To do real parameter
optimization, fit parameters only on each window's train slice and pass
the result into that window's test call - never touch the test slice
until scoring it.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_hub import backtest, config, jev_engine
from trading_hub.kraken_client import Candle


@dataclass
class WindowResult:
    window_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_summary: dict
    test_summary: dict


def run_walk_forward(
    candles_by_asset: dict[str, list[Candle]],
    decide_fn=jev_engine.decide,
    starting_cash: float = config.STARTING_CASH_USD,
    train_window: int = config.BACKTEST_TRAIN_WINDOW_CANDLES,
    test_window: int = config.BACKTEST_TEST_WINDOW_CANDLES,
) -> list[WindowResult]:
    """Returns one WindowResult per completed train/test pair. A window is
    only included once its full test slice fits within the supplied data -
    a partial trailing window is dropped rather than silently truncated."""
    lengths = {len(c) for c in candles_by_asset.values()}
    if not lengths:
        return []
    n = min(lengths)

    windows: list[WindowResult] = []
    train_start = 0
    window_index = 0

    while True:
        train_end = train_start + train_window
        test_start = train_end
        test_end = test_start + test_window
        if test_end > n:
            break

        train_result = backtest.run_backtest(
            candles_by_asset, starting_cash=starting_cash, decide_fn=decide_fn,
            start_index=train_start, end_index=train_end,
        )
        test_result = backtest.run_backtest(
            candles_by_asset, starting_cash=starting_cash, decide_fn=decide_fn,
            start_index=test_start, end_index=test_end,
        )

        windows.append(WindowResult(
            window_index=window_index, train_start=train_start, train_end=train_end,
            test_start=test_start, test_end=test_end,
            train_summary=backtest.summarize(train_result), test_summary=backtest.summarize(test_result),
        ))

        train_start += test_window
        window_index += 1

    return windows


def aggregate_out_of_sample(windows: list[WindowResult]) -> dict:
    """Rolls every window's out-of-sample (test) result into one summary -
    the number that actually matters for "does this generalize", as
    opposed to any single window's in-sample train performance."""
    if not windows:
        return {"window_count": 0}

    test_returns = [w.test_summary["return_pct"] for w in windows]
    test_net_pnls = [w.test_summary["net_pnl"] for w in windows]
    profitable_windows = sum(1 for r in test_returns if r > 0)

    return {
        "window_count": len(windows),
        "profitable_windows": profitable_windows,
        "profitable_window_rate_pct": profitable_windows / len(windows) * 100,
        "mean_test_return_pct": sum(test_returns) / len(test_returns),
        "total_test_net_pnl": sum(test_net_pnls),
        "worst_window_return_pct": min(test_returns),
        "best_window_return_pct": max(test_returns),
    }
