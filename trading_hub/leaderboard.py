"""Cross-bot leaderboard: starting capital, current equity, net P&L,
return %, max drawdown, trade count, win rate, profit factor, expectancy,
Sharpe, Sortino, and profit/minute for every bot - each computed strictly
from that bot's own persisted portfolio and trade journal. Capital is
never pooled: a strategy's success is judged on its own $-for-$ account.
"""

from __future__ import annotations

import html
import os
import time

from trading_hub import analytics, config
from trading_hub.portfolio import Portfolio

BOTS: list[tuple[str, float]] = [
    ("trend", config.STARTING_CASH_USD),
    ("mean_reversion", config.STARTING_CASH_USD),
    ("breakout", config.STARTING_CASH_USD),
    ("jev", config.JEV_STARTING_BALANCE),
]

_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="20">
<title>Trading Hub - Leaderboard</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background: #0b0f14; color: #e6edf3; margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .updated {{ color: #8b949e; font-size: 12px; margin-bottom: 20px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: right; padding: 8px 10px; border-bottom: 1px solid #30363d; white-space: nowrap; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ color: #8b949e; font-weight: 500; position: sticky; top: 0; background: #0b0f14; }}
  .pos {{ color: #3fb950; }}
  .neg {{ color: #f85149; }}
  a {{ color: #3987e5; text-decoration: none; font-weight: 600; }}
</style>
</head>
<body>
  <h1>Trading Hub - Leaderboard</h1>
  <div class="updated">Last updated: {updated_at} - capital is never pooled across bots; each row is independent.</div>
  <div style="overflow-x:auto;">
  <table>
    <tr>
      <th>Bot</th><th>Start</th><th>Equity</th><th>Net P&amp;L</th><th>Return</th><th>Max DD</th>
      <th>Trades</th><th>Win Rate</th><th>Profit Factor</th><th>Expectancy</th><th>Sharpe</th><th>Sortino</th><th>$/min</th>
    </tr>
    {rows}
  </table>
  </div>
</body>
</html>
"""


def _pnl_class(v: float) -> str:
    return "pos" if v >= 0 else "neg"


def _current_equity(bot_name: str, starting_balance: float) -> float:
    path = config.PORTFOLIO_STATE_PATH_TEMPLATE.format(bot=bot_name)
    if not os.path.exists(path):
        return starting_balance
    portfolio = Portfolio.load(path)
    if portfolio.equity_history:
        return portfolio.equity_history[-1]["equity"]
    return portfolio.cash


def build_rows() -> list[dict]:
    rows = []
    for bot_name, starting_balance in BOTS:
        report = analytics.compute_report(bot_name, starting_balance)
        equity = _current_equity(bot_name, starting_balance)
        rows.append({
            "name": bot_name,
            "starting_balance": starting_balance,
            "equity": equity,
            "net_pnl": equity - starting_balance,
            "return_pct": (equity - starting_balance) / starting_balance * 100 if starting_balance else 0.0,
            "max_drawdown_pct": report.max_drawdown_pct,
            "trade_count": report.trade_count,
            "win_rate": report.win_rate,
            "profit_factor": report.profit_factor,
            "expectancy": report.expectancy,
            "sharpe_ratio": report.sharpe_ratio,
            "sortino_ratio": report.sortino_ratio,
            "profit_per_minute": report.profit_per_minute,
        })
    return rows


def render() -> str:
    row_html_parts = []
    for r in build_rows():
        profit_factor_display = "inf" if r["profit_factor"] == float("inf") else f"{r['profit_factor']:.2f}"
        row_html_parts.append(
            f"<tr><td><a href='dashboard_{html.escape(r['name'])}.html'>{html.escape(r['name'])}</a></td>"
            f"<td>${r['starting_balance']:,.2f}</td>"
            f"<td>${r['equity']:,.2f}</td>"
            f"<td class='{_pnl_class(r['net_pnl'])}'>${r['net_pnl']:+,.2f}</td>"
            f"<td class='{_pnl_class(r['return_pct'])}'>{r['return_pct']:+.2f}%</td>"
            f"<td>{r['max_drawdown_pct']:.2f}%</td>"
            f"<td>{r['trade_count']}</td>"
            f"<td>{r['win_rate']:.1f}%</td>"
            f"<td>{profit_factor_display}</td>"
            f"<td class='{_pnl_class(r['expectancy'])}'>${r['expectancy']:+.3f}</td>"
            f"<td>{r['sharpe_ratio']:.2f}</td>"
            f"<td>{r['sortino_ratio']:.2f}</td>"
            f"<td class='{_pnl_class(r['profit_per_minute'])}'>${r['profit_per_minute']:+.3f}</td></tr>"
        )
    return _TEMPLATE.format(updated_at=time.strftime("%Y-%m-%d %H:%M:%S %Z"), rows="".join(row_html_parts))


def write(path: str = config.LEADERBOARD_HTML_PATH) -> None:
    with open(path, "w") as f:
        f.write(render())
