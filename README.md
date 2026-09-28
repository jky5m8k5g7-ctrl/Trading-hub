# Trading Hub

Paper trading pipeline that turns live Kraken market data into gated,
risk-checked simulated trades. Three strategies run side by side as
independent paper bots over the same 10-asset universe, sharing one live
data feed, so you can compare how differently-reasoned bots perform against
the same market in real time.

```
REASONING LAYER -> KRAKEN LIVE DATA -> FEATURE ENGINE -> [3 STRATEGIES] -> HARD RISK ENGINE -> PAPER EXECUTION -> LIVE DASHBOARDS
```

## Strategies (bots)

- **`trend`** - the original Joint Ensemble Vote (JEV): three voters (trend, momentum, RSI mean-reversion) per asset; agreement drives confidence, volume confirmation adds a bonus.
- **`mean_reversion`** - fades a large deviation from the long SMA, confirmed by RSI overbought/oversold.
- **`breakout`** - trades a decisive break of the prior 20-candle high/low channel, with a volume-confirmation bonus.

Each bot gets its own portfolio, dashboard, and persisted state
(`dashboard_<bot>.html`, `state_<bot>.json`, `portfolio_state_<bot>.json`).
`dashboard.html` is a combined overview comparing all three, linking to each
bot's own page.

## Asset universe

10 pairs, chosen by ranking Kraken's ~670 USD-quoted markets by 24h quote
volume (volume * vwap) via the public Ticker endpoint and keeping the top
10: BTC, XRP, SOL, ETH, NEAR, LTC, LINK, DOGE, UNI, ARB. See `config.ASSETS`;
re-run that ranking periodically to refresh the universe as liquidity shifts.

## Pipeline

- **`reasoning_layer.py`** - classifies market regime / strategy context (`trending_up`, `trending_down`, `ranging`, `volatile`) from aggregated features. Uses the TypeSafe AI SDK when `TYPESAFE_API_KEY` is configured, otherwise falls back to a local momentum/volatility heuristic. Dampens a bot's confidence when its call fights the assessed regime (e.g. going short in a strong uptrend).
- **`kraken_client.py`** - fetches OHLC candles and ticker/spread data from Kraken's public REST API.
- **`live_feed.py`** - Kraken's public WebSocket (v2) ticker stream, kept in a background thread; the decision loop prefers a fresh live tick over the last candle close when pricing a trade, and the dashboard is pushed immediately on every tick (event-driven, not polled).
- **`feature_engine.py`** - computes SMA short/long, momentum, RSI, volatility, volume change, and a rolling 20-candle high/low channel per asset.
- **`jev_engine.py`** / **`strategies.py`** - the three decision functions above. All share `jev_engine.pick_best_action`, which picks the single highest-confidence non-neutral call across assets, or `HOLD`.
- **`risk_engine.py`** - hard, non-negotiable gate applied identically to every bot: confidence must exceed 72%, trade size capped at $50, trading halts at 5% drawdown from peak equity, no leverage, per-asset cooldown after each trade, and a fee/spread-vs-edge check.
- **`portfolio.py`** / **`executor.py`** - simulated (paper) positions, cash, and trade log; no real orders are ever sent. Positions auto-close when their asset's signal reverses or goes neutral.
- **`dashboard.py`** - renders each bot's dashboard/state plus the overview page.
- **`main.py`** - runs all three bots' decision cycle on a configurable poll interval, plus the live feed and dashboard-push threads.

## Running

```bash
pip install -r requirements.txt
cp .env.example .env  # optionally set TYPESAFE_API_KEY
python -m trading_hub.main
```

Then open `dashboard.html` for the bot comparison overview, or a bot's own
`dashboard_<name>.html` for its detail view; both auto-refresh in your
browser every 2 seconds. The underlying files are updated immediately on
every live tick, independent of that refresh interval.

## Configuration

All thresholds (confidence floor, max trade size, max drawdown, cooldown,
poll interval, asset universe, etc.) live in `trading_hub/config.py`.
Environment variables (currently just `TYPESAFE_API_KEY`) are loaded from
`.env` via `python-dotenv`.

## Tests

```bash
pytest
```
