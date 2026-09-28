"""Defense-in-depth guarantee that this system can never place a real
order, independent of and in addition to the simple fact that no
order-placement code exists anywhere in the codebase.

Why this exists as its own module, not just a comment: "we never call a
real exchange trading endpoint" is true today by omission - nobody wrote
that code. That's fragile against a future edit. This module makes it
true by assertion instead: a set of runtime checks that refuse to start
(or immediately halt every bot) if anything looks like it could route to
real money - a real exchange API secret sitting in the environment, a
leverage setting above 1x, or a config value that's been tampered with
past its safe bound. Call check() once at startup (main.py does) and it
either passes silently or raises SafetyViolation, which the caller must
treat as fatal - never caught and ignored.
"""

from __future__ import annotations

import os

from trading_hub import config

# Env var name fragments that would indicate real exchange trading
# credentials are present. Matched case-insensitively as substrings, so
# this catches KRAKEN_API_SECRET, COINBASE_PRIVATE_KEY,
# HYPERLIQUID_WALLET_KEY, etc. without needing to enumerate every exchange's
# exact naming convention. TYPESAFE_API_KEY is explicitly exempted - it's
# an AI reasoning provider, not an exchange, and has no trading capability.
_FORBIDDEN_ENV_SUBSTRINGS = (
    "KRAKEN_API", "KRAKEN_SECRET", "KRAKEN_PRIVATE",
    "COINBASE_API", "COINBASE_SECRET", "COINBASE_PRIVATE",
    "HYPERLIQUID_API", "HYPERLIQUID_SECRET", "HYPERLIQUID_PRIVATE", "HYPERLIQUID_WALLET",
    "BINANCE_API", "BINANCE_SECRET",
    "EXCHANGE_API", "EXCHANGE_SECRET", "EXCHANGE_PRIVATE",
    "BROKER_API", "BROKER_SECRET",
    "WALLET_PRIVATE_KEY", "WALLET_SEED", "WALLET_MNEMONIC",
)

_EXEMPT_ENV_VARS = frozenset({"TYPESAFE_API_KEY"})


class SafetyViolation(RuntimeError):
    """Raised when a paper-only guarantee would be violated. Always fatal -
    never caught and suppressed anywhere in this codebase."""


def _scan_environment_for_exchange_credentials() -> list[str]:
    hits = []
    for env_name in os.environ:
        if env_name in _EXEMPT_ENV_VARS:
            continue
        upper = env_name.upper()
        if any(fragment in upper for fragment in _FORBIDDEN_ENV_SUBSTRINGS):
            hits.append(env_name)
    return hits


def check() -> None:
    """Raises SafetyViolation if this process is not safely paper-only.
    Call once at startup, before any bot is constructed. Never call this
    inside a try/except that swallows the result - a violation here means
    stop, not degrade."""
    credential_hits = _scan_environment_for_exchange_credentials()
    if credential_hits:
        raise SafetyViolation(
            "Refusing to start: environment variable(s) that look like real exchange "
            f"trading credentials are present: {', '.join(sorted(credential_hits))}. "
            "This system is paper-trading only and must never hold real exchange secrets."
        )

    if config.LEVERAGE != 1:
        raise SafetyViolation(f"Refusing to start: config.LEVERAGE={config.LEVERAGE!r}, must be exactly 1 (no leverage).")

    if config.ALLOW_LEVERAGE is not False:
        raise SafetyViolation(f"Refusing to start: config.ALLOW_LEVERAGE={config.ALLOW_LEVERAGE!r}, must be False.")

    if config.DAILY_HARD_DRAWDOWN_PCT <= 0 or config.DAILY_HARD_DRAWDOWN_PCT > 25:
        raise SafetyViolation(
            f"Refusing to start: config.DAILY_HARD_DRAWDOWN_PCT={config.DAILY_HARD_DRAWDOWN_PCT!r} "
            "is outside a sane bound (0, 25] - looks tampered with, not tuned."
        )

    if config.MAX_TOTAL_EXPOSURE_PERCENT <= 0 or config.MAX_TOTAL_EXPOSURE_PERCENT > 1.0:
        raise SafetyViolation(
            f"Refusing to start: config.MAX_TOTAL_EXPOSURE_PERCENT={config.MAX_TOTAL_EXPOSURE_PERCENT!r} "
            "must be a fraction of equity in (0, 1.0]."
        )
