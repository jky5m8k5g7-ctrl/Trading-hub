import glob
import os

import pytest

from trading_hub import config, safety_guard


def test_check_passes_in_a_clean_environment(monkeypatch):
    for name in list(os.environ):
        if any(f in name.upper() for f in safety_guard._FORBIDDEN_ENV_SUBSTRINGS):
            monkeypatch.delenv(name, raising=False)
    safety_guard.check()  # must not raise


def test_check_rejects_kraken_trading_credentials(monkeypatch):
    monkeypatch.setenv("KRAKEN_API_SECRET", "sk-fake-value")
    with pytest.raises(safety_guard.SafetyViolation, match="KRAKEN_API_SECRET"):
        safety_guard.check()


def test_check_rejects_any_exchange_named_credential(monkeypatch):
    monkeypatch.setenv("COINBASE_PRIVATE_KEY", "fake")
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check()
    monkeypatch.delenv("COINBASE_PRIVATE_KEY")

    monkeypatch.setenv("HYPERLIQUID_WALLET_KEY", "fake")
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check()


def test_check_ignores_typesafe_api_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-real-typesafe-key")
    safety_guard.check()  # must not raise - TypeSafe is not an exchange


def test_check_rejects_leverage_above_one(monkeypatch):
    monkeypatch.setattr(config, "LEVERAGE", 5)
    with pytest.raises(safety_guard.SafetyViolation, match="LEVERAGE"):
        safety_guard.check()


def test_check_rejects_allow_leverage_true(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_LEVERAGE", True)
    with pytest.raises(safety_guard.SafetyViolation, match="ALLOW_LEVERAGE"):
        safety_guard.check()


def test_check_rejects_tampered_drawdown_bound(monkeypatch):
    monkeypatch.setattr(config, "DAILY_HARD_DRAWDOWN_PCT", 0)
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check()

    monkeypatch.setattr(config, "DAILY_HARD_DRAWDOWN_PCT", 90)
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check()


def test_check_rejects_tampered_exposure_bound(monkeypatch):
    monkeypatch.setattr(config, "MAX_TOTAL_EXPOSURE_PERCENT", 1.5)
    with pytest.raises(safety_guard.SafetyViolation):
        safety_guard.check()


# --- Static analysis: prove no real order-placement code path exists at all,
# not just that safety_guard blocks credentials. This is the actual
# defense-in-depth check against a future edit accidentally wiring one in. ---

_FORBIDDEN_SOURCE_PATTERNS = (
    "/0/private/",  # Kraken's authenticated (real-money) REST namespace
    "AddOrder",  # Kraken's real order-placement endpoint name
    "CancelOrder",
    "krakenex",  # the real Kraken trading SDK, as opposed to plain `requests`
    "ccxt",  # generic multi-exchange trading SDK capable of real orders
)


def test_no_source_file_references_real_order_placement():
    package_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_hub")
    offenders = []
    for path in glob.glob(os.path.join(package_dir, "*.py")):
        with open(path) as f:
            content = f.read()
        for pattern in _FORBIDDEN_SOURCE_PATTERNS:
            if pattern in content:
                offenders.append((os.path.basename(path), pattern))
    assert offenders == [], f"found references to real order placement: {offenders}"


def test_kraken_client_only_calls_public_endpoints():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_hub", "kraken_client.py")
    with open(path) as f:
        content = f.read()
    assert "KRAKEN_API_BASE" in content
    assert '"https://api.kraken.com/0/public"' in content or "'https://api.kraken.com/0/public'" in content
    assert "/0/private" not in content
