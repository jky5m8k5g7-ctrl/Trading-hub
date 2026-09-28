from trading_hub.kraken_client import Candle


def make_candles(prices: list[float], volumes: list[float] | None = None, start_ts: int = 1_700_000_000) -> list[Candle]:
    volumes = volumes or [100.0] * len(prices)
    candles = []
    for i, (price, vol) in enumerate(zip(prices, volumes)):
        candles.append(
            Candle(
                timestamp=start_ts + i * 60,
                open=price,
                high=price * 1.001,
                low=price * 0.999,
                close=price,
                volume=vol,
            )
        )
    return candles


def uptrend_prices(n: int = 40, start: float = 100.0, step_pct: float = 0.5) -> list[float]:
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + step_pct / 100))
    return prices


def flat_prices(n: int = 40, price: float = 100.0) -> list[float]:
    return [price] * n
