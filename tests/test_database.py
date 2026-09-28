from trading_hub import database


def test_init_db_is_idempotent(tmp_path):
    path = str(tmp_path / "test.db")
    database.init_db(path)
    database.init_db(path)  # must not raise on a second call


def test_get_or_create_active_session_is_stable(tmp_path):
    path = str(tmp_path / "test.db")
    database.init_db(path)
    sid1 = database.get_or_create_active_session("jev", 200.0, db_path=path)
    sid2 = database.get_or_create_active_session("jev", 200.0, db_path=path)
    assert sid1 == sid2


def test_start_new_session_closes_the_old_one_and_resets(tmp_path):
    path = str(tmp_path / "test.db")
    database.init_db(path)
    sid1 = database.get_or_create_active_session("jev", 200.0, db_path=path)
    sid2 = database.start_new_session("jev", 200.0, session_name="fresh-run", db_path=path)
    assert sid2 != sid1
    # The old session must no longer be "active" - a fresh get_or_create
    # should return the new one, not resurrect the old.
    sid3 = database.get_or_create_active_session("jev", 200.0, db_path=path)
    assert sid3 == sid2


def test_record_and_fetch_decision_round_trips(tmp_path):
    path = str(tmp_path / "test.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    database.record_decision(
        "jev", sid, "BTC_LONG", "BTC", 0.85, 0.85, 0.10, {"BTC_LONG": 0.85, "HOLD": 0.15},
        "TREND_UP", "APPROVED", "passed all checks", 42.0, db_path=path,
    )
    rows = database.fetch_recent_decisions("jev", db_path=path)
    assert len(rows) == 1
    assert rows[0]["action"] == "BTC_LONG"
    assert rows[0]["outcome"] == "APPROVED"
    assert rows[0]["session_id"] == sid


def test_hold_decisions_are_persisted_too(tmp_path):
    path = str(tmp_path / "test.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    database.record_decision("jev", sid, "HOLD", None, 0.0, None, None, None, "RANGE_BOUND", "HOLD", "no signal", 5.0, db_path=path)
    rows = database.fetch_recent_decisions("jev", db_path=path)
    assert rows[0]["action"] == "HOLD"


def test_record_and_fetch_trade_round_trips(tmp_path):
    path = str(tmp_path / "test.db")
    database.init_db(path)
    sid = database.get_or_create_active_session("jev", 200.0, db_path=path)
    trade = database.TradeRecord(
        bot_name="jev", session_id=sid, asset="BTC", side="LONG", opened_at=100,
        entry_price=100.0, quantity=0.3, notional_usd=30.0, closed_at=200,
        exit_price=104.0, gross_pnl=1.2, fees=0.08, net_pnl=1.12,
        holding_time_seconds=100.0, exit_reason="TAKE_PROFIT", equity_after=201.12,
    )
    database.record_trade(trade, db_path=path)
    trades = database.fetch_trades("jev", db_path=path)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "TAKE_PROFIT"
    assert trades[0]["net_pnl"] == 1.12


def test_restart_recovery_reads_back_same_session(tmp_path):
    """Simulates a process restart: a fresh get_or_create_active_session call
    after the db file already has an active session must resume it, not
    create a new one (open positions/trade history stay attached to it)."""
    path = str(tmp_path / "test.db")
    database.init_db(path)
    sid_before_restart = database.get_or_create_active_session("jev", 200.0, db_path=path)
    database.record_trade(
        database.TradeRecord(bot_name="jev", session_id=sid_before_restart, asset="BTC", side="LONG", opened_at=1, entry_price=100.0, quantity=0.3, notional_usd=30.0),
        db_path=path,
    )

    # "restart": re-init and re-fetch, as main.py would on process start.
    database.init_db(path)
    sid_after_restart = database.get_or_create_active_session("jev", 200.0, db_path=path)

    assert sid_after_restart == sid_before_restart
    assert len(database.fetch_trades("jev", db_path=path)) == 1
