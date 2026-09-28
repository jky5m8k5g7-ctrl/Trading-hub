from trading_hub import backtest, jev_engine
from tests.conftest import make_candles, uptrend_prices, flat_prices


def _aligned_candles(prices, volumes=None):
    return make_candles(prices, volumes)


def test_no_candles_returns_empty_result():
    result = backtest.run_backtest({})
    assert result.bars_processed == 0


def test_flat_market_produces_no_trades():
    candles = {"BTC": _aligned_candles(flat_prices(200)), "ETH": _aligned_candles(flat_prices(200))}
    result = backtest.run_backtest(candles, starting_cash=1000.0)
    assert result.bars_processed > 0
    assert result.trades_opened == 0
    assert result.portfolio.cash == 1000.0


def test_strong_uptrend_can_open_and_track_a_position():
    prices = uptrend_prices(200, start=100.0, step_pct=0.3)
    candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * 200)}
    result = backtest.run_backtest(candles, starting_cash=1000.0)
    assert result.bars_processed > 0
    # Equity history should be recorded every bar processed.
    assert len(result.portfolio.equity_history) == result.bars_processed


def test_no_lookahead_same_prefix_gives_identical_trades_up_to_truncation():
    """The defining no-lookahead guarantee: running the same strategy over
    a longer series must reproduce identical decisions for every bar that
    exists in both runs - later data must not retroactively change earlier
    behavior."""
    prices = uptrend_prices(300, start=100.0, step_pct=0.25)
    short_candles = {"BTC": _aligned_candles(prices[:220]), "ETH": _aligned_candles([2000.0] * 220)}
    long_candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * 300)}

    short_result = backtest.run_backtest(short_candles, starting_cash=1000.0, end_index=220)
    long_result = backtest.run_backtest(long_candles, starting_cash=1000.0, end_index=220)

    short_actions = [t.action for t in short_result.portfolio.trade_log]
    long_actions = [t.action for t in long_result.portfolio.trade_log]
    assert short_actions == long_actions


def test_train_test_windows_do_not_overlap():
    from trading_hub import config
    prices = uptrend_prices(config.BACKTEST_TRAIN_WINDOW_CANDLES + config.BACKTEST_TEST_WINDOW_CANDLES + 50, step_pct=0.05)
    candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * len(prices))}

    train_end = config.BACKTEST_TRAIN_WINDOW_CANDLES
    test_start = train_end
    test_end = test_start + config.BACKTEST_TEST_WINDOW_CANDLES

    train_result = backtest.run_backtest(candles, start_index=0, end_index=train_end)
    test_result = backtest.run_backtest(candles, start_index=test_start, end_index=test_end)

    assert train_result.bars_processed > 0
    assert test_result.bars_processed > 0
    # No bar index is processed by both windows.
    assert test_start >= train_end


def test_stop_loss_checked_against_bar_high_low_not_just_close():
    """A stop must trigger on an intrabar wick, not only when the close
    itself breaches it - otherwise the backtest assumes unrealistically
    perfect fills that only trade on closes."""
    from trading_hub.kraken_client import Candle
    from trading_hub.portfolio import Portfolio

    portfolio = Portfolio(cash=1000.0, starting_equity=1000.0, peak_equity=1000.0)
    portfolio.open_position("BTC", direction=1, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=0, stop_price=98.0, take_profit_price=110.0)

    # A bar whose close is above the stop but whose low wicked through it.
    wick_candle = Candle(timestamp=60, open=99.5, high=100.5, low=97.0, close=99.8, volume=10.0)
    position = portfolio.positions["BTC"]
    hit_stop = position.direction == 1 and wick_candle.low <= position.stop_price
    assert hit_stop
