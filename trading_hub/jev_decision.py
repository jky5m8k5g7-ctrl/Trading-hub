"""The Jev AI Decision Bot: calls TypeSafe AI's System One classifier to pick
a directional action (or HOLD) with a full probability distribution.

This module only reports what Jev said - it does NOT decide whether a trade
is allowed to happen. That is risk_governor.py's job exclusively, per the
architecture principle: Jev chooses the potential trade, deterministic code
decides whether it executes. The one thing this module enforces itself is
the fail-safe list that's really "Jev produced nothing usable" rather than
a risk decision: no API key configured, a timeout, a malformed response, or
any other TypeSafe error all become an explicit HOLD with the reason
recorded - never a fabricated decision.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from trading_hub import config
from trading_hub.jev_state import JevState

log = logging.getLogger(__name__)


@dataclass
class JevDecision:
    action: str  # one of config.JEV_ACTIONS
    winner_probability: float  # 0-1
    second_probability: float  # 0-1, highest probability among the remaining candidates
    probabilities: dict[str, float]
    source: str  # "typesafe" or "unavailable"
    reason: str  # human-readable explanation, especially for "unavailable"
    timestamp: float
    latency_ms: float

    @property
    def probability_gap(self) -> float:
        return self.winner_probability - self.second_probability


def _unavailable(reason: str, started_at: float) -> JevDecision:
    log.warning("Jev decision unavailable, defaulting to HOLD: %s", reason)
    return JevDecision(
        action="HOLD",
        winner_probability=0.0,
        second_probability=0.0,
        probabilities={"HOLD": 1.0},
        source="unavailable",
        reason=reason,
        timestamp=time.time(),
        latency_ms=(time.monotonic() - started_at) * 1000,
    )


def decide(state: JevState) -> JevDecision:
    """Ask Jev for a decision. Falls back to an explicit, logged HOLD on any
    failure - never fabricates a decision when Jev didn't actually answer."""
    started_at = time.monotonic()

    if not os.environ.get("TYPESAFE_API_KEY"):
        return _unavailable("no TYPESAFE_API_KEY configured", started_at)

    try:
        from typesafe_sdk import Choice, TypeSafeClient

        criteria = {action: f"Take this action: {action}" for action in config.JEV_ACTIONS}
        with TypeSafeClient() as client:
            response = client.system_one(
                state=state.to_json_dict(),
                questions={
                    "decision": Choice(
                        instructions=(
                            "You are Jev, a bounded crypto paper-trading decision bot. "
                            "Given the current market state, regime, account state, and open "
                            "positions, choose the single best action right now."
                        ),
                        criteria=criteria,
                    ),
                },
                timeout=config.JEV_DECISION_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        return _unavailable(f"TypeSafe call failed: {exc}", started_at)

    try:
        answer = response.choices["decision"]
        action = answer.choice
        probabilities = dict(answer.probabilities)
        if action not in config.JEV_ACTIONS or not probabilities:
            raise ValueError(f"malformed Jev response: action={action!r} probabilities={probabilities!r}")
    except Exception as exc:
        return _unavailable(f"invalid Jev response: {exc}", started_at)

    winner_probability = probabilities.get(action, 0.0)
    remaining = sorted((p for a, p in probabilities.items() if a != action), reverse=True)
    second_probability = remaining[0] if remaining else 0.0

    return JevDecision(
        action=action,
        winner_probability=winner_probability,
        second_probability=second_probability,
        probabilities=probabilities,
        source="typesafe",
        reason="ok",
        timestamp=time.time(),
        latency_ms=(time.monotonic() - started_at) * 1000,
    )
