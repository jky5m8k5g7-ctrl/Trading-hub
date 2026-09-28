"""Thin client for Kraken's public REST API (no auth needed for market data)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

KRAKEN_API_BASE = "https://api.kraken.com/0/public"


class KrakenClientError(RuntimeError):
    """Raised when Kraken returns an error payload or an unusable response."""


@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class KrakenClient:
    """Fetches live ticker and OHLC data from Kraken."""

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        resp = self._session.get(f"{KRAKEN_API_BASE}/{path}", params=params, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("error"):
            raise KrakenClientError(f"Kraken API error for {path}: {payload['error']}")
        return payload["result"]

    def get_ohlc(self, pair: str, interval_minutes: int = 1, since: int | None = None) -> list[Candle]:
        """Return recent OHLC candles for `pair`, oldest first."""
        params = {"pair": pair, "interval": interval_minutes}
        if since is not None:
            params["since"] = since
        result = self._get("OHLC", params)
        rows = next(v for k, v in result.items() if k != "last")
        return [
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[6]),
            )
            for row in rows
        ]

    def get_ticker(self, pair: str) -> dict:
        """Return the raw ticker payload for `pair`, including best bid/ask."""
        result = self._get("Ticker", {"pair": pair})
        return next(iter(result.values()))

    def get_spread_fraction(self, pair: str) -> float:
        """Return (ask - bid) / mid as a fraction, used for fee/spread risk checks."""
        ticker = self.get_ticker(pair)
        bid = float(ticker["b"][0])
        ask = float(ticker["a"][0])
        mid = (bid + ask) / 2
        if mid <= 0:
            return 0.0
        return (ask - bid) / mid

    @staticmethod
    def now() -> int:
        return int(time.time())
