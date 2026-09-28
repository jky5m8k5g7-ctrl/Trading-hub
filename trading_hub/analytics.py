"""Performance analytics computed from a bot's trade journal (database.py).

Deliberately does not judge success by raw P&L alone - risk-adjusted and
cost-aware metrics matter more for evaluating real-world viability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_hub import config, database


@dataclass
class PerformanceReport:
    trade_count: int = 0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    win_loss_ratio: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    trades_per_hour: float = 0.0
    average_holding_time_seconds: float = 0.0
    maker_ratio: float = 0.0
    fees_paid: float = 0.0
    slippage_paid: float = 0.0
    funding_paid: float = 0.0
    best_asset: str | None = None
    worst_asset: str | None = None
    best_regime: str | None = None
    worst_regime: str | None = None
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    profit_per_minute: float = 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _downside_std(values: list[float]) -> float:
    downside = [v for v in values if v < 0]
    if not downside:
        return 0.0
    mean = sum(downside) / len(downside)
    variance = sum((v - mean) ** 2 for v in downside) / len(downside)
    return math.sqrt(variance)


def _max_drawdown_pct(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100)
    return worst


def compute_report(bot_name: str, starting_balance: float, session_id: int | None = None, db_path: str = config.DATABASE_PATH) -> PerformanceReport:
    trades = [t for t in database.fetch_trades(bot_name, session_id, db_path=db_path) if t.get("closed_at") is not None]
    report = PerformanceReport(trade_count=len(trades))
    if not trades:
        return report

    net_pnls = [t["net_pnl"] or 0.0 for t in trades]
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p <= 0]

    report.net_pnl = sum(net_pnls)
    report.return_pct = report.net_pnl / starting_balance * 100 if starting_balance else 0.0
    report.win_rate = len(wins) / len(trades) * 100
    report.loss_rate = len(losses) / len(trades) * 100
    report.average_win = sum(wins) / len(wins) if wins else 0.0
    report.average_loss = sum(losses) / len(losses) if losses else 0.0
    report.win_loss_ratio = abs(report.average_win / report.average_loss) if report.average_loss else 0.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    report.profit_factor = gross_profit / gross_loss if gross_loss else float("inf") if gross_profit else 0.0
    report.expectancy = report.net_pnl / len(trades)

    equity_curve = [starting_balance]
    running = starting_balance
    for pnl in net_pnls:
        running += pnl
        equity_curve.append(running)
    report.max_drawdown_pct = _max_drawdown_pct(equity_curve)
    report.calmar_ratio = report.return_pct / report.max_drawdown_pct if report.max_drawdown_pct else 0.0

    returns = [pnl / starting_balance for pnl in net_pnls] if starting_balance else net_pnls
    mean_return = sum(returns) / len(returns)
    std_return = _std(returns)
    report.sharpe_ratio = (mean_return / std_return * math.sqrt(len(returns))) if std_return else 0.0
    downside_std = _downside_std(returns)
    report.sortino_ratio = (mean_return / downside_std * math.sqrt(len(returns))) if downside_std else 0.0

    holding_times = [t["holding_time_seconds"] for t in trades if t.get("holding_time_seconds") is not None]
    report.average_holding_time_seconds = sum(holding_times) / len(holding_times) if holding_times else 0.0

    opened_ats = [t["opened_at"] for t in trades]
    closed_ats = [t["closed_at"] for t in trades if t.get("closed_at")]
    if opened_ats and closed_ats:
        span_hours = max((max(closed_ats) - min(opened_ats)) / 3600, 1e-9)
        report.trades_per_hour = len(trades) / span_hours
        elapsed_minutes = max((max(closed_ats) - min(opened_ats)) / 60, 1e-9)
        report.profit_per_minute = report.net_pnl / elapsed_minutes

    makers = [t for t in trades if t.get("maker_or_taker") == "maker"]
    report.maker_ratio = len(makers) / len(trades) * 100 if trades else 0.0
    report.fees_paid = sum(t.get("fees") or 0.0 for t in trades)
    report.slippage_paid = sum(t.get("slippage") or 0.0 for t in trades)
    report.funding_paid = sum(t.get("funding") or 0.0 for t in trades)

    by_asset: dict[str, float] = {}
    by_regime: dict[str, float] = {}
    for t in trades:
        by_asset[t["asset"]] = by_asset.get(t["asset"], 0.0) + (t["net_pnl"] or 0.0)
        if t.get("regime"):
            by_regime[t["regime"]] = by_regime.get(t["regime"], 0.0) + (t["net_pnl"] or 0.0)
        if t["side"] == "LONG":
            report.long_pnl += t["net_pnl"] or 0.0
        else:
            report.short_pnl += t["net_pnl"] or 0.0

    if by_asset:
        report.best_asset = max(by_asset, key=by_asset.get)
        report.worst_asset = min(by_asset, key=by_asset.get)
    if by_regime:
        report.best_regime = max(by_regime, key=by_regime.get)
        report.worst_regime = min(by_regime, key=by_regime.get)

    return report
