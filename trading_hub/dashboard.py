"""Renders a static, auto-refreshing HTML dashboard from the current pipeline state."""

from __future__ import annotations

import html
import json
import time
from dataclasses import asdict

from trading_hub import config
from trading_hub.jev_engine import Decision
from trading_hub.portfolio import Portfolio
from trading_hub.reasoning_layer import RegimeAssessment
from trading_hub.risk_engine import RiskVerdict

_EQUITY_LINE_COLOR = "#3987e5"  # validated dark-mode categorical slot 1 (dataviz skill palette)
_CHART_WIDTH = 680
_CHART_HEIGHT = 180
_CHART_PAD = {"left": 56, "right": 12, "top": 16, "bottom": 24}

_PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>Trading Hub - Live Paper Dashboard</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background: #0b0f14; color: #e6edf3; margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .updated {{ color: #8b949e; font-size: 12px; margin-bottom: 20px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; min-width: 160px; }}
  .card .label {{ color: #8b949e; font-size: 12px; text-transform: uppercase; }}
  .card .value {{ font-size: 22px; font-weight: 600; margin-top: 4px; }}
  .pos {{ color: #3fb950; }}
  .neg {{ color: #f85149; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #30363d; font-size: 13px; }}
  th {{ color: #8b949e; font-weight: 500; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
  .badge.approved {{ background: #1a3a24; color: #3fb950; }}
  .badge.rejected {{ background: #3a1a1a; color: #f85149; }}
  .badge.hold {{ background: #2d2a1a; color: #d29922; }}
  .ticker-strip {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }}
  .ticker {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 16px; min-width: 120px; }}
  .ticker .asset {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }}
  .ticker .price {{ font-size: 16px; font-weight: 600; margin-top: 2px; font-variant-numeric: tabular-nums; }}
</style>
</head>
<body>
  <h1>Trading Hub - Live Paper Dashboard</h1>
  <div class="updated">Last updated: {updated_at}</div>

  <div class="ticker-strip">
    {ticker_strip}
  </div>

  <div class="cards">
    <div class="card"><div class="label">Equity</div><div class="value">${equity:,.2f}</div></div>
    <div class="card"><div class="label">Cash</div><div class="value">${cash:,.2f}</div></div>
    <div class="card"><div class="label">Drawdown</div><div class="value">{drawdown:.2f}%</div></div>
    <div class="card"><div class="label">Last Decision</div><div class="value">{action}</div></div>
    <div class="card"><div class="label">Confidence</div><div class="value">{confidence:.1f}%</div></div>
    <div class="card"><div class="label">Risk Verdict</div><div class="value">{risk_badge}</div></div>
    <div class="card"><div class="label">Market Regime</div><div class="value">{regime}</div></div>
  </div>
  <div class="updated">Regime source: {regime_source} - {regime_rationale}</div>

  <h2>Equity Curve</h2>
  <div class="card" style="min-width: unset;">
    {equity_chart}
  </div>

  <h2>Open Positions</h2>
  <table>
    <tr><th>Asset</th><th>Direction</th><th>Size (USD)</th><th>Entry Price</th><th>Unrealized PnL</th></tr>
    {positions_rows}
  </table>

  <h2>Recent Trades</h2>
  <table>
    <tr><th>Time</th><th>Action</th><th>Size (USD)</th><th>Price</th><th>Fee</th><th>PnL</th></tr>
    {trades_rows}
  </table>
</body>
</html>
"""


def _pnl_class(pnl: float) -> str:
    return "pos" if pnl >= 0 else "neg"


def render_ticker_strip(prices: dict[str, float], previous_prices: dict[str, float] | None = None) -> str:
    """Live per-asset price ticker for every configured asset, regardless of
    whether there's an open position - colored by move since the previous
    render so it visibly ticks."""
    previous_prices = previous_prices or {}
    cards = []
    for asset in config.ASSETS:
        price = prices.get(asset)
        if price is None:
            cards.append(f'<div class="ticker"><div class="asset">{html.escape(asset)}</div><div class="price">-</div></div>')
            continue
        prev = previous_prices.get(asset)
        if prev is None or price == prev:
            arrow, css_class = "", ""
        elif price > prev:
            arrow, css_class = " ▲", "pos"
        else:
            arrow, css_class = " ▼", "neg"
        decimals = 2 if price >= 1 else 6
        cards.append(
            f'<div class="ticker"><div class="asset">{html.escape(asset)}</div>'
            f'<div class="price {css_class}">${price:,.{decimals}f}{arrow}</div></div>'
        )
    return "".join(cards)


def render_equity_chart(equity_history: list[dict], starting_equity: float) -> str:
    """Inline SVG line chart of equity over time; no external chart library
    since this page is opened as a local file. Single series (magnitude over
    time), so no legend is needed - the title names it."""
    if len(equity_history) < 2:
        return (
            f'<div style="height:{_CHART_HEIGHT}px;display:flex;align-items:center;'
            f'justify-content:center;color:#8b949e;font-size:13px;">'
            f"Not enough history yet - equity chart fills in after a few cycles.</div>"
        )

    w, h = _CHART_WIDTH, _CHART_HEIGHT
    pad = _CHART_PAD
    plot_w = w - pad["left"] - pad["right"]
    plot_h = h - pad["top"] - pad["bottom"]

    values = [p["equity"] for p in equity_history]
    timestamps = [p["timestamp"] for p in equity_history]
    lo = min(values + [starting_equity])
    hi = max(values + [starting_equity])
    span = hi - lo or max(abs(hi), 1.0) * 0.02  # avoid a zero-height scale on a flat line
    lo -= span * 0.1
    hi += span * 0.1
    span = hi - lo

    n = len(values)

    def x_at(i: int) -> float:
        return pad["left"] + (i / (n - 1)) * plot_w if n > 1 else pad["left"]

    def y_at(v: float) -> float:
        return pad["top"] + plot_h - ((v - lo) / span) * plot_h

    points = [(x_at(i), y_at(v)) for i, v in enumerate(values)]
    poly_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    baseline_y = pad["top"] + plot_h
    area_path = f"M{points[0][0]:.1f},{baseline_y:.1f} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in points) + f" L{points[-1][0]:.1f},{baseline_y:.1f} Z"

    start_y = y_at(starting_equity)
    end_value = values[-1]
    end_color = "#3fb950" if end_value >= starting_equity else "#f85149"

    start_label_time = time.strftime("%H:%M", time.localtime(timestamps[0]))
    end_label_time = time.strftime("%H:%M", time.localtime(timestamps[-1]))

    js_points = json.dumps([[round(x, 1), round(y, 1), round(v, 2), t] for (x, y), v, t in zip(points, values, timestamps)])

    return f"""
<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" id="equity-chart" style="overflow:visible;">
  <defs>
    <linearGradient id="equity-fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{_EQUITY_LINE_COLOR}" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="{_EQUITY_LINE_COLOR}" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <line x1="{pad['left']}" y1="{start_y:.1f}" x2="{w - pad['right']}" y2="{start_y:.1f}"
        stroke="#8b949e" stroke-width="1" stroke-dasharray="3,4"/>
  <text x="{w - pad['right']}" y="{start_y - 5:.1f}" text-anchor="end" font-size="10" fill="#8b949e">start ${starting_equity:,.0f}</text>
  <path d="{area_path}" fill="url(#equity-fill)"/>
  <polyline points="{poly_points}" fill="none" stroke="{_EQUITY_LINE_COLOR}" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{points[-1][0]:.1f}" cy="{points[-1][1]:.1f}" r="3.5" fill="{end_color}"/>
  <text x="{points[-1][0]:.1f}" y="{points[-1][1] - 8:.1f}" text-anchor="end" font-size="11" font-weight="600" fill="{end_color}">${end_value:,.2f}</text>
  <text x="{pad['left']}" y="{h - 6}" font-size="10" fill="#8b949e">{start_label_time}</text>
  <text x="{w - pad['right']}" y="{h - 6}" text-anchor="end" font-size="10" fill="#8b949e">{end_label_time}</text>
  <line id="equity-crosshair" x1="0" y1="{pad['top']}" x2="0" y2="{baseline_y:.1f}" stroke="#e6edf3"
        stroke-width="1" stroke-dasharray="2,2" opacity="0" pointer-events="none"/>
  <circle id="equity-hover-dot" cx="0" cy="0" r="4" fill="{_EQUITY_LINE_COLOR}" opacity="0" pointer-events="none"/>
  <g id="equity-tooltip" opacity="0" pointer-events="none">
    <rect x="0" y="0" width="120" height="34" rx="4" fill="#161b22" stroke="#30363d"/>
    <text id="equity-tooltip-value" x="8" y="14" font-size="11" font-weight="600" fill="#e6edf3"></text>
    <text id="equity-tooltip-time" x="8" y="27" font-size="10" fill="#8b949e"></text>
  </g>
  <rect x="{pad['left']}" y="0" width="{plot_w}" height="{h}" fill="transparent"
        onmousemove="equityChartHover(event)" onmouseleave="equityChartHoverEnd()"/>
</svg>
<script>
(function() {{
  const points = {js_points};
  const svg = document.getElementById('equity-chart');
  window.equityChartHover = function(evt) {{
    const rect = svg.getBoundingClientRect();
    const scaleX = {w} / rect.width;
    const mouseX = (evt.clientX - rect.left) * scaleX;
    let nearest = points[0];
    let bestDist = Infinity;
    for (const p of points) {{
      const dist = Math.abs(p[0] - mouseX);
      if (dist < bestDist) {{ bestDist = dist; nearest = p; }}
    }}
    const [x, y, value, ts] = nearest;
    document.getElementById('equity-crosshair').setAttribute('x1', x);
    document.getElementById('equity-crosshair').setAttribute('x2', x);
    document.getElementById('equity-crosshair').setAttribute('opacity', 1);
    const dot = document.getElementById('equity-hover-dot');
    dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('opacity', 1);
    const tooltip = document.getElementById('equity-tooltip');
    const tx = Math.min(Math.max(x - 60, 4), {w} - 124);
    const ty = Math.max(y - 44, 4);
    tooltip.setAttribute('transform', 'translate(' + tx + ',' + ty + ')');
    tooltip.setAttribute('opacity', 1);
    document.getElementById('equity-tooltip-value').textContent = '$' + value.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
    document.getElementById('equity-tooltip-time').textContent = new Date(ts * 1000).toLocaleTimeString();
  }};
  window.equityChartHoverEnd = function() {{
    document.getElementById('equity-crosshair').setAttribute('opacity', 0);
    document.getElementById('equity-hover-dot').setAttribute('opacity', 0);
    document.getElementById('equity-tooltip').setAttribute('opacity', 0);
  }};
}})();
</script>
"""


def render_html(
    portfolio: Portfolio,
    prices: dict[str, float],
    decision: Decision,
    risk_verdict: RiskVerdict,
    regime: RegimeAssessment | None = None,
    previous_prices: dict[str, float] | None = None,
    refresh_seconds: int = 2,
) -> str:
    equity = portfolio.equity(prices)
    drawdown = portfolio.drawdown_pct(prices)

    if decision.action == "HOLD":
        risk_badge = '<span class="badge hold">HOLD</span>'
    elif risk_verdict.approved:
        risk_badge = '<span class="badge approved">APPROVED</span>'
    else:
        risk_badge = '<span class="badge rejected">REJECTED</span>'

    positions_rows = "".join(
        f"<tr><td>{html.escape(pos.asset)}</td>"
        f"<td>{'LONG' if pos.direction == 1 else 'SHORT'}</td>"
        f"<td>${pos.size_usd:,.2f}</td>"
        f"<td>${pos.entry_price:,.4f}</td>"
        f"<td class='{_pnl_class(pos.unrealized_pnl(prices.get(pos.asset, pos.entry_price)))}'>"
        f"${pos.unrealized_pnl(prices.get(pos.asset, pos.entry_price)):,.2f}</td></tr>"
        for pos in portfolio.positions.values()
    ) or "<tr><td colspan='5'>No open positions</td></tr>"

    trades_rows = "".join(
        f"<tr><td>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t.timestamp))}</td>"
        f"<td>{html.escape(t.action)}</td>"
        f"<td>${t.size_usd:,.2f}</td>"
        f"<td>${t.price:,.4f}</td>"
        f"<td>${t.fee_usd:,.4f}</td>"
        f"<td class='{_pnl_class(t.pnl_usd)}'>${t.pnl_usd:,.2f}</td></tr>"
        for t in reversed(portfolio.trade_log[-20:])
    ) or "<tr><td colspan='6'>No trades yet</td></tr>"

    return _PAGE_TEMPLATE.format(
        refresh_seconds=refresh_seconds,
        updated_at=time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        equity=equity,
        cash=portfolio.cash,
        drawdown=drawdown,
        action=html.escape(decision.action),
        confidence=decision.confidence,
        risk_badge=risk_badge,
        regime=html.escape(regime.regime) if regime else "n/a",
        regime_source=html.escape(regime.source) if regime else "n/a",
        regime_rationale=html.escape(regime.rationale) if regime else "not assessed",
        equity_chart=render_equity_chart(portfolio.equity_history, portfolio.starting_equity),
        ticker_strip=render_ticker_strip(prices, previous_prices),
        positions_rows=positions_rows,
        trades_rows=trades_rows,
    )


def _read_previous_prices(state_path: str) -> dict[str, float]:
    try:
        with open(state_path) as f:
            return json.load(f).get("prices", {})
    except (FileNotFoundError, ValueError):
        return {}


def write_dashboard(
    portfolio: Portfolio,
    prices: dict[str, float],
    decision: Decision,
    risk_verdict: RiskVerdict,
    regime: RegimeAssessment | None = None,
    html_path: str = config.DASHBOARD_HTML_PATH,
    state_path: str = config.STATE_JSON_PATH,
) -> None:
    previous_prices = _read_previous_prices(state_path)

    with open(html_path, "w") as f:
        f.write(render_html(portfolio, prices, decision, risk_verdict, regime, previous_prices))

    state = {
        "updated_at": int(time.time()),
        "prices": prices,
        "equity": portfolio.equity(prices),
        "cash": portfolio.cash,
        "drawdown_pct": portfolio.drawdown_pct(prices),
        "decision": {"action": decision.action, "confidence": decision.confidence},
        "risk": {"approved": risk_verdict.approved, "reason": risk_verdict.reason},
        "regime": {"regime": regime.regime, "source": regime.source, "rationale": regime.rationale} if regime else None,
        "positions": {
            asset: {**asdict(pos), "unrealized_pnl": pos.unrealized_pnl(prices.get(asset, pos.entry_price))}
            for asset, pos in portfolio.positions.items()
        },
        "recent_trades": [asdict(t) for t in portfolio.trade_log[-20:]],
    }
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)
