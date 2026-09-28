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
