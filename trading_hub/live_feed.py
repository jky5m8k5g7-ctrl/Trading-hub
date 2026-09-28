"""Real-time price feed over Kraken's public WebSocket API (v2).

The decision loop (main.py) still reasons over 1-minute OHLC candles -
momentum/RSI/SMA are meaningless at tick resolution. But *pricing* a trade
or marking an open position's PnL off the last completed candle can be
tens of seconds stale by the time the decision fires. This feed keeps a
live last-trade price per asset, updated on every tick, so execution and
equity/dashboard marks use the current market price instead of a stale
candle close.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import websocket

from trading_hub import config

log = logging.getLogger(__name__)

KRAKEN_WS_URL = "wss://ws.kraken.com/v2"

# Kraken's WS v2 API uses "BASE/QUOTE" symbols, unlike the REST pair codes
# in config.ASSETS.
WS_SYMBOLS = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "DOGE": "DOGE/USD",
    "XRP": "XRP/USD",
}
_SYMBOL_TO_ASSET = {v: k for k, v in WS_SYMBOLS.items()}


class LiveFeed:
    """Runs a Kraken WS v2 ticker subscription in a background thread and
    exposes the latest last-trade price per asset via get_prices()."""

    def __init__(self, assets: dict[str, str] | None = None):
        self._symbols = list((assets or WS_SYMBOLS).values())
        self._lock = threading.Lock()
        self._prices: dict[str, float] = {}
        self._updated_at: dict[str, float] = {}
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._on_tick: Callable[[], None] | None = None

    def on_tick(self, callback: Callable[[], None]) -> None:
        """Register a callback fired synchronously on every tick received -
        for pushing an update immediately instead of polling on a timer."""
        self._on_tick = callback

    def get_prices(self) -> dict[str, float]:
        with self._lock:
            return dict(self._prices)

    def age_seconds(self, asset: str) -> float:
        with self._lock:
            updated = self._updated_at.get(asset)
        return float("inf") if updated is None else time.time() - updated

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        ws.send(json.dumps({"method": "subscribe", "params": {"channel": "ticker", "symbol": self._symbols}}))
        log.info("live feed subscribed to %s", self._symbols)

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
        except ValueError:
            return

        if payload.get("channel") != "ticker":
            return

        got_tick = False
        for row in payload.get("data", []):
            symbol = row.get("symbol")
            last = row.get("last")
            asset = _SYMBOL_TO_ASSET.get(symbol)
            if asset is None or last is None:
                continue
            with self._lock:
                self._prices[asset] = float(last)
                self._updated_at[asset] = time.time()
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
            )
            self._ws.run_forever(ping_interval=20, ping_timeout=10)
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
