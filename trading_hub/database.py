"""SQLite-backed trade journal, decision log, risk-event log, and sessions.

This is the durable record the spec calls for: every Jev decision
(including HOLD), every rejection with its reason, every fill, and every
exit is written here, independent of the JSON portfolio snapshots
(portfolio.py) that drive fast restart recovery of open positions. A
database write failure here is treated as a HALT condition upstream
(risk_governor.py), never silently ignored, per the fail-safe requirement.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass

from trading_hub import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_name TEXT NOT NULL,
    session_name TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    duration_hours REAL,
    starting_balance REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    bot_name TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    action TEXT NOT NULL,
    asset TEXT,
    confidence REAL,
    winner_probability REAL,
    second_probability REAL,
    probabilities_json TEXT,
    regime TEXT,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    decision_latency_ms REAL
);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    bot_name TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    bot_name TEXT NOT NULL,
    asset TEXT NOT NULL,
    side TEXT NOT NULL,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER,
    entry_price REAL NOT NULL,
    exit_price REAL,
    quantity REAL NOT NULL,
    notional_usd REAL NOT NULL,
    stop_price REAL,
    take_profit_price REAL,
    gross_pnl REAL,
    fees REAL NOT NULL DEFAULT 0,
    slippage REAL NOT NULL DEFAULT 0,
    funding REAL NOT NULL DEFAULT 0,
    net_pnl REAL,
    holding_time_seconds REAL,
    mfe REAL,
    mae REAL,
    exit_reason TEXT,
    regime TEXT,
    jev_confidence REAL,
    equity_after REAL,
    maker_or_taker TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_bot_time ON decisions(bot_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_bot_time ON trades(bot_name, opened_at);
CREATE INDEX IF NOT EXISTS idx_risk_events_bot_time ON risk_events(bot_name, timestamp);
"""


class DatabaseError(RuntimeError):
    """Raised on any journal write/read failure. Callers must treat this as
    a HALT condition, not something to retry-and-ignore."""


@contextmanager
def _connect(db_path: str = config.DATABASE_PATH):
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise DatabaseError(str(exc)) from exc
    finally:
        conn.close()


def init_db(db_path: str = config.DATABASE_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def get_or_create_active_session(bot_name: str, starting_balance: float, session_name: str = "default", duration_hours: float | None = None, db_path: str = config.DATABASE_PATH) -> int:
    """Returns the active session id for this bot, creating one if none
    exists. Never resets an existing active session's balance implicitly -
    callers must explicitly close/start a new one to reset."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE bot_name = ? AND active = 1 ORDER BY id DESC LIMIT 1", (bot_name,)
        ).fetchone()
        if row is not None:
            return row["id"]

        cur = conn.execute(
            "INSERT INTO sessions (bot_name, session_name, started_at, duration_hours, starting_balance, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (bot_name, session_name, int(time.time()), duration_hours, starting_balance),
        )
        return cur.lastrowid


def start_new_session(bot_name: str, starting_balance: float, session_name: str, duration_hours: float | None = None, db_path: str = config.DATABASE_PATH) -> int:
    """Explicitly closes any active session for this bot and starts a fresh
    one. This is the only way a bot's tracked session (and by extension its
    "since session start" numbers) resets - never automatic."""
    with _connect(db_path) as conn:
        conn.execute("UPDATE sessions SET active = 0 WHERE bot_name = ? AND active = 1", (bot_name,))
        cur = conn.execute(
            "INSERT INTO sessions (bot_name, session_name, started_at, duration_hours, starting_balance, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (bot_name, session_name, int(time.time()), duration_hours, starting_balance),
        )
        return cur.lastrowid


def record_decision(
    bot_name: str,
    session_id: int | None,
    action: str,
    asset: str | None,
    confidence: float,
    winner_probability: float | None,
    second_probability: float | None,
    probabilities: dict[str, float] | None,
    regime: str | None,
    outcome: str,
    reason: str,
    decision_latency_ms: float | None,
    db_path: str = config.DATABASE_PATH,
) -> None:
    """Persist every decision Jev makes, including HOLD and rejections."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO decisions (session_id, bot_name, timestamp, action, asset, confidence, "
            "winner_probability, second_probability, probabilities_json, regime, outcome, reason, decision_latency_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, bot_name, int(time.time()), action, asset, confidence,
                winner_probability, second_probability,
                json.dumps(probabilities) if probabilities else None,
                regime, outcome, reason, decision_latency_ms,
            ),
        )


def record_risk_event(bot_name: str, session_id: int | None, event_type: str, detail: str, db_path: str = config.DATABASE_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO risk_events (session_id, bot_name, timestamp, event_type, detail) VALUES (?, ?, ?, ?, ?)",
            (session_id, bot_name, int(time.time()), event_type, detail),
        )


@dataclass
class TradeRecord:
    bot_name: str
    session_id: int | None
    asset: str
    side: str
    opened_at: int
    entry_price: float
    quantity: float
    notional_usd: float
    stop_price: float | None = None
    take_profit_price: float | None = None
    regime: str | None = None
    jev_confidence: float | None = None
    maker_or_taker: str | None = None
    closed_at: int | None = None
    exit_price: float | None = None
    gross_pnl: float | None = None
    fees: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0
    net_pnl: float | None = None
    holding_time_seconds: float | None = None
    mfe: float | None = None
    mae: float | None = None
    exit_reason: str | None = None
    equity_after: float | None = None


def record_trade(trade: TradeRecord, db_path: str = config.DATABASE_PATH) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO trades (session_id, bot_name, asset, side, opened_at, closed_at, entry_price, exit_price, "
            "quantity, notional_usd, stop_price, take_profit_price, gross_pnl, fees, slippage, funding, net_pnl, "
            "holding_time_seconds, mfe, mae, exit_reason, regime, jev_confidence, equity_after, maker_or_taker) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade.session_id, trade.bot_name, trade.asset, trade.side, trade.opened_at, trade.closed_at,
                trade.entry_price, trade.exit_price, trade.quantity, trade.notional_usd, trade.stop_price,
                trade.take_profit_price, trade.gross_pnl, trade.fees, trade.slippage, trade.funding, trade.net_pnl,
                trade.holding_time_seconds, trade.mfe, trade.mae, trade.exit_reason, trade.regime,
                trade.jev_confidence, trade.equity_after, trade.maker_or_taker,
            ),
        )
        return cur.lastrowid


def fetch_trades(bot_name: str, session_id: int | None = None, db_path: str = config.DATABASE_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        if session_id is not None:
            rows = conn.execute("SELECT * FROM trades WHERE bot_name = ? AND session_id = ? ORDER BY opened_at", (bot_name, session_id)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM trades WHERE bot_name = ? ORDER BY opened_at", (bot_name,)).fetchall()
        return [dict(r) for r in rows]


def fetch_recent_decisions(bot_name: str, limit: int = config.DECISION_STREAM_MAX_ROWS, db_path: str = config.DATABASE_PATH) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE bot_name = ? ORDER BY id DESC LIMIT ?", (bot_name, limit)
        ).fetchall()
        return [dict(r) for r in rows]
