from unittest.mock import MagicMock, patch

from trading_hub import jev_decision
from trading_hub.jev_state import AccountState, JevState


def make_state():
    return JevState(
        timestamp=0.0, regime="TREND_UP", regime_confidence=0.8, allowed_assets=["BTC"], assets={},
        account=AccountState(equity=200.0, cash=200.0, realized_pnl=0.0, drawdown_pct=0.0, consecutive_wins=0, consecutive_losses=0, open_positions=[], recent_win_rate=None),
    )


def test_no_api_key_holds(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    decision = jev_decision.decide(make_state())
    assert decision.action == "HOLD"
    assert decision.source == "unavailable"
    assert "TYPESAFE_API_KEY" in decision.reason


def test_typesafe_exception_holds(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    with patch("typesafe_sdk.TypeSafeClient", side_effect=RuntimeError("connection refused")):
        decision = jev_decision.decide(make_state())
    assert decision.action == "HOLD"
    assert decision.source == "unavailable"
    assert "connection refused" in decision.reason


def test_valid_response_is_parsed_with_probability_gap(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")

    mock_answer = MagicMock()
    mock_answer.choice = "BTC_LONG"
    mock_answer.probabilities = {"BTC_LONG": 0.81, "HOLD": 0.12, "BTC_SHORT": 0.07}

    mock_response = MagicMock()
    mock_response.choices = {"decision": mock_answer}

    mock_client_instance = MagicMock()
    mock_client_instance.__enter__.return_value = mock_client_instance
    mock_client_instance.system_one.return_value = mock_response

    with patch("typesafe_sdk.TypeSafeClient", return_value=mock_client_instance):
        decision = jev_decision.decide(make_state())

    assert decision.action == "BTC_LONG"
    assert decision.source == "typesafe"
    assert decision.winner_probability == 0.81
    assert decision.second_probability == 0.12
    assert abs(decision.probability_gap - 0.69) < 1e-9


def test_malformed_action_holds(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")

    mock_answer = MagicMock()
    mock_answer.choice = "NOT_A_REAL_ACTION"
    mock_answer.probabilities = {"NOT_A_REAL_ACTION": 0.9}

    mock_response = MagicMock()
    mock_response.choices = {"decision": mock_answer}

    mock_client_instance = MagicMock()
    mock_client_instance.__enter__.return_value = mock_client_instance
    mock_client_instance.system_one.return_value = mock_response

    with patch("typesafe_sdk.TypeSafeClient", return_value=mock_client_instance):
        decision = jev_decision.decide(make_state())

    assert decision.action == "HOLD"
    assert decision.source == "unavailable"
    assert "invalid Jev response" in decision.reason


def test_empty_probabilities_holds(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")

    mock_answer = MagicMock()
    mock_answer.choice = "HOLD"
    mock_answer.probabilities = {}

    mock_response = MagicMock()
    mock_response.choices = {"decision": mock_answer}

    mock_client_instance = MagicMock()
    mock_client_instance.__enter__.return_value = mock_client_instance
    mock_client_instance.system_one.return_value = mock_response

    with patch("typesafe_sdk.TypeSafeClient", return_value=mock_client_instance):
        decision = jev_decision.decide(make_state())

    assert decision.action == "HOLD"
    assert decision.source == "unavailable"
