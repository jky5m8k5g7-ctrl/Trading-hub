from trading_hub import backtest, walk_forward
from tests.conftest import make_candles, uptrend_prices, flat_prices


def _aligned_candles(prices):
    return make_candles(prices)


def test_no_data_returns_no_windows():
    windows = walk_forward.run_walk_forward({}, train_window=50, test_window=20)
    assert windows == []


def test_insufficient_data_for_one_full_window_returns_empty():
    prices = flat_prices(60)
    candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * 60)}
    windows = walk_forward.run_walk_forward(candles, train_window=50, test_window=50)
    assert windows == []


def test_produces_expected_number_of_windows():
    # 300 bars, train=100, test=50 -> windows start at 0, 50, 100, 150 (train_end+test_end <= 300)
    # window0: train[0:100] test[100:150]
    # window1: train[50:150] test[150:200]
    # window2: train[100:200] test[200:250]
    # window3: train[150:250] test[250:300]
    prices = uptrend_prices(300, step_pct=0.05)
    candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * 300)}
    windows = walk_forward.run_walk_forward(candles, train_window=100, test_window=50)
    assert len(windows) == 4


def test_windows_roll_forward_without_overlapping_test_slices():
    prices = uptrend_prices(300, step_pct=0.05)
    candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * 300)}
    windows = walk_forward.run_walk_forward(candles, train_window=100, test_window=50)

    for i in range(len(windows) - 1):
        # Each window's test slice ends exactly where the next window's
        # train slice ends minus the roll - concretely: no two windows'
        # test ranges overlap.
        assert windows[i].test_end <= windows[i + 1].test_start


def test_train_and_test_never_overlap_within_a_window():
    prices = uptrend_prices(300, step_pct=0.05)
    candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * 300)}
    windows = walk_forward.run_walk_forward(candles, train_window=100, test_window=50)

    for w in windows:
        assert w.train_end == w.test_start  # test starts exactly where train ends, no gap, no overlap


def test_each_window_has_summary_metrics():
    prices = uptrend_prices(300, step_pct=0.05)
    candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * 300)}
    windows = walk_forward.run_walk_forward(candles, train_window=100, test_window=50)

    for w in windows:
        assert "return_pct" in w.train_summary
        assert "return_pct" in w.test_summary
        assert "max_drawdown_pct" in w.test_summary


def test_aggregate_out_of_sample_with_no_windows():
    summary = walk_forward.aggregate_out_of_sample([])
    assert summary["window_count"] == 0


def test_aggregate_out_of_sample_combines_test_windows():
    prices = uptrend_prices(300, step_pct=0.05)
    candles = {"BTC": _aligned_candles(prices), "ETH": _aligned_candles([2000.0] * 300)}
    windows = walk_forward.run_walk_forward(candles, train_window=100, test_window=50)

    summary = walk_forward.aggregate_out_of_sample(windows)
    assert summary["window_count"] == len(windows)
    assert "mean_test_return_pct" in summary
    assert "profitable_window_rate_pct" in summary
    assert summary["best_window_return_pct"] >= summary["worst_window_return_pct"]
