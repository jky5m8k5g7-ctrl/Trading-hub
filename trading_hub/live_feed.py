"""Real-time price feed over Kraken's public WebSocket API (v2).

The decision loop (main.py) still reasons over 1-minute OHLC candles -
momentum/RSI/SMA are meaningless at tick resolution. But *pricing* a trade
or marking an open position's PnL off the last completed candle can be
tens of seconds stale by the time the decision fires. This feed keeps a
live snapshot (bid/ask/sizes/vwap/volume/last) per asset, updated on every
tick, plus a short tick-history buffer for sub-minute return features
(1s/5s/15s/30s/1m/5m) that OHLC candles can't provide.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import websocket

from trading_hub import config

log = logging.getLogger(__name__)

KRAKEN_WS_URL = "wss://ws.kraken.com/v2"

# Kraken's WS v2 API uses "BASE/QUOTE" symbols, unlike the REST pair codes
# in config.ASSETS - and not always the same base ticker (XBT vs BTC, XDG
# vs DOGE), so this is hand-verified against the live WS v2 API rather than
# derived from REST's "wsname" field (which uses the legacy X-prefixed
# tickers the WS v2 API actually rejects).
WS_SYMBOLS = {
    "BTC": "BTC/USD",
    "XRP": "XRP/USD",
    "SOL": "SOL/USD",
    "ETH": "ETH/USD",
    "NEAR": "NEAR/USD",
    "LTC": "LTC/USD",
    "LINK": "LINK/USD",
    "DOGE": "DOGE/USD",
    "UNI": "UNI/USD",
    "ARB": "ARB/USD",
}
_SYMBOL_TO_ASSET = {v: k for k, v in WS_SYMBOLS.items()}


@dataclass
class MarketSnapshot:
    asset: str
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    last: float
    vwap: float
    volume: float
    timestamp: float  # time.time() this snapshot was received

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def microprice(self) -> float:
        """Size-weighted mid: leans toward the side with less size resting
        (the side more likely to move first)."""
        total = self.bid_qty + self.ask_qty
        if total <= 0:
            return self.mid
        return (self.bid * self.ask_qty + self.ask * self.bid_qty) / total

    @property
    def spread_bps(self) -> float:
        if self.mid <= 0:
            return 0.0
        return (self.ask - self.bid) / self.mid * 10_000

    @property
    def imbalance(self) -> float:
        """(bid_qty - ask_qty) / (bid_qty + ask_qty), in [-1, 1]. Positive
        means more resting size on the bid (buy pressure). This is a
        top-of-book proxy, not full order-book depth."""
        total = self.bid_qty + self.ask_qty
        return 0.0 if total <= 0 else (self.bid_qty - self.ask_qty) / total

    def age_seconds(self, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        return now - self.timestamp


class LiveFeed:
    """Runs a Kraken WS v2 ticker subscription in a background thread and
    exposes the latest snapshot and short tick history per asset."""

    def __init__(self, assets: dict[str, str] | None = None):
        self._symbols = list((assets or WS_SYMBOLS).values())
        self._lock = threading.Lock()
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._tick_history: dict[str, deque[tuple[float, float]]] = {}
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._connected = False
        self._on_tick: Callable[[], None] | None = None

    def on_tick(self, callback: Callable[[], None]) -> None:
        """Register a callback fired synchronously on every tick received -
        for pushing an update immediately instead of polling on a timer."""
        self._on_tick = callback

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def get_prices(self) -> dict[str, float]:
        with self._lock:
            return {asset: s.last for asset, s in self._snapshots.items()}

    def get_snapshot(self, asset: str) -> MarketSnapshot | None:
        with self._lock:
            return self._snapshots.get(asset)

    def get_snapshots(self) -> dict[str, MarketSnapshot]:
        with self._lock:
            return dict(self._snapshots)

    def age_seconds(self, asset: str) -> float:
        with self._lock:
            snapshot = self._snapshots.get(asset)
        return float("inf") if snapshot is None else snapshot.age_seconds()

    def get_returns(self, asset: str, now: float | None = None) -> dict[str, float]:
        """Percent return over each window in config.RETURN_WINDOWS_SECONDS,
        computed from the tick history buffer (sub-candle resolution).
        Missing an entry means there isn't enough history for that window yet."""
        now = now if now is not None else time.time()
        with self._lock:
            history = list(self._tick_history.get(asset, ()))
        if not history:
            return {}

        latest_price = history[-1][1]
        returns: dict[str, float] = {}
        for name, window_seconds in config.RETURN_WINDOWS_SECONDS.items():
            target_ts = now - window_seconds
            past_price = None
            for ts, price in history:
                if ts <= target_ts:
                    past_price = price
                else:
                    break
            if past_price is not None and past_price != 0:
                returns[name] = (latest_price - past_price) / past_price * 100
        return returns

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        with self._lock:
            self._connected = True
        ws.send(json.dumps({"method": "subscribe", "params": {"channel": "ticker", "symbol": self._symbols}}))
        log.info("live feed subscribed to %s", self._symbols)

    def _on_close(self, ws: websocket.WebSocketApp, *_args) -> None:
        with self._lock:
            self._connected = False

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
        except ValueError:
            return

        if payload.get("channel") != "ticker":
            return

        now = time.time()
        got_tick = False
        for row in payload.get("data", []):
            symbol = row.get("symbol")
            asset = _SYMBOL_TO_ASSET.get(symbol)
            last = row.get("last")
            bid = row.get("bid")
            ask = row.get("ask")
            if asset is None or last is None or bid is None or ask is None:
                continue

            snapshot = MarketSnapshot(
                asset=asset,
                bid=float(bid),
                ask=float(ask),
                bid_qty=float(row.get("bid_qty") or 0.0),
                ask_qty=float(row.get("ask_qty") or 0.0),
                last=float(last),
                vwap=float(row.get("vwap") or last),
                volume=float(row.get("volume") or 0.0),
                timestamp=now,
            )
            with self._lock:
                self._snapshots[asset] = snapshot
                history = self._tick_history.setdefault(asset, deque())
                history.append((now, snapshot.last))
                cutoff = now - config.TICK_BUFFER_MAX_AGE_SECONDS
                while history and history[0][0] < cutoff:
                    history.popleft()
            got_tick = True

        if got_tick and self._on_tick is not None:
            try:
                self._on_tick()
            except Exception:
                log.exception("on_tick callback failed")

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        log.warning("live feed error: %s", error)

    def _run_forever_with_reconnect(self) -> None:
        backoff = 1
        while not self._stopping:
            self._ws = websocket.WebSocketApp(
                KRAKEN_WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws.run_forever(ping_interval=20, ping_timeout=10)
            with self._lock:
                self._connected = False
            if self._stopping:
                return
            log.warning("live feed disconnected, reconnecting in %ss", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._run_forever_with_reconnect, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        if self._ws is not None:
            self._ws.close()
