"""Central configuration for the trading hub pipeline.

Every tunable value in the system lives here - nothing below is scattered
into the modules that use it. Two risk profiles coexist:

- The original three rule-based strategies (trend/mean_reversion/breakout
  in strategies.py + jev_engine.py) use the "legacy" block below: $1,000
  start, $50 flat position size, single confidence gate.
- The Jev AI Decision Bot (jev_decision.py, risk_governor.py) uses the
  JEV_* block: $200 start, volatility-adjusted position sizing and stops,
  a probability-gap + expected-edge gate on top of confidence, and hard
  circuit breakers (loss-streak cooldown, daily drawdown halt/de-risk).
  Jev's risk parameters are NOT configurable by Jev itself - risk_governor.py
  is the only thing that reads them, and nothing in the decision path can
  change them at runtime.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

# Kraken pair codes keyed by our internal asset symbol. Chosen from Kraken's
# full ~670-pair USD market by ranking 24h quote volume (volume * vwap) via
# the public Ticker endpoint and keeping the top 10 - see the research in
# the commit that introduced this list. Re-run that ranking periodically to
# refresh the universe as liquidity shifts.
ASSETS: dict[str, str] = {
    "BTC": "XXBTZUSD",
    "XRP": "XXRPZUSD",
    "SOL": "SOLUSD",
    "ETH": "XETHZUSD",
    "NEAR": "NEARUSD",
    "LTC": "XLTCZUSD",
    "LINK": "LINKUSD",
    "DOGE": "XDGUSD",
    "UNI": "UNIUSD",
    "ARB": "ARBUSD",
}

# Jev trades a tighter, explicitly-named universe per the spec (BTC, ETH,
# SOL, DOGE, XRP), a subset of ASSETS above.
JEV_ASSETS: list[str] = ["BTC", "ETH", "SOL", "DOGE", "XRP"]

# All actions any strategy's decision layer may output.
ACTIONS: list[str] = [f"{asset}_{side}" for asset in ASSETS for side in ("LONG", "SHORT")] + ["HOLD"]
JEV_ACTIONS: list[str] = [f"{asset}_{side}" for asset in JEV_ASSETS for side in ("LONG", "SHORT")] + ["HOLD"]

# --- Feature engine (shared) ---
SHORT_WINDOW = 5
LONG_WINDOW = 20
RSI_WINDOW = 14

# Short-horizon return windows (seconds) computed from the live tick buffer,
# not OHLC candles - these need sub-candle resolution.
RETURN_WINDOWS_SECONDS: dict[str, int] = {
    "return_1s": 1,
    "return_5s": 5,
    "return_15s": 15,
    "return_30s": 30,
    "return_1m": 60,
    "return_5m": 300,
}
TICK_BUFFER_MAX_AGE_SECONDS = 600  # keep 10 minutes of ticks per asset

# --- Legacy hard risk engine (trend / mean_reversion / breakout bots) ---
MIN_CONFIDENCE_PCT = 72.0
MAX_TRADE_USD = 50.0
MAX_DRAWDOWN_PCT = 5.0
ALLOW_LEVERAGE = False
COOLDOWN_SECONDS = 5 * 60
MAX_FEE_SPREAD_FRACTION = 0.35  # reject if est. round-trip cost eats >35% of edge
TAKER_FEE_FRACTION = 0.0026  # Kraken default taker fee (~0.26%)
MAKER_FEE_FRACTION = 0.0016  # Kraken default maker fee (~0.16%)
STARTING_CASH_USD = 1000.0

# --- Orchestration ---
POLL_INTERVAL_SECONDS = 20  # DECISION_INTERVAL: how often each bot re-decides
DECISION_INTERVAL = POLL_INTERVAL_SECONDS
REGIME_INTERVAL = 5 * 60  # regime/reasoning layer refresh cadence (5-10 min per spec)
OHLC_INTERVAL_MINUTES = 1
OHLC_LOOKBACK_CANDLES = 60

# --- Live price feed (Kraken WS v2) ---
LIVE_PRICE_MAX_AGE_SECONDS = 30
DATA_STALE_THRESHOLD_SECONDS = 15  # per-asset ticker age beyond which Jev must not trade that asset
MIN_DASHBOARD_WRITE_INTERVAL_SECONDS = 0.1
DASHBOARD_HEARTBEAT_SECONDS = 5

# --- Dashboard / per-bot file paths ---
DASHBOARD_HTML_PATH_TEMPLATE = "dashboard_{bot}.html"
STATE_JSON_PATH_TEMPLATE = "state_{bot}.json"
PORTFOLIO_STATE_PATH_TEMPLATE = "portfolio_state_{bot}.json"
OVERVIEW_HTML_PATH = "dashboard.html"
LEADERBOARD_HTML_PATH = "leaderboard.html"
MAX_EQUITY_HISTORY_POINTS = 500
DECISION_STREAM_MAX_ROWS = 200

# --- Database (trade journal, decisions, sessions) ---
DATABASE_PATH = "trading_hub.db"

# ============================================================================
# Jev AI Decision Bot - deterministic risk governor parameters.
# These are read-only to the decision layer; only risk_governor.py enforces
# them, and nothing in jev_decision.py can alter them at runtime.
# ============================================================================

# --- Capital & position limits ---
JEV_STARTING_BALANCE = 200.0
STARTING_BALANCE = JEV_STARTING_BALANCE
DEFAULT_POSITION_NOTIONAL = 30.0
MAX_POSITION_PERCENT = 0.15  # of current equity
MAX_TOTAL_EXPOSURE_PERCENT = 0.30  # sum of open notional / equity
MAX_SIMULTANEOUS_POSITIONS = 2
LEVERAGE = 1  # hard cap, no leverage beyond 1x, enforced in risk_governor.py

# --- Jev decision gates ---
MIN_JEV_CONFIDENCE = 0.72
MIN_PROBABILITY_GAP = 0.15  # winner_probability - second_highest_probability
EDGE_COST_MULTIPLIER = 1.5  # expected_gross_edge must be >= this x round-trip cost
JEV_DECISION_TIMEOUT_SECONDS = 10.0  # Jev call taking longer than this -> HOLD

# --- Position sizing & stops ---
MAX_RISK_PER_TRADE_PCT = 0.005  # 0.5% of current equity, max planned loss per trade
ATR_STOP_MULTIPLIER = 1.2  # stop_distance = ATR_STOP_MULTIPLIER * short_term_ATR
MIN_STOP_PCT = 0.0040  # 0.40%
MAX_STOP_PCT = 0.0150  # 1.50%
TAKE_PROFIT_R_MULTIPLE = 1.75  # take profit at ~1.5-2.0R; midpoint default
TIME_STOP_SECONDS = 10 * 60  # 10 minutes
TRAILING_STOP_ACTIVATE_R = 1.0  # trailing stop arms after +1R
TRAILING_STOP_DISTANCE_R = 0.5  # trails this many R behind the peak once armed

# --- Circuit breakers ---
LOSS_STREAK_LIMIT = 3
LOSS_COOLDOWN_SECONDS = 20 * 60
DAILY_SOFT_DRAWDOWN_PCT = 3.0  # -> halve new position sizes
DAILY_SOFT_DRAWDOWN_SIZE_MULTIPLIER = 0.5
DAILY_HARD_DRAWDOWN_PCT = 5.0  # -> halt trading until manual reset / new session
EXTREME_SPREAD_BPS = 25.0  # spread wider than this (bps of mid) -> no trade
ABNORMAL_VOLATILITY_ZSCORE = 3.0  # realized vol this many std devs above its own recent mean -> reduce/HOLD

# --- Benchmark (display only - never influences sizing or risk) ---
TARGET_PROFIT_PER_MINUTE = 0.75

# --- Execution simulation ---
ORDER_TIMEOUT_SECONDS = 3.0  # simulated maker order left unfilled this long -> convert to taker
MAKER_FILL_PROBABILITY = 0.65  # simulated probability a passive maker order fills before timeout
SLIPPAGE_BPS_PER_TAKER_ORDER = 2.0  # additional adverse slippage modeled on top of spread for taker fills

# --- Backtesting ---
BACKTEST_TRAIN_WINDOW_CANDLES = 2880  # ~2 days of 1-min candles for calibration
BACKTEST_TEST_WINDOW_CANDLES = 1440  # ~1 day out-of-sample
