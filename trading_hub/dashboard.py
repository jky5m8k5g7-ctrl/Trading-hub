"""Renders a static, auto-refreshing HTML dashboard from the current pipeline state."""

from __future__ import annotations

import html
import json
import time
from dataclasses import asdict

from trading_hub import config
from trading_hub.jev_engine import Decision
from trading_hub.portfolio import Portfolio
from trading_hub.risk_engine import RiskVerdict

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
</style>
</head>
<body>
  <h1>Trading Hub - Live Paper Dashboard</h1>
  <div class="updated">Last updated: {updated_at}</div>

  <div class="cards">
    <div class="card"><div class="label">Equity</div><div class="value">${equity:,.2f}</div></div>
    <div class="card"><div class="label">Cash</div><div class="value">${cash:,.2f}</div></div>
    <div class="card"><div class="label">Drawdown</div><div class="value">{drawdown:.2f}%</div></div>
    <div class="card"><div class="label">Last Decision</div><div class="value">{action}</div></div>
    <div class="card"><div class="label">Confidence</div><div class="value">{confidence:.1f}%</div></div>
    <div class="card"><div class="label">Risk Verdict</div><div class="value">{risk_badge}</div></div>
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


def render_html(
    portfolio: Portfolio,
    prices: dict[str, float],
    decision: Decision,
    risk_verdict: RiskVerdict,
    refresh_seconds: int = config.POLL_INTERVAL_SECONDS,
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
        positions_rows=positions_rows,
        trades_rows=trades_rows,
    )


def write_dashboard(
    portfolio: Portfolio,
    prices: dict[str, float],
    decision: Decision,
    risk_verdict: RiskVerdict,
    html_path: str = config.DASHBOARD_HTML_PATH,
    state_path: str = config.STATE_JSON_PATH,
) -> None:
    with open(html_path, "w") as f:
        f.write(render_html(portfolio, prices, decision, risk_verdict))

    state = {
        "updated_at": int(time.time()),
        "equity": portfolio.equity(prices),
        "cash": portfolio.cash,
        "drawdown_pct": portfolio.drawdown_pct(prices),
        "decision": {"action": decision.action, "confidence": decision.confidence},
        "risk": {"approved": risk_verdict.approved, "reason": risk_verdict.reason},
        "positions": {
            asset: {**asdict(pos), "unrealized_pnl": pos.unrealized_pnl(prices.get(asset, pos.entry_price))}
            for asset, pos in portfolio.positions.items()
        },
        "recent_trades": [asdict(t) for t in portfolio.trade_log[-20:]],
    }
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)
