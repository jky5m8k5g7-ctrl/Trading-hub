"""Central configuration for the trading hub pipeline.

Values here implement the risk envelope from the architecture spec:
confidence > 72%, max trade $50, max drawdown 5%, no leverage, cooldown,
fee/spread checks.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

# Kraken pair codes keyed by our internal asset symbol.
ASSETS: dict[str, str] = {
    "BTC": "XXBTZUSD",
    "ETH": "XETHZUSD",
    "SOL": "SOLUSD",
    "DOGE": "XDGUSD",
    "XRP": "XXRPZUSD",
}

# All actions the JEV decision layer may output.
ACTIONS: list[str] = [
    "BTC_LONG", "BTC_SHORT",
    "ETH_LONG", "ETH_SHORT",
    "SOL_LONG", "SOL_SHORT",
    "DOGE_LONG", "DOGE_SHORT",
    "XRP_LONG", "XRP_SHORT",
    "HOLD",
]

# --- Feature engine ---
SHORT_WINDOW = 5
LONG_WINDOW = 20
RSI_WINDOW = 14

# --- Hard risk engine ---
MIN_CONFIDENCE_PCT = 72.0
MAX_TRADE_USD = 50.0
MAX_DRAWDOWN_PCT = 5.0
ALLOW_LEVERAGE = False
COOLDOWN_SECONDS = 5 * 60
MAX_FEE_SPREAD_FRACTION = 0.35  # reject if est. round-trip cost eats >35% of edge
TAKER_FEE_FRACTION = 0.0026  # Kraken default taker fee (~0.26%)

# --- Paper execution ---
STARTING_CASH_USD = 1000.0

# --- Orchestration ---
POLL_INTERVAL_SECONDS = 20
OHLC_INTERVAL_MINUTES = 1
OHLC_LOOKBACK_CANDLES = 60

# --- Live price feed (Kraken WS v2) ---
# The decision loop still reasons over OHLC candles, but execution/marking
# prefers a live tick over the last candle close when one has arrived
# recently, so trades aren't priced off data up to a full poll interval old.
LIVE_PRICE_MAX_AGE_SECONDS = 30
DASHBOARD_REFRESH_SECONDS = 2

# --- Dashboard ---
DASHBOARD_HTML_PATH = "dashboard.html"
STATE_JSON_PATH = "state.json"
MAX_EQUITY_HISTORY_POINTS = 500

# Persists the paper portfolio (cash, positions, trade log) across restarts.
PORTFOLIO_STATE_PATH = "portfolio_state.json"
