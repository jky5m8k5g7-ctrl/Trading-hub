from trading_hub import database, leaderboard
from trading_hub.portfolio import Portfolio


def test_build_rows_with_no_data_falls_back_to_starting_balance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database.init_db()

    rows = leaderboard.build_rows()
    names = [r["name"] for r in rows]
    assert names == ["trend", "mean_reversion", "breakout", "jev"]
    for r in rows:
        assert r["equity"] == r["starting_balance"]
        assert r["trade_count"] == 0


def test_build_rows_reflects_persisted_portfolio_equity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database.init_db()

    portfolio = Portfolio(cash=1050.0, starting_equity=1000.0, peak_equity=1050.0)
    portfolio.record_equity({}, now=1)
    portfolio.save("portfolio_state_trend.json")

    rows = leaderboard.build_rows()
    trend_row = next(r for r in rows if r["name"] == "trend")
    assert trend_row["equity"] == 1050.0
    assert trend_row["net_pnl"] == 50.0


def test_render_produces_html_with_all_bots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database.init_db()

    html_out = leaderboard.render()
    assert "trend" in html_out
    assert "jev" in html_out
    assert "<table>" in html_out


def test_write_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    database.init_db()

    leaderboard.write("leaderboard.html")
    with open("leaderboard.html") as f:
        content = f.read()
    assert "Leaderboard" in content
