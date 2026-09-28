"""Renders the Jev AI Decision Bot's dedicated dashboard: status, equity,
open position detail, the decision panel (confidence/probabilities/
latency), the regime panel, the risk panel, and a live scrolling decision
stream so it's possible to see *why* the system traded or refused to."""

from __future__ import annotations

import html
import json
import time

from trading_hub import config
from trading_hub.jev_bot import JevBot

_PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<title>Jev AI Decision Bot</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background: #0b0f14; color: #e6edf3; margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  h2 {{ font-size: 15px; color: #c9d1d9; margin: 24px 0 8px; }}
  .updated {{ color: #8b949e; font-size: 12px; margin-bottom: 20px; }}
  .status {{ display: inline-block; padding: 4px 12px; border-radius: 6px; font-weight: 700; font-size: 13px; letter-spacing: 0.04em; }}
  .status.live {{ background: #1a3a24; color: #3fb950; }}
  .status.paused {{ background: #2d2a1a; color: #d29922; }}
  .status.halted {{ background: #3a1a1a; color: #f85149; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; min-width: 150px; }}
  .card .label {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em; }}
  .card .value {{ font-size: 20px; font-weight: 600; margin-top: 4px; }}
  .card .sub {{ color: #8b949e; font-size: 11px; margin-top: 2px; }}
  .pos {{ color: #3fb950; }}
  .neg {{ color: #f85149; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 8px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #30363d; font-size: 13px; }}
  th {{ color: #8b949e; font-weight: 500; }}
  .stream {{ background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 12px; max-height: 420px; overflow-y: auto; font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.6; }}
  .stream .row {{ display: flex; gap: 12px; padding: 2px 0; }}
  .stream .ts {{ color: #8b949e; flex-shrink: 0; }}
  .stream .action {{ font-weight: 600; flex-shrink: 0; width: 110px; }}
  .stream .note.approved {{ color: #3fb950; }}
  .stream .note.rejected {{ color: #f85149; }}
  .stream .note.hold {{ color: #8b949e; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
  .prob-bar {{ background: #21262d; border-radius: 4px; height: 6px; margin-top: 3px; overflow: hidden; }}
  .prob-fill {{ background: #3987e5; height: 100%; }}
</style>
</head>
<body>
  <h1>Jev AI Decision Bot <span class="status {status_class}">{status}</span></h1>
  <div class="updated">Last updated: {updated_at}</div>

  <div class="cards">
    <div class="card"><div class="label">Equity</div><div class="value">${equity:,.2f}</div></div>
    <div class="card"><div class="label">Cash</div><div class="value">${cash:,.2f}</div></div>
    <div class="card"><div class="label">Realized P&amp;L</div><div class="value {realized_class}">${realized_pnl:+,.2f}</div></div>
    <div class="card"><div class="label">Unrealized P&amp;L</div><div class="value {unrealized_class}">${unrealized_pnl:+,.2f}</div></div>
    <div class="card"><div class="label">Total P&amp;L</div><div class="value {total_class}">${total_pnl:+,.2f}</div></div>
    <div class="card"><div class="label">Return</div><div class="value {total_class}">{return_pct:+.2f}%</div></div>
  </div>
  <div class="cards">
    <div class="card"><div class="label">Profit / Minute</div><div class="value {ppm_class}">${profit_per_minute:+.3f}</div><div class="sub">target ${target_ppm:.2f}/min - {ppm_distance_label}</div></div>
    <div class="card"><div class="label">Elapsed</div><div class="value">{elapsed_minutes:.0f}m</div><div class="sub">target cum. ${target_cumulative:.2f} vs actual ${actual_cumulative:+.2f}</div></div>
  </div>

  <h2>Position</h2>
  <table>
    <tr><th>Asset</th><th>Side</th><th>Entry</th><th>Current</th><th>Size</th><th>Stop</th><th>Take Profit</th><th>Time Left</th><th>Unrealized P&amp;L</th></tr>
    {position_rows}
  </table>

  <h2>Jev Decision</h2>
  <div class="cards">
    <div class="card"><div class="label">Decision</div><div class="value">{decision_action}</div></div>
    <div class="card"><div class="label">Winner Probability</div><div class="value">{winner_probability:.1%}</div></div>
    <div class="card"><div class="label">2nd Probability</div><div class="value">{second_probability:.1%}</div></div>
    <div class="card"><div class="label">Latency</div><div class="value">{latency_ms:.0f}ms</div></div>
  </div>
  <div style="margin-bottom:16px;">{probability_bars}</div>

  <h2>Market Regime</h2>
  <div class="cards">
    <div class="card"><div class="label">Regime</div><div class="value">{regime}</div></div>
    <div class="card"><div class="label">Confidence</div><div class="value">{regime_confidence:.0%}</div></div>
    <div class="card"><div class="label">Risk Multiplier</div><div class="value">{risk_multiplier:.2f}x</div></div>
    <div class="card"><div class="label">Allowed Strategies</div><div class="value" style="font-size:14px;">{allowed_strategies}</div></div>
  </div>
  <div class="updated">{reasoning_summary}</div>

  <h2>Risk Governor</h2>
  <div class="cards">
    <div class="card"><div class="label">Drawdown</div><div class="value">{drawdown_pct:.2f}%</div></div>
    <div class="card"><div class="label">Consecutive Losses</div><div class="value">{consecutive_losses}</div></div>
    <div class="card"><div class="label">Total Exposure</div><div class="value">{exposure_pct:.1f}%</div></div>
    <div class="card"><div class="label">Circuit Breaker</div><div class="value" style="font-size:14px;">{circuit_breaker_state}</div></div>
  </div>

  <h2>Live Decision Stream</h2>
  <div class="stream">
    {stream_rows}
  </div>
</body>
</html>
"""


def _outcome_class(note: str) -> str:
    if "APPROVED" in note:
        return "approved"
    if "REJECTED" in note:
        return "rejected"
    return "hold"


def _pnl_class(value: float) -> str:
    return "pos" if value >= 0 else "neg"


def render(bot: JevBot, prices: dict[str, float]) -> str:
    portfolio = bot.portfolio
    equity = portfolio.equity(prices)
    realized_pnl = sum(t.pnl_usd for t in portfolio.trade_log)
    unrealized_pnl = sum(p.unrealized_pnl(prices.get(a, p.entry_price)) for a, p in portfolio.positions.items())
    total_pnl = equity - portfolio.starting_equity
    return_pct = (total_pnl / portfolio.starting_equity * 100) if portfolio.starting_equity else 0.0

    if portfolio.halted:
        status, status_class = "HALTED", "halted"
    elif portfolio.is_paused(int(time.time())):
        status, status_class = "PAUSED", "paused"
    else:
        status, status_class = "LIVE", "live"

    first_trade_ts = portfolio.trade_log[0].timestamp if portfolio.trade_log else time.time()
    elapsed_minutes = max((time.time() - first_trade_ts) / 60, 1e-9) if portfolio.trade_log else 0.0
    profit_per_minute = (total_pnl / elapsed_minutes) if elapsed_minutes > 0 else 0.0
    target_cumulative = config.TARGET_PROFIT_PER_MINUTE * elapsed_minutes
    ppm_distance = total_pnl - target_cumulative
    ppm_distance_label = f"{'+' if ppm_distance >= 0 else ''}{ppm_distance:.2f} vs target"

    position_rows = ""
    now = int(time.time())
    for asset, pos in portfolio.positions.items():
        current = prices.get(asset, pos.entry_price)
        side = "LONG" if pos.direction == 1 else "SHORT"
        upnl = pos.unrealized_pnl(current)
        time_left = max(0, (pos.time_limit_at or now) - now)
        position_rows += (
            f"<tr><td>{html.escape(asset)}</td><td>{side}</td><td>${pos.entry_price:,.4f}</td>"
            f"<td>${current:,.4f}</td><td>${pos.size_usd:,.2f}</td>"
            f"<td>{f'${pos.stop_price:,.4f}' if pos.stop_price else '-'}</td>"
            f"<td>{f'${pos.take_profit_price:,.4f}' if pos.take_profit_price else '-'}</td>"
            f"<td>{time_left}s</td><td class='{_pnl_class(upnl)}'>${upnl:+,.2f}</td></tr>"
        )
    if not position_rows:
        position_rows = "<tr><td colspan='9'>No open position</td></tr>"

    decision = bot.last_decision
    verdict = bot.last_verdict
    decision_action = decision.action if decision else "-"
    winner_probability = decision.winner_probability if decision else 0.0
    second_probability = decision.second_probability if decision else 0.0
    latency_ms = decision.latency_ms if decision else 0.0
    probabilities = decision.probabilities if decision else {}
    probability_bars = "".join(
        f"<div style='margin-bottom:4px;font-size:12px;'>{html.escape(a)} - {p:.1%}"
        f"<div class='prob-bar'><div class='prob-fill' style='width:{p*100:.1f}%;'></div></div></div>"
        for a, p in sorted(probabilities.items(), key=lambda kv: -kv[1])[:6]
    ) or "<div style='color:#8b949e;font-size:12px;'>no decision yet</div>"

    regime = bot.last_regime
    regime_name = regime.regime if regime else "-"
    regime_confidence = regime.confidence if regime else 0.0
    risk_multiplier = regime.risk_multiplier if regime else 0.0
    allowed_strategies = ", ".join(regime.allowed_strategies) if regime and regime.allowed_strategies else "none"
    reasoning_summary = html.escape(regime.reasoning_summary) if regime else "not yet assessed"

    drawdown_pct = portfolio.drawdown_pct(prices)
    exposure_pct = (sum(p.size_usd for p in portfolio.positions.values()) / equity * 100) if equity else 0.0
    if portfolio.halted:
        circuit_breaker_state = f"HALTED: {html.escape(portfolio.halted_reason)}"
    elif portfolio.is_paused(now):
        circuit_breaker_state = f"cooldown, {portfolio.trading_paused_until - now}s left"
    else:
        circuit_breaker_state = "normal"

    stream_rows = "".join(
        f"<div class='row'><span class='ts'>{time.strftime('%H:%M:%S', time.localtime(row['timestamp']))}</span>"
        f"<span class='action'>{html.escape(row['action'])}</span>"
        f"<span>conf {row['confidence']:.2f}</span>"
        f"<span class='note {_outcome_class(row['note'])}'>{html.escape(row['note'])}</span></div>"
        for row in reversed(bot.decision_stream[-config.DECISION_STREAM_MAX_ROWS:])
    ) or "<div style='color:#8b949e;'>no decisions yet</div>"

    return _PAGE_TEMPLATE.format(
        status=status, status_class=status_class,
        updated_at=time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        equity=equity, cash=portfolio.cash,
        realized_pnl=realized_pnl, realized_class=_pnl_class(realized_pnl),
        unrealized_pnl=unrealized_pnl, unrealized_class=_pnl_class(unrealized_pnl),
        total_pnl=total_pnl, total_class=_pnl_class(total_pnl),
        return_pct=return_pct,
        profit_per_minute=profit_per_minute, ppm_class=_pnl_class(profit_per_minute), target_ppm=config.TARGET_PROFIT_PER_MINUTE,
        ppm_distance_label=ppm_distance_label,
        elapsed_minutes=elapsed_minutes, target_cumulative=target_cumulative, actual_cumulative=total_pnl,
        position_rows=position_rows,
        decision_action=html.escape(decision_action), winner_probability=winner_probability, second_probability=second_probability, latency_ms=latency_ms,
        probability_bars=probability_bars,
        regime=html.escape(regime_name), regime_confidence=regime_confidence, risk_multiplier=risk_multiplier, allowed_strategies=html.escape(allowed_strategies),
        reasoning_summary=reasoning_summary,
        drawdown_pct=drawdown_pct, consecutive_losses=portfolio.consecutive_losses, exposure_pct=exposure_pct, circuit_breaker_state=circuit_breaker_state,
        stream_rows=stream_rows,
    )


def write(bot: JevBot, prices: dict[str, float]) -> None:
    with open(bot.html_path, "w") as f:
        f.write(render(bot, prices))

    portfolio = bot.portfolio
    decision = bot.last_decision
    verdict = bot.last_verdict
    regime = bot.last_regime
    state = {
        "updated_at": int(time.time()),
        "prices": prices,
        "equity": portfolio.equity(prices),
        "cash": portfolio.cash,
        "realized_pnl": sum(t.pnl_usd for t in portfolio.trade_log),
        "drawdown_pct": portfolio.drawdown_pct(prices),
        "halted": portfolio.halted,
        "halted_reason": portfolio.halted_reason,
        "paused": portfolio.is_paused(int(time.time())),
        "decision": {
            "action": decision.action, "winner_probability": decision.winner_probability,
            "second_probability": decision.second_probability, "probabilities": decision.probabilities,
            "latency_ms": decision.latency_ms, "source": decision.source,
        } if decision else None,
        "risk": {"outcome": verdict.outcome, "reason": verdict.reason} if verdict else None,
        "regime": {
            "regime": regime.regime, "confidence": regime.confidence, "risk_multiplier": regime.risk_multiplier,
            "allowed_assets": regime.allowed_assets, "reasoning_summary": regime.reasoning_summary,
        } if regime else None,
        "positions": {
            asset: {
                "direction": pos.direction, "size_usd": pos.size_usd, "entry_price": pos.entry_price,
                "stop_price": pos.stop_price, "take_profit_price": pos.take_profit_price,
                "unrealized_pnl": pos.unrealized_pnl(prices.get(asset, pos.entry_price)),
            }
            for asset, pos in portfolio.positions.items()
        },
        "recent_trades": [
            {"asset": t.asset, "action": t.action, "pnl_usd": t.pnl_usd, "exit_reason": t.exit_reason, "timestamp": t.timestamp}
            for t in portfolio.trade_log[-20:]
        ],
    }
    with open(bot.state_path, "w") as f:
        json.dump(state, f, indent=2)
