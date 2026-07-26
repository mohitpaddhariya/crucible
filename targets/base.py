"""targets/base.py — the Target protocol and its error taxonomy.

Contract: docs/INTERFACES.md §3.1. Structural typing only; no I/O, no transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class AgentTurn:
    text: str
    event_id: int
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict)  # the last agent_response frame
    #: How many agent_response frames were merged into this one turn. 1 in the normal case;
    #: >1 when the agent split a turn (filler -> tool call -> answer). Additive and
    #: defaulted, so any Target that does not coalesce keeps working unchanged.
    parts: int = 1


@runtime_checkable
class Target(Protocol):
    """One conversation with the agent under test. Not reusable after close()."""

    async def open(self, scenario_vars: dict[str, str]) -> str: ...
    async def recv_agent_turn(self, timeout_s: float = 90.0) -> AgentTurn: ...
    async def send_user_turn(self, text: str) -> None: ...
    async def close(self, reason: str = "runner_decided") -> None: ...


class TargetError(Exception):
    """Base class for every target failure."""


class TargetTimeout(TargetError):
    """No agent_response arrived inside timeout_s."""


class TargetClosed(TargetError):
    """The socket died mid-conversation. There is no resume — the conversation is over.

    `close_code` is the WebSocket close code when the peer closed cleanly, else None.
    It matters: 1000 (normal closure) means the AGENT HUNG UP, which is a completely
    different event from a socket that dropped, and only the code tells them apart.
    """

    def __init__(self, message: str, *, close_code: int | None = None) -> None:
        super().__init__(message)
        self.close_code = close_code


class TargetProtocolError(TargetError):
    """We violated the ordering contract (e.g. spoke before the agent's opening)."""


__all__ = [
    "AgentTurn",
    "Target",
    "TargetError",
    "TargetTimeout",
    "TargetClosed",
    "TargetProtocolError",
]
