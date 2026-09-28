from trading_hub import database, jev_engine, main
from trading_hub.jev_engine import AssetVerdict
from trading_hub.portfolio import Portfolio


class FakeLiveFeed:
    def __init__(self, prices, ages):
        self._prices = prices
        self._ages = ages

    def get_prices(self):
        return dict(self._prices)

    def age_seconds(self, asset):
        return self._ages.get(asset, float("inf"))


class FakeBot:
    """Minimal stand-in for main.Bot: manage_open_positions only needs
    .portfolio, .name, and .session_id."""

    def __init__(self, portfolio, name="test_bot"):
        self.portfolio = portfolio
        self.name = name
        self.session_id = database.get_or_create_active_session(name, portfolio.starting_equity)


def _bot_with_db(tmp_path, monkeypatch, cash=1000.0):
    # config.DATABASE_PATH is a relative path and database.py's functions
    # bind it as a default at import time, so chdir (not monkeypatching the
    # config value) is what actually isolates each test's database.
    monkeypatch.chdir(tmp_path)
    database.init_db()
    return FakeBot(Portfolio(cash=cash))


def test_manage_open_positions_closes_on_signal_flip(tmp_path, monkeypatch):
    bot = _bot_with_db(tmp_path, monkeypatch)
    bot.portfolio.open_position("BTC", direction=jev_engine.LONG, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)

    verdicts = {"BTC": AssetVerdict(asset="BTC", direction=jev_engine.SHORT, confidence=80.0, votes={})}
    main.manage_open_positions(bot, verdicts, {"BTC": 105.0})

    assert "BTC" not in bot.portfolio.positions
    assert bot.portfolio.trade_log[-1].action == "BTC_CLOSE"
    trades = database.fetch_trades(bot.name)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "SIGNAL_REVERSAL"


def test_manage_open_positions_closes_on_neutral_signal(tmp_path, monkeypatch):
    bot = _bot_with_db(tmp_path, monkeypatch)
    bot.portfolio.open_position("BTC", direction=jev_engine.LONG, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)

    verdicts = {"BTC": AssetVerdict(asset="BTC", direction=jev_engine.NEUTRAL, confidence=0.0, votes={})}
    main.manage_open_positions(bot, verdicts, {"BTC": 105.0})

    assert "BTC" not in bot.portfolio.positions


def test_manage_open_positions_leaves_aligned_position_open(tmp_path, monkeypatch):
    bot = _bot_with_db(tmp_path, monkeypatch)
    bot.portfolio.open_position("BTC", direction=jev_engine.LONG, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)

    verdicts = {"BTC": AssetVerdict(asset="BTC", direction=jev_engine.LONG, confidence=90.0, votes={})}
    main.manage_open_positions(bot, verdicts, {"BTC": 105.0})

    assert "BTC" in bot.portfolio.positions


def test_manage_open_positions_skips_assets_with_no_verdict_or_price(tmp_path, monkeypatch):
    bot = _bot_with_db(tmp_path, monkeypatch)
    bot.portfolio.open_position("BTC", direction=jev_engine.LONG, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)

    main.manage_open_positions(bot, {}, {})

    assert "BTC" in bot.portfolio.positions


def test_merge_live_prices_prefers_fresh_live_tick():
    candle_prices = {"BTC": 100.0, "ETH": 2000.0}
    live_feed = FakeLiveFeed(prices={"BTC": 101.5}, ages={"BTC": 1.0})

    merged = main.merge_live_prices(candle_prices, live_feed)

    assert merged["BTC"] == 101.5
    assert merged["ETH"] == 2000.0


def test_merge_live_prices_ignores_stale_live_tick():
    candle_prices = {"BTC": 100.0}
    live_feed = FakeLiveFeed(prices={"BTC": 999.0}, ages={"BTC": 999.0})

    merged = main.merge_live_prices(candle_prices, live_feed)

    assert merged["BTC"] == 100.0


def test_strategies_registry_covers_all_bots():
    assert set(main.STRATEGIES.keys()) == {"trend", "mean_reversion", "breakout"}
    for decide_fn in main.STRATEGIES.values():
        assert callable(decide_fn)


def test_bot_uses_isolated_state_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bot = main.Bot("trend", main.STRATEGIES["trend"])

    assert bot.html_path == "dashboard_trend.html"
    assert bot.state_path == "state_trend.json"
    assert bot.portfolio_path == "portfolio_state_trend.json"
    assert bot.portfolio.cash == bot.portfolio.starting_equity


def test_bot_summary_reflects_portfolio_and_last_decision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bot = main.Bot("trend", main.STRATEGIES["trend"])
    bot.portfolio.open_position("BTC", direction=1, size_usd=50.0, price=100.0, fee_usd=0.0, action="BTC_LONG", now=1)
    bot.decision = jev_engine.Decision(action="BTC_LONG", confidence=85.0, asset="BTC", direction=1, verdicts={})

    summary = bot.summary({"BTC": 110.0})

    assert summary["name"] == "trend"
    assert summary["open_positions"] == 1
    assert summary["last_action"] == "BTC_LONG"
    assert summary["last_confidence"] == 85.0
    assert summary["equity"] > summary["starting_equity"]
