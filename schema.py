"""schema.py — the shared types. Zero behaviour, stdlib only.

Contract: docs/INTERFACES.md §2. Everything imports this; this imports nothing of ours,
so there can be no import cycle.

Do NOT add logic, I/O, or third-party imports here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = "1.0"

Speaker = Literal["agent", "persona"]
# "agent"   = X, the ElevenLabs agent under test (Tara)
# "persona" = Y, our Sarvam-driven synthetic customer
# There is no third speaker. The referee never appears in turns[].

EndCode = Literal[
    "turns_over", "seconds_over",                                        # hard, per-persona
    "goal_reached", "agent_offers_human_handoff", "persona_walked_away",  # soft
    "hard_stop_turns",                                                   # nuclear, always wins
    "wall_clock_cap",                                                    # runner-global safety cap
    "budget_exceeded",                                                   # run-level
    "target_disconnected", "error",                                      # failure paths
]
EndKind = Literal["hard", "soft", "error"]

Stage = Literal["config", "target", "persona_brain", "referee", "runner"]


@dataclass(frozen=True)
class Turn:
    idx: int                     # flat index across BOTH speakers, starts at 0
    speaker: Speaker
    text: str
    latency_ms: int
    ts: str                      # ISO-8601 UTC, 'Z' suffix, when the text was complete
    event_id: int | None = None  # ElevenLabs event_id for agent turns; None for persona turns
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EndReason:
    code: EndCode
    kind: EndKind
    detail: str                  # human-readable, e.g. "persona sent 12 replies (limit 12)"
    at_turn: int                 # turns[] idx that was last appended when this fired
    evidence: str | None = None  # verbatim quote — REQUIRED for kind == "soft", else None


@dataclass(frozen=True)
class Usage:
    calls: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0   # Sarvam counts reasoning inside this
    reasoning_chars: int = 0     # len(reasoning_content) summed; diagnostic only
    total_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            calls=self.calls + other.calls,
            retries=self.retries + other.retries,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            reasoning_chars=self.reasoning_chars + other.reasoning_chars,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(frozen=True)
class RunError:
    at: str                      # ISO-8601 UTC
    stage: Stage
    code: str                    # stable machine slug, see INTERFACES §8.3
    message: str
    turn_idx: int | None = None
    attempt: int = 1
    retryable: bool = False
    fatal: bool = False          # True => this ended the conversation


__all__ = [
    "SCHEMA_VERSION",
    "Speaker",
    "EndCode",
    "EndKind",
    "Stage",
    "Turn",
    "EndReason",
    "Usage",
    "RunError",
]
