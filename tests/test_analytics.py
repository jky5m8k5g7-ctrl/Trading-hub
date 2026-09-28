from trading_hub import analytics, database


def _trade(session_id, asset="BTC", side="LONG", net_pnl=1.0, opened_at=0, closed_at=60, regime="TREND_UP", maker="maker", fees=0.05, slippage=0.02):
    return database.TradeRecord(
        bot_name="jev", session_id=session_id, asset=asset, side=side, opened_at=opened_at, closed_at=closed_at,
        entry_price=100.0, exit_price=101.0, quantity=0.3, notional_usd=30.0, gross_pnl=net_pnl + fees + slippage,
        fees=fees, slippage=slippage, net_pnl=net_pnl, holding_time_seconds=closed_at - opened_at,
        exit_reason="TAKE_PROFIT", regime=regime, maker_or_taker=maker,
    )


def test_empty_journal_reports_zero(tmp_path):
    path = str(tmp_path / "t.db")
    database.init_db(path)
    report = analytics.compute_report("jev", starting_balance=200.0, db_path=path)
    assert report.trade_count == 0
    assert report.net_pnl == 0.0


def test_basic_metrics_from_mixed_wins_and_losses(tmp_path):
    path = str(tmp_path / "t.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    database.record_trade(_trade(sid, net_pnl=4.0, opened_at=0, closed_at=60), db_path=path)
    database.record_trade(_trade(sid, net_pnl=-2.0, opened_at=60, closed_at=120), db_path=path)
    database.record_trade(_trade(sid, net_pnl=3.0, opened_at=120, closed_at=180), db_path=path)

    report = analytics.compute_report("jev", starting_balance=200.0, session_id=sid, db_path=path)

    assert report.trade_count == 3
    assert report.net_pnl == 5.0
    assert round(report.win_rate, 2) == round(2 / 3 * 100, 2)
    assert report.average_win == 3.5
    assert report.average_loss == -2.0
    assert report.profit_factor == 7.0 / 2.0
    assert round(report.expectancy, 4) == round(5.0 / 3, 4)


def test_only_closed_trades_count():
    pass  # covered implicitly: open positions have closed_at=None and are excluded


def test_open_trades_are_excluded_from_the_report(tmp_path):
    path = str(tmp_path / "t.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    open_trade = database.TradeRecord(bot_name="jev", session_id=sid, asset="BTC", side="LONG", opened_at=0, entry_price=100.0, quantity=0.3, notional_usd=30.0)
    database.record_trade(open_trade, db_path=path)
    database.record_trade(_trade(sid, net_pnl=1.0), db_path=path)

    report = analytics.compute_report("jev", starting_balance=200.0, session_id=sid, db_path=path)
    assert report.trade_count == 1


def test_best_and_worst_asset_and_regime(tmp_path):
    path = str(tmp_path / "t.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    database.record_trade(_trade(sid, asset="BTC", net_pnl=5.0, regime="TREND_UP"), db_path=path)
    database.record_trade(_trade(sid, asset="DOGE", net_pnl=-3.0, regime="HIGH_VOLATILITY"), db_path=path)

    report = analytics.compute_report("jev", starting_balance=200.0, session_id=sid, db_path=path)
    assert report.best_asset == "BTC"
    assert report.worst_asset == "DOGE"
    assert report.best_regime == "TREND_UP"
    assert report.worst_regime == "HIGH_VOLATILITY"


def test_long_and_short_pnl_split(tmp_path):
    path = str(tmp_path / "t.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    database.record_trade(_trade(sid, side="LONG", net_pnl=4.0), db_path=path)
    database.record_trade(_trade(sid, side="SHORT", net_pnl=-1.0), db_path=path)

    report = analytics.compute_report("jev", starting_balance=200.0, session_id=sid, db_path=path)
    assert report.long_pnl == 4.0
    assert report.short_pnl == -1.0


def test_fees_and_slippage_are_summed(tmp_path):
    path = str(tmp_path / "t.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    database.record_trade(_trade(sid, fees=0.10, slippage=0.05), db_path=path)
    database.record_trade(_trade(sid, fees=0.08, slippage=0.03), db_path=path)

    report = analytics.compute_report("jev", starting_balance=200.0, session_id=sid, db_path=path)
    assert round(report.fees_paid, 4) == 0.18
    assert round(report.slippage_paid, 4) == 0.08


def test_maker_ratio(tmp_path):
    path = str(tmp_path / "t.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    database.record_trade(_trade(sid, maker="maker"), db_path=path)
    database.record_trade(_trade(sid, maker="taker"), db_path=path)

    report = analytics.compute_report("jev", starting_balance=200.0, session_id=sid, db_path=path)
    assert report.maker_ratio == 50.0
