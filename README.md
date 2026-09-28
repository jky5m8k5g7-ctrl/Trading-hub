# Trading Hub

Paper trading pipeline that turns live Kraken market data into gated,
risk-checked simulated trades across five crypto pairs.

```
REASONING LAYER -> KRAKEN LIVE DATA -> FEATURE ENGINE -> JEV DECISION -> HARD RISK ENGINE -> PAPER EXECUTION -> LIVE DASHBOARD
```

## Pipeline

- **`reasoning_layer.py`** - classifies market regime / strategy context (`trending_up`, `trending_down`, `ranging`, `volatile`) from aggregated features. Uses the TypeSafe AI SDK when `TYPESAFE_API_KEY` is configured, otherwise falls back to a local momentum/volatility heuristic. Dampens JEV confidence when a call fights the assessed regime (e.g. going short in a strong uptrend).
- **`kraken_client.py`** - fetches OHLC candles and ticker/spread data from Kraken's public REST API.
- **`feature_engine.py`** - computes SMA short/long, momentum, RSI, volatility, and volume change per asset.
- **`jev_engine.py`** - Joint Ensemble Vote decision layer. Three independent voters (trend, momentum, RSI mean-reversion) vote per asset; agreement drives confidence, volume confirmation adds a bonus. Outputs one of `BTC_LONG`, `BTC_SHORT`, `ETH_LONG`, `ETH_SHORT`, `SOL_LONG`, `SOL_SHORT`, `DOGE_LONG`, `DOGE_SHORT`, `XRP_LONG`, `XRP_SHORT`, or `HOLD`.
- **`risk_engine.py`** - hard, non-negotiable gate: confidence must exceed 72%, trade size capped at $50, trading halts at 5% drawdown from peak equity, no leverage, per-asset cooldown after each trade, and a fee/spread-vs-edge check.
- **`portfolio.py`** / **`executor.py`** - simulated (paper) positions, cash, and trade log; no real orders are ever sent.
- **`dashboard.py`** - renders `dashboard.html` (auto-refreshing) and `state.json` after every cycle, including the current regime assessment.
- **`main.py`** - runs the cycle above on a configurable poll interval.

## Running

```bash
pip install -r requirements.txt
cp .env.example .env  # optionally set TYPESAFE_API_KEY
python -m trading_hub.main
```

Then open `dashboard.html` in a browser; it refreshes itself every poll cycle.

## Configuration

All thresholds (confidence floor, max trade size, max drawdown, cooldown,
poll interval, etc.) live in `trading_hub/config.py`. Environment variables
(currently just `TYPESAFE_API_KEY`) are loaded from `.env` via `python-dotenv`.

## Tests

```bash
pytest
```
