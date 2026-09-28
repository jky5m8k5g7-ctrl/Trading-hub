# Trading Hub

Paper trading pipeline that turns live Kraken market data into gated,
risk-checked simulated trades. Four bots run side by side over the same
live market data: three simple rule-based strategies, and **Jev**, an
AI decision bot whose calls are subject to a fully deterministic risk
governor - Jev picks a potential trade, ordinary Python code decides
whether it's allowed to happen.

**Paper trading only.** No real exchange orders, no withdrawal permissions,
no real funds connected, anywhere in this codebase.

```
REASONING / REGIME LAYER
        |
KRAKEN LIVE DATA (REST + WebSocket) -> FEATURE ENGINE
        |
   JEV DECISION ENGINE (TypeSafe AI)
        |
DETERMINISTIC RISK GOVERNOR  <- Jev can never touch this
        |
  PAPER EXECUTION ENGINE (maker/taker fill simulation)
        |
  DATABASE / TRADE JOURNAL (SQLite)
        |
  LIVE DASHBOARDS
```

The three legacy bots (`trend`, `mean_reversion`, `breakout`) run the same
shape of pipeline with simpler, deterministic decision functions instead of
an AI call - see [Strategies](#strategies-4-independent-paper-bots) below.

## Architecture overview

This is a single Python process (`trading_hub/`), not a separate
frontend/backend/database split - that's what the codebase already was
before this change, and the brief was to extend it rather than bolt on an
unrelated stack. "Frontend" here is server-rendered static HTML files
(`dashboard.html`, `dashboard_<bot>.html`, `leaderboard.html`), regenerated
by the backend process and viewed by opening them in a browser; there is no
separate API server or JS framework. "Database" is SQLite
(`trading_hub.db`), written directly by the same process. See
[Limitations](#limitations) for what a real Netlify/Railway split would
require.

Two loops run concurrently per bot cycle:

- **The decision loop** (every `POLL_INTERVAL_SECONDS`, default 20s):
  fetch Kraken OHLC candles -> compute features -> assess regime -> ask
  Jev (or run a strategy's deterministic decision function) -> risk-gate
  -> execute -> journal to the database -> persist portfolio state.
- **The live tick loop**: a background thread holds a Kraken WebSocket
  (v2) subscription; every tick immediately redraws every bot's dashboard
  (event-driven, debounced to `MIN_DASHBOARD_WRITE_INTERVAL_SECONDS`) and
  is preferred over the last candle close when pricing a fill, so trades
  aren't priced off data up to a full poll interval old.

## Strategies (4 independent paper bots)

| Bot | Starting capital | Decision source | Position sizing | Stops |
|---|---|---|---|---|
| `trend` | $1,000 | `jev_engine.py` - trend+momentum+RSI ensemble | flat $50 | none (closes on signal reversal) |
| `mean_reversion` | $1,000 | `strategies.py` - fades SMA deviation | flat $50 | none (closes on signal reversal) |
| `breakout` | $1,000 | `strategies.py` - 20-candle channel break | flat $50 | none (closes on signal reversal) |
| `jev` | $200 | `jev_decision.py` - TypeSafe AI (System One) | volatility-adjusted, risk-budgeted | stop-loss, take-profit, time-stop, trailing |

**Capital is never pooled.** Each bot has its own `Portfolio`, its own
persisted state file, and its own row set in the database - a leaderboard
(`leaderboard.html`) compares them side by side without merging accounts.

Every bot's data lives in its own files, `{bot}` filled with its name:
`dashboard_{bot}.html`, `state_{bot}.json`, `portfolio_state_{bot}.json`.
`dashboard.html` is a combined overview; `leaderboard.html` is the
cross-bot performance comparison.

## How Jev's decisions work

1. **Feature engine** (`feature_engine.py`) computes SMA short/long,
   momentum, RSI, realized volatility, volume change, and a rolling
   20-candle high/low channel per asset from OHLC candles. `jev_state.py`
   adds live order-book context (bid/ask, microprice, spread, top-of-book
   imbalance) from the WebSocket feed, plus short-horizon returns
   (1s/5s/15s/30s/1m/5m) from a rolling tick buffer (`live_feed.py`).
2. **Regime engine** (`regime_engine.py`) classifies the market into one
   of `TREND_UP`, `TREND_DOWN`, `MEAN_REVERTING`, `RANGE_BOUND`,
   `HIGH_VOLATILITY`, `LOW_VOLATILITY`, `BREAKOUT`, `LIQUIDATION_EVENT`,
   `ABNORMAL_SPREAD`, `RISK_OFF`, or `UNCERTAIN`, on its own slow cadence
   (`REGIME_INTERVAL`, default 5 min, cached between refreshes) -
   deterministic statistics today, but the interface
   (`assess() -> JevRegime`) is stable so an LLM can replace the
   classification body later without touching any caller. Its output
   includes `allowed_assets`/`disabled_assets` and a `risk_multiplier`
   that the risk governor actually enforces.
3. **Jev decision** (`jev_decision.py`) sends a compact structured state
   (market features + regime + account/position/performance context - see
   `jev_state.py`) to TypeSafe AI's System One classifier, asking it to
   choose one of `BTC_LONG`, `BTC_SHORT`, `ETH_LONG`, `ETH_SHORT`,
   `SOL_LONG`, `SOL_SHORT`, `DOGE_LONG`, `DOGE_SHORT`, `XRP_LONG`,
   `XRP_SHORT`, or `HOLD`, and gets back a full probability distribution
   over all of them. **Any failure here - no API key, a timeout, a
   malformed response - is an explicit, logged HOLD, never a fabricated
   decision.**
4. **Risk governor** (`risk_governor.py`) is the only thing that can turn
   a Jev decision into a trade - see [Risk parameters](#exact-risk-parameters)
   below.
5. **Execution** (`jev_execution.py`) simulates a realistic paper fill
   (maker/taker, spread-crossing, slippage - never assumes a midpoint
   fill).
6. **Exit rules** (`exit_rules.py`) check stop-loss/take-profit/time-stop/
   trailing-stop every cycle, independent of Jev - Jev may request an
   early exit (an opposing-direction call that itself clears the
   confidence/probability-gap gates), but it can never loosen, widen, or
   disable a hard stop.
7. Every decision (including every `HOLD` and every rejection, with its
   reason) and every closed trade is written to `trading_hub.db`
   (`database.py`) - this is what the dashboard's live decision stream and
   the leaderboard's analytics (`analytics.py`) read.

## Exact risk parameters

All in `trading_hub/config.py`, nowhere else:

| Parameter | Value | Enforced by |
|---|---|---|
| `MIN_JEV_CONFIDENCE` | 0.72 | `risk_governor.py` |
| `MIN_PROBABILITY_GAP` | 0.15 | `risk_governor.py` |
| `EDGE_COST_MULTIPLIER` | 1.5x round-trip cost | `risk_governor.py` |
| `JEV_STARTING_BALANCE` | $200 | - |
| `DEFAULT_POSITION_NOTIONAL` | $30 | `risk_governor.py` (hard cap) |
| `MAX_POSITION_PERCENT` | 15% of equity | `risk_governor.py` (hard cap) |
| `MAX_TOTAL_EXPOSURE_PERCENT` | 30% of equity | `risk_governor.py` |
| `MAX_SIMULTANEOUS_POSITIONS` | 2 | `risk_governor.py` |
| `LEVERAGE` | 1 (none) | `portfolio.py` never borrows |
| `MAX_RISK_PER_TRADE_PCT` | 0.5% of equity | `risk_governor.compute_position_size` |
| `ATR_STOP_MULTIPLIER` / `MIN_STOP_PCT` / `MAX_STOP_PCT` | 1.2x, 0.40%-1.50% | `risk_governor.compute_stop_distance_pct` |
| `TAKE_PROFIT_R_MULTIPLE` | 1.75R | `risk_governor.py` |
| `TIME_STOP_SECONDS` | 600 (10 min) | `exit_rules.py` |
| `TRAILING_STOP_ACTIVATE_R` / `TRAILING_STOP_DISTANCE_R` | +1R, 0.5R trail | `exit_rules.py` |
| `LOSS_STREAK_LIMIT` / `LOSS_COOLDOWN_SECONDS` | 3 losses -> 20 min pause | `portfolio.register_trade_result` |
| `DAILY_SOFT_DRAWDOWN_PCT` | 3% -> 0.5x new position size | `risk_governor.py` |
| `DAILY_HARD_DRAWDOWN_PCT` | 5% -> halt until manual reset | `portfolio.halt` |
| `EXTREME_SPREAD_BPS` | 25bps -> reject | `risk_governor.py` |
| `DATA_STALE_THRESHOLD_SECONDS` | 15s -> HOLD | `risk_governor.py` |
| `TARGET_PROFIT_PER_MINUTE` | $0.75 (display-only benchmark) | never influences sizing |

Position size is **the smaller of** the risk-budget-implied size
(`risk_budget / stop_distance`) and the hard $30/15%-of-equity cap -
never only one fixed rule. Position sizing is applied on top of a
volatility-adjusted stop distance (`ATR_STOP_MULTIPLIER * realized
volatility`, bounded to 0.40%-1.50%), never a single fixed percentage.

## Fail-safe behavior

| Condition | Result |
|---|---|
| No `TYPESAFE_API_KEY`, timeout, or malformed Jev response | HOLD (logged, journaled) |
| Stale data (`DATA_STALE_THRESHOLD_SECONDS`) | HOLD |
| WebSocket disconnected | HOLD (no new trades) |
| Extreme spread | REJECTED |
| Regime disables the asset | REJECTED |
| Confidence or probability-gap gate fails | REJECTED |
| Max simultaneous positions / exposure reached | REJECTED |
| 3% daily drawdown | New position sizes halved |
| 5% daily drawdown | Bot HALTED until manual `reset_halt()` / new session |
| 3 consecutive losses | 20-minute trading pause |
| Database write failure | Treated as HALT upstream, never silently ignored |

## How paper execution works

`jev_execution.py` never assumes a midpoint fill. An entry first tries a
passive maker order at the near-touch price (`MAKER_FILL_PROBABILITY`
chance of filling before `ORDER_TIMEOUT_SECONDS`), falling back to a taker
order that crosses the spread with `SLIPPAGE_BPS_PER_TAKER_ORDER` of extra
adverse slippage. Exits always cross the spread (taker), modeling a
conservative worst-case exit rather than a favorable passive fill on the
way out. Every fill records expected price, submitted price, fill price,
slippage, fee, maker/taker classification, and latency.

## Backtesting

`backtest.py` replays historical 1-minute OHLC candles bar-by-bar through
the real feature engine and risk/execution machinery, with an explicit
no-lookahead guarantee (bar `i` only ever sees `candles[:i+1]`) verified by
`tests/test_backtest.py::test_no_lookahead_same_prefix_gives_identical_trades_up_to_truncation`.
Stops/targets are checked against each bar's high/low range, not just its
close, so an intrabar wick is caught the way it would be live. It does
**not** call the live TypeSafe API per historical bar (slow, costly, and
not how you'd want to validate an AI signal anyway); it takes a pluggable
deterministic `decide_fn` and defaults to the `trend` ensemble. See
[Limitations](#limitations) for what full walk-forward optimization would add.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env  # optionally set TYPESAFE_API_KEY - without it, Jev always HOLDs
python -m trading_hub.main
```

Then open, in a browser:
- `dashboard.html` - overview comparing all 4 bots
- `dashboard_jev.html` - Jev's dedicated dashboard: status, equity/P&L
  breakdown, position detail with stop/target/time-remaining, the decision
  panel (confidence, full probability distribution, latency), the regime
  panel, the risk panel, and a live scrolling decision stream
- `dashboard_trend.html` / `dashboard_mean_reversion.html` /
  `dashboard_breakout.html` - the legacy bots' dashboards
- `leaderboard.html` - cross-bot performance comparison (Sharpe, Sortino,
  profit factor, expectancy, win rate, profit/minute, etc.)

All auto-refresh every 2 seconds in the browser; the underlying files are
rewritten immediately on every live tick, independent of that refresh
interval.

## Required environment variables

| Variable | Required? | Used by |
|---|---|---|
| `TYPESAFE_API_KEY` | No (Jev HOLDs without it) | `jev_decision.py`, `reasoning_layer.py` |

Loaded from `.env` via `python-dotenv`, read server-side only
(`config.py`). Never sent to the dashboard HTML, never logged. See
`.env.example`.

## Starting a new session / resetting

Portfolio balances persist across restarts and are **never** reset
automatically. To start a fresh named session for a bot (e.g. a clean
24-hour run):

```python
from trading_hub import config, database
from trading_hub.portfolio import Portfolio

sid = database.start_new_session("jev", config.JEV_STARTING_BALANCE, session_name="24h-run-1", duration_hours=24)
Portfolio(cash=config.JEV_STARTING_BALANCE, starting_equity=config.JEV_STARTING_BALANCE, peak_equity=config.JEV_STARTING_BALANCE) \
    .save(config.PORTFOLIO_STATE_PATH_TEMPLATE.format(bot="jev"))
```

Do this for each bot you want reset, then (re)start `python -m
trading_hub.main`. Old sessions' decisions/trades stay in the database
under their own `session_id`, so historical runs remain comparable -
nothing is deleted.

## Pausing / halting

- **Loss-streak pause** (3 consecutive losses) clears itself automatically
  after `LOSS_COOLDOWN_SECONDS`.
- **Hard drawdown halt** (5%) does not self-clear - call
  `portfolio.reset_halt()` (load the bot's `Portfolio`, call the method,
  save it back) or start a new session as above.
- To stop a bot's process entirely, stop `python -m trading_hub.main`
  (`pkill -f trading_hub.main` or your process manager's equivalent) - all
  state is already persisted, so restarting resumes exactly where it left
  off (open positions, stops, targets, circuit-breaker state included).

## Configuration

Every tunable value - confidence/probability-gap/edge thresholds, position
sizing, stop bounds, take-profit R-multiple, time-stop, trailing-stop,
circuit breakers, poll/regime intervals, stale-data threshold, asset
universe - lives in `trading_hub/config.py`. Nothing is scattered as a
magic number elsewhere in the codebase.

The 10-asset universe (`config.ASSETS`) was chosen by ranking Kraken's
~670 USD-quoted markets by 24h quote volume (volume * vwap) via the public
Ticker endpoint and keeping the top 10: BTC, XRP, SOL, ETH, NEAR, LTC,
LINK, DOGE, UNI, ARB. Jev trades a fixed subset of that (`config.JEV_ASSETS`
- BTC, ETH, SOL, DOGE, XRP) per the spec.

## Tests

```bash
pytest
```

125 tests, covering (among others): the Jev response parser and its
fail-safe paths, confidence/probability-gap/edge-vs-cost gates, position
sizing (both the risk-budget and hard-cap branches), volatility-adjusted
stop bounds, max exposure/simultaneous positions, daily soft/hard
drawdown, loss-streak cooldown, stale-data rejection, the maker/taker fill
simulator, fee/slippage/P&L calculation for both long and short, take-
profit/stop-loss/time-stop/trailing-stop exit rules (including that a
trailing stop never loosens and a hard stop still fires after it arms),
session persistence and restart recovery, and the backtester's
no-lookahead guarantee.

## Limitations

Documented honestly rather than glossed over:

- **No separate frontend/backend/DB deployment split.** This is one
  Python process; "frontend" is server-rendered HTML files opened
  directly, not a hosted SPA. Deploying as Netlify (frontend) + Railway/
  Render (backend) + a managed Postgres would need: (a) a small HTTP/SSE
  or WebSocket API server wrapping the dashboard data (the `render_*`
  functions in `dashboard.py`/`jev_dashboard.py`/`leaderboard.py` already
  separate data-shaping from HTML string formatting, so this is a real but
  bounded lift, not a rewrite); (b) swapping SQLite for a networked
  database in `database.py` (the schema and query functions would carry
  over almost unchanged - only `_connect()` changes); (c) the persistent
  trading loop (`main.py`) staying on a long-running host (Railway/Render/
  a VM), never inside a Netlify serverless function, per the brief.
- **No full L2 order-book depth.** `imbalance`/`microprice` use Kraken's
  top-of-book bid/ask size from the WS v2 ticker channel, not full depth -
  a real but shallow proxy, documented as such in `live_feed.py`.
- **No funding rate / perp basis.** Kraken spot pairs only; those fields
  don't apply and are omitted rather than faked.
- **Single exchange (Kraken).** Coinbase/Hyperliquid were in scope per the
  spec as options; Kraken was kept since it was already integrated,
  tested, and confirmed reachable from this environment.
- **Backtester has no automated walk-forward loop.** It supports a clean
  train/test split with a verified no-lookahead guarantee, but repeatedly
  calibrating on a train window, testing out-of-sample, and rolling
  forward is left to the caller to drive - not automated end-to-end here.
- **Regime classification is deterministic statistics, not an LLM call.**
  Its interface is stable specifically so that can change later without
  touching any caller, but today it's rule-based, not reasoning-based.
- **Jev only trades when `TYPESAFE_API_KEY` is configured with a real,
  working key.** This session did not have one set, so Jev has been
  observed only in its (correct, intentional) fail-safe HOLD state, not
  placing a live trade - the deterministic risk governor around it is
  fully tested in isolation (`tests/test_risk_governor.py`,
  `tests/test_jev_decision.py`), but end-to-end "Jev proposes, risk
  governor approves, a real AI-driven paper trade executes" has not been
  observed live in this environment.
