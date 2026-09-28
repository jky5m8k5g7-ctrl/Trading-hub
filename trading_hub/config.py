"""Central configuration for the trading hub pipeline.

Values here implement the risk envelope from the architecture spec:
confidence > 72%, max trade $50, max drawdown 5%, no leverage, cooldown,
fee/spread checks.
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

# All actions any strategy's decision layer may output.
ACTIONS: list[str] = [f"{asset}_{side}" for asset in ASSETS for side in ("LONG", "SHORT")] + ["HOLD"]

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

# The dashboard is pushed on every tick (event-driven, not polled), debounced
# to this floor so a multi-asset tick burst doesn't turn into a disk-write
# storm. A slow heartbeat covers the rare case the feed goes quiet.
MIN_DASHBOARD_WRITE_INTERVAL_SECONDS = 0.1
DASHBOARD_HEARTBEAT_SECONDS = 5

# --- Dashboard ---
# Multiple bots (strategies) run side by side, each with its own dashboard,
# state snapshot, and persisted portfolio; {bot} is filled with the bot's
# name (see strategies.py). dashboard.html is a separate overview page
# comparing all bots at a glance.
DASHBOARD_HTML_PATH_TEMPLATE = "dashboard_{bot}.html"
STATE_JSON_PATH_TEMPLATE = "state_{bot}.json"
PORTFOLIO_STATE_PATH_TEMPLATE = "portfolio_state_{bot}.json"
OVERVIEW_HTML_PATH = "dashboard.html"
MAX_EQUITY_HISTORY_POINTS = 500
