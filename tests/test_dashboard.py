from trading_hub import dashboard
from trading_hub.portfolio import Portfolio


def test_equity_chart_shows_placeholder_with_insufficient_history():
    html = dashboard.render_equity_chart([], starting_equity=1000.0)
    assert "Not enough history" in html
    assert "<svg" not in html


def test_equity_chart_renders_svg_with_history():
    history = [
        {"timestamp": 1_700_000_000 + i * 60, "equity": 1000.0 + i * 5}
        for i in range(10)
    ]
    html = dashboard.render_equity_chart(history, starting_equity=1000.0)
    assert "<svg" in html
    assert "polyline" in html
    assert "$1,045.00" in html  # last point's direct label


def test_equity_chart_handles_flat_history_without_zero_division():
    history = [{"timestamp": 1_700_000_000 + i * 60, "equity": 1000.0} for i in range(5)]
    html = dashboard.render_equity_chart(history, starting_equity=1000.0)
    assert "<svg" in html


def test_write_dashboard_includes_chart_in_html(tmp_path):
    from trading_hub.jev_engine import Decision
    from trading_hub.risk_engine import RiskVerdict

    portfolio = Portfolio()
    portfolio.record_equity({}, now=1)
    portfolio.record_equity({}, now=2)
    decision = Decision(action="HOLD", confidence=0.0, asset=None, direction=0, verdicts={})
    verdict = RiskVerdict(approved=False, reason="no actionable decision (HOLD)")

    html_path = str(tmp_path / "dashboard.html")
    state_path = str(tmp_path / "state.json")
    dashboard.write_dashboard(portfolio, {}, decision, verdict, html_path=html_path, state_path=state_path)

    with open(html_path) as f:
        content = f.read()
    assert "<svg" in content


def test_ticker_strip_shows_every_configured_asset():
    prices = {"BTC": 83000.0, "ETH": 2650.0, "SOL": 119.0, "DOGE": 0.093, "XRP": 1.49}
    html_out = dashboard.render_ticker_strip(prices)
    for asset in prices:
        assert asset in html_out


def test_ticker_strip_shows_placeholder_for_missing_price():
    html_out = dashboard.render_ticker_strip({"BTC": 83000.0})
    assert "-" in html_out  # ETH/SOL/DOGE/XRP have no price yet


def test_ticker_strip_marks_up_and_down_moves():
    prices = {"BTC": 84000.0, "ETH": 2600.0}
    previous = {"BTC": 83000.0, "ETH": 2650.0}
    html_out = dashboard.render_ticker_strip(prices, previous)
    assert 'class="price pos"' in html_out
    assert 'class="price neg"' in html_out


def test_write_dashboard_persists_prices_for_next_ticker_comparison(tmp_path):
    from trading_hub.jev_engine import Decision
    from trading_hub.risk_engine import RiskVerdict

    portfolio = Portfolio()
    decision = Decision(action="HOLD", confidence=0.0, asset=None, direction=0, verdicts={})
    verdict = RiskVerdict(approved=False, reason="no actionable decision (HOLD)")
    html_path = str(tmp_path / "dashboard.html")
    state_path = str(tmp_path / "state.json")

    dashboard.write_dashboard(portfolio, {"BTC": 100.0}, decision, verdict, html_path=html_path, state_path=state_path)
    dashboard.write_dashboard(portfolio, {"BTC": 105.0}, decision, verdict, html_path=html_path, state_path=state_path)

    with open(html_path) as f:
        content = f.read()
    assert 'class="price pos"' in content
