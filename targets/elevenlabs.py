"""ElevenLabs Conversational-AI WebSocket target — LEVEL 0, text only.

Implements the `Target` protocol from docs/INTERFACES.md §3 against the wire
behaviour VERIFIED by scripts/spike_text_mode.py (4 live conversations,
25 July 2026). Nothing here was re-derived; every rule below is measured.

The three rules that break the build if broken:
  1. ElevenLabs' own conversation-simulation endpoint is NEVER touched (it runs
     the simulated user on their model, deleting Sarvam from the loop). This
     module speaks only the live /v1/convai/conversation WebSocket.
  2. The live agent is NEVER modified. `text_only` goes out as a per-conversation
     runtime override inside conversation_initiation_client_data, nothing else.
  3. The agent speaks FIRST, unprompted. open() does not consume that turn; the
     runner must call recv_agent_turn(25) before send_user_turn().

Two traps this module exists to absorb:
  - `audio` frames keep arriving even with text_only:true. They are 9600-byte
    comfort-noise (~2% full scale), not speech. They are discarded silently and
    counted. Their presence is NOT a failure. The real proof text_only was
    honoured is that `agent_chat_response_part` frames appear.
  - The server drives an application-level ping every ~1.7 s and drops the
    socket if you do not pong. We pong IMMEDIATELY and never sleep on `ping_ms`
    (it is the server's own RTT estimate; sleeping on it makes it climb forever).
    The first ping always has `ping_ms: null`.

There is no end-of-conversation event in this protocol and the server never
hangs up. An agent farewell is not an ending. close() is always client-side.

Requires: websockets>=14,<16 (v14 renamed extra_headers -> additional_headers).
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Literal

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from targets.base import (
    AgentTurn,
    TargetClosed,
    TargetError,
    TargetProtocolError,
    TargetTimeout,
)

API_HOST = "api.elevenlabs.io"
WS_URL = f"wss://{API_HOST}/v1/convai/conversation"
SIGNED_URL_ENDPOINT = f"https://{API_HOST}/v1/convai/conversation/get-signed-url"

OPEN_TIMEOUT_S = 30.0        # TCP/TLS/handshake
METADATA_TIMEOUT_S = 30.0    # conversation_initiation_metadata after init frame
MAX_FRAME_BYTES = 16 * 1024 * 1024

# One agent turn can arrive as MORE THAN ONE agent_response frame — the standard
# ElevenLabs tool-call sequence is agent_response("let me check that for you") ->
# agent_tool_response -> agent_response("your plan renews on 20 June"). Returning the first
# frame and leaving the second in the socket permanently skews the transcript by one turn:
# every later agent line answers the customer's PREVIOUS message, and the judge scores that
# against the agent. The live raw logs already contain agent_tool_response frames, so this
# target is one filler utterance away from it.
#
# So after an agent_response lands we keep listening briefly and MERGE whatever else
# arrives before we speak again into the same turn. Pings are still ponged during the wait
# (it is the same _pump), so the socket stays healthy.
#
# The wait is deliberately short, because a flat multi-second settle on every turn would
# push these conversations past their own `seconds_over` cap (the live ones already run
# 138-197 s against a 180 s limit). MEASURED from runs/*/raw/*.jsonl: an utterance is always
# STREAMED as agent_chat_response_part frames BEFORE its agent_response lands, and after an
# agent_response nothing but audio/ping arrives until we speak again. So streaming or tool
# activity AFTER an agent_response is a positive signal that a second utterance is on its
# way — and that, not the clock, is what buys the longer wait.
AGENT_TURN_SETTLE_S = 0.5        # quiet window after an agent_response
AGENT_TURN_FOLLOWUP_S = 2.5      # extension granted once more output is provably coming
AGENT_TURN_MAX_SETTLE_S = 8.0    # absolute ceiling on all of the above, per turn
MAX_AGENT_RESPONSE_PARTS = 6

# The 11 declared placeholders (PREFLIGHT §4). Order is cosmetic; presence is not.
SCENARIO_VAR_KEYS = (
    "subscriber_name", "call_reason", "call_intro", "plan_name", "amount_inr",
    "expiry_date", "content_hook", "offer_text", "renewal_date",
    "next_retry_date", "failure_reason",
)


def _b64_decoded_len(b64: str) -> int:
    """Byte length a base64 string decodes to, without decoding it."""
    if not b64:
        return 0
    return len(b64) // 4 * 3 - b64.count("=")


class ElevenLabsTarget:
    """One WebSocket conversation with the live agent. Not reusable after close()."""

    def __init__(
        self,
        *,
        api_key: str,
        agent_id: str,
        raw_log_path: Path | None = None,
        text_only: bool = True,
        auth: Literal["header", "signed"] = "header",
    ) -> None:
        if auth not in ("header", "signed"):
            raise ValueError(f"auth must be 'header' or 'signed', got {auth!r}")
        if not api_key:
            raise ValueError("api_key is empty")
        if not agent_id:
            raise ValueError("agent_id is empty")

        self._api_key = api_key
        self.agent_id = agent_id
        self.auth_method = auth
        self.text_only = text_only
        self.text_only_override_sent = False
        self.raw_log_path = Path(raw_log_path) if raw_log_path else None

        # read-only after open()
        self.conversation_id: str | None = None
        self.audio_frames_discarded: int = 0
        self.unknown_events: dict[str, int] = {}
        self.event_id_regressions: int = 0
        self.agent_turns: int = 0
        self.agent_characters: int = 0
        self.user_characters: int = 0
        #: How many EXTRA agent_response frames were merged into a turn. Never silent: the
        #: runner copies this into warnings[] so a coalesced turn is auditable.
        self.agent_response_parts_merged: int = 0

        self._ws: Any = None
        self._log_fh: Any = None
        self._opened = False
        self._closed = False
        self._first_turn_received = False
        self._last_outbound_at: float | None = None   # monotonic, for latency_ms
        self._last_event_id: int | None = None
        # An agent_response can in principle land while we are still waiting for
        # the metadata frame. Never drop it — stash it with its arrival time.
        self._pending_agent_response: tuple[dict, float] | None = None
        # The peer closed while we were draining the tail of a turn. The turn we already
        # have is good and is returned; the close surfaces on the next call instead.
        self._peer_closed: TargetClosed | None = None
        # monotonic time of the last frame proving the agent is mid-utterance
        # (agent_chat_response_part / agent_tool_response). See recv_agent_turn.
        self._agent_activity_at: float | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "ElevenLabsTarget":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close("context_exit")

    async def open(self, scenario_vars: dict[str, str]) -> str:
        """Connect, send the init frame, return the conversation_id.

        Does NOT consume the agent's opening turn — that is recv_agent_turn(25).
        """
        if self._opened:
            raise TargetProtocolError("open() called twice on the same target")
        if self._closed:
            raise TargetProtocolError("open() called after close()")

        self._open_raw_log()

        url, headers = await self._connect_target()
        try:
            self._ws = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=None,      # MANDATORY: the server drives its own app-level ping
                max_size=MAX_FRAME_BYTES,
                open_timeout=OPEN_TIMEOUT_S,
            )
        except (asyncio.TimeoutError, TimeoutError) as e:
            self._log("meta", {"event": "connect_timeout", "error": str(e)})
            raise TargetTimeout(f"websocket handshake timed out after {OPEN_TIMEOUT_S}s") from e
        except (WebSocketException, OSError) as e:
            self._log("meta", {"event": "connect_failed", "error": f"{type(e).__name__}: {e}"})
            raise TargetClosed(f"could not connect to the agent: {type(e).__name__}: {e}") from e

        self._opened = True
        self._log("meta", {"event": "socket_open", "auth": self.auth_method,
                           "agent_id": self.agent_id, "text_only": self.text_only})

        init = self._init_frame(scenario_vars)
        await self._send(init)
        self.text_only_override_sent = self.text_only

        meta = await self._pump("conversation_initiation_metadata",
                                time.monotonic() + METADATA_TIMEOUT_S)
        if meta is None:
            await self.close("no_metadata")
            raise TargetTimeout(
                f"no conversation_initiation_metadata within {METADATA_TIMEOUT_S}s"
            )

        ev = meta.get("conversation_initiation_metadata_event") or {}
        self.conversation_id = ev.get("conversation_id")
        if not self.conversation_id:
            raise TargetProtocolError(
                "conversation_initiation_metadata carried no conversation_id"
            )
        return self.conversation_id

    async def close(self, reason: str = "runner_decided") -> None:
        """Client-side close. Idempotent. Never raises."""
        if self._closed:
            return
        self._closed = True
        self._log("meta", {"event": "close", "reason": reason,
                           "conversation_id": self.conversation_id,
                           "audio_frames_discarded": self.audio_frames_discarded,
                           "unknown_events": self.unknown_events,
                           "event_id_regressions": self.event_id_regressions})
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if self._log_fh is not None:
            try:
                self._log_fh.flush()
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    # ── the turn loop ────────────────────────────────────────────────────────

    async def recv_agent_turn(self, timeout_s: float = 90.0) -> AgentTurn:
        """Drain frames until the agent's turn is complete. That is the turn boundary.

        "Complete" is not "the first agent_response arrived": one turn can be split across
        several agent_response frames (filler, tool call, real answer). Every frame that
        lands inside AGENT_TURN_SETTLE_S of the previous one, and before we speak again,
        belongs to THIS turn and is merged into it. Leaving the extras in the socket is
        what desynchronises the whole rest of the conversation.

        timeout_s: 25 for the unprompted opening, 90 for every later turn
        (measured agent latency is ~1 s, so 90 s is deliberately generous).
        """
        self._require_live("recv_agent_turn")

        if self._pending_agent_response is not None:
            frame, arrived_at = self._pending_agent_response
            self._pending_agent_response = None
        else:
            frame = await self._pump("agent_response", time.monotonic() + timeout_s)
            arrived_at = time.monotonic()
            if frame is None:
                raise TargetTimeout(
                    f"no agent_response within {timeout_s}s "
                    f"(conversation_id={self.conversation_id})"
                )

        parts = [frame]
        settle_started = time.monotonic()
        deadline = settle_started + AGENT_TURN_SETTLE_S
        while len(parts) < MAX_AGENT_RESPONSE_PARTS:
            try:
                extra = await self._pump("agent_response", deadline)
            except TargetClosed as exc:
                # The turn in hand is complete and usable. Hold the close and raise it on
                # the next call, exactly where a dead socket actually matters: the send.
                self._peer_closed = exc
                self._log("meta", {"event": "peer_closed_during_settle",
                                   "close_code": exc.close_code})
                break

            if extra is not None:
                parts.append(extra)
                arrived_at = time.monotonic()
                deadline = arrived_at + AGENT_TURN_SETTLE_S
                self.agent_response_parts_merged += 1
                self._log("meta", {"event": "agent_response_merged", "parts": len(parts)})
                continue

            # Quiet window elapsed. Only keep waiting if the agent has provably started
            # saying something else since the last frame we took.
            now = time.monotonic()
            still_talking = (
                self._agent_activity_at is not None and self._agent_activity_at > arrived_at
            )
            if still_talking and now - settle_started < AGENT_TURN_MAX_SETTLE_S:
                deadline = now + AGENT_TURN_FOLLOWUP_S
                continue
            break

        texts: list[str] = []
        event_id = 0
        for part in parts:
            ev = part.get("agent_response_event") or {}
            piece = ev.get("agent_response") or ""
            if piece:
                texts.append(piece)
            raw_event_id = ev.get("event_id")
            if isinstance(raw_event_id, (int, float)):
                event_id = int(raw_event_id)   # the LAST frame's id identifies the turn
        text = " ".join(texts)

        # event_id increments 1,2,3... shared with its chat_response_part stream.
        # A regression means a dropped or duplicated turn. Do NOT raise — the
        # caller records a non-fatal RunError(code="event_id_regression").
        if self._last_event_id is not None and event_id <= self._last_event_id:
            self.event_id_regressions += 1
            self._log("meta", {"event": "event_id_regression",
                               "previous": self._last_event_id, "received": event_id})
        self._last_event_id = event_id

        base = self._last_outbound_at if self._last_outbound_at is not None else arrived_at
        latency_ms = max(0, int((arrived_at - base) * 1000))

        self._first_turn_received = True
        self.agent_turns += 1
        self.agent_characters += len(text)
        return AgentTurn(text=text, event_id=event_id, latency_ms=latency_ms,
                         raw=parts[-1], parts=len(parts))

    async def send_user_turn(self, text: str) -> None:
        """Send one persona line. The agent speaks first — never call this first.

        No `user_transcript` echo comes back in text mode; the runner owns that
        line in turns[]. This target does not and must not record it.
        """
        self._require_live("send_user_turn")
        if not self._first_turn_received:
            raise TargetProtocolError(
                "send_user_turn() before the first recv_agent_turn(). The agent "
                "speaks first, unprompted, ~1-2 s after init; sending first "
                "deadlocks or double-speaks turn one."
            )
        await self._send({"type": "user_message", "text": text})
        self.user_characters += len(text)

    # ── internals ────────────────────────────────────────────────────────────

    def _require_live(self, what: str) -> None:
        if not self._opened:
            raise TargetProtocolError(f"{what}() before open()")
        if self._closed or self._ws is None:
            raise TargetClosed(f"{what}() after the socket was closed")
        if self._peer_closed is not None:
            # Deferred from the turn-settle drain: the turn was handed over intact, and the
            # close is reported here, with its close_code, exactly as if it had happened now.
            raise self._peer_closed

    def _init_frame(self, scenario_vars: dict[str, str]) -> dict:
        """The one and only client frame sent before anything else.

        `dynamic_variables` is a TOP-LEVEL SIBLING of conversation_config_override,
        not nested inside it. Nesting it silently yields unrendered {{placeholders}}.
        All values are strings (amount_inr is "1499", not 1499).
        """
        dyn = {k: str(v) for k, v in (scenario_vars or {}).items()}
        missing = [k for k in SCENARIO_VAR_KEYS if k not in dyn]
        if missing:
            raise TargetProtocolError(
                f"scenario_vars is missing declared dynamic variables: {missing}. "
                "Unsent placeholders render as literal {{braces}} in Tara's opening."
            )
        frame: dict = {
            "type": "conversation_initiation_client_data",
            "dynamic_variables": dyn,
        }
        if self.text_only:
            # RUNTIME override only. The live agent is never modified.
            frame["conversation_config_override"] = {"conversation": {"text_only": True}}
        return frame

    async def _connect_target(self) -> tuple[str, dict[str, str] | None]:
        if self.auth_method == "header":
            return f"{WS_URL}?agent_id={self.agent_id}", {"xi-api-key": self._api_key}
        # Verified-working fallback. The signed URL embeds a token — it is never logged.
        signed = await asyncio.to_thread(self._fetch_signed_url)
        return signed, None

    def _fetch_signed_url(self) -> str:
        url = f"{SIGNED_URL_ENDPOINT}?agent_id={self.agent_id}"
        req = urllib.request.Request(url, headers={"xi-api-key": self._api_key})
        try:
            with urllib.request.urlopen(req, timeout=OPEN_TIMEOUT_S) as r:
                body = json.loads(r.read().decode())
        except Exception as e:
            raise TargetClosed(f"get-signed-url failed: {type(e).__name__}: {e}") from e
        signed = body.get("signed_url")
        if not signed:
            raise TargetClosed("get-signed-url returned no signed_url")
        return signed

    async def _send(self, frame: dict) -> None:
        try:
            await self._ws.send(json.dumps(frame, ensure_ascii=False))
        except ConnectionClosed as e:
            raise TargetClosed(
                f"socket closed while sending {frame.get('type')}: {e}",
                close_code=_close_code(e),
            ) from e
        except (WebSocketException, OSError) as e:
            raise TargetClosed(
                f"send failed for {frame.get('type')}: {type(e).__name__}: {e}"
            ) from e
        self._log("out", frame)
        if frame.get("type") in ("conversation_initiation_client_data", "user_message"):
            # latency_ms is measured from the completion of the last outbound turn
            # frame (the init frame, for turn 0). Pongs must not reset the clock.
            self._last_outbound_at = time.monotonic()

    async def _pump(self, want: str, deadline: float) -> dict | None:
        """Drain frames until `want` arrives. None on deadline. Pongs inline.

        Pings arrive on the same socket, so a separate keepalive task would only
        add a second failure mode.
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                return None
            except ConnectionClosed as e:
                code = _close_code(e)
                self._log("meta", {"event": "socket_closed_by_peer",
                                   "close_code": code, "error": str(e)})
                raise TargetClosed(
                    f"socket died mid-conversation while waiting for {want}: {e}",
                    close_code=code,
                ) from e
            except (WebSocketException, OSError) as e:
                raise TargetClosed(f"recv failed: {type(e).__name__}: {e}") from e

            if isinstance(raw, (bytes, bytearray)):
                # Never observed. Log, count, discard — never raise.
                self._count_unknown("<binary_frame>")
                self._log("in", {"type": "<binary_frame>", "bytes": len(raw)})
                continue

            try:
                frame = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                self._count_unknown("<non_json>")
                self._log("in", {"type": "<non_json>", "chars": len(raw)})
                continue
            if not isinstance(frame, dict):
                self._count_unknown("<non_object>")
                self._log("in", {"type": "<non_object>"})
                continue

            ftype = frame.get("type") or "<no_type>"

            if ftype == "audio":
                # THE #1 FALSE ALARM. Audio frames always arrive, even with
                # text_only:true — 9600-byte comfort noise at ~2% full scale.
                # Discard silently. Do not decode, do not buffer, not a failure.
                self.audio_frames_discarded += 1
                self._log("in", frame)
                continue

            if ftype == "ping":
                ev = frame.get("ping_event") or {}
                self._log("in", frame)
                # Pong IMMEDIATELY. ping_ms is the server's RTT estimate, not an
                # instruction — sleeping on it feeds it back into itself and it
                # climbs forever. The first ping always has ping_ms: null.
                _ = ev.get("ping_ms") or 0
                await self._send({"type": "pong", "event_id": ev.get("event_id")})
                continue

            if ftype == "agent_chat_response_part":
                # Same text, streamed. Ignored at Level 0 — but its presence is
                # the real proof that text_only was honoured, and its TIMING is how
                # recv_agent_turn knows the agent has started a second utterance.
                self._agent_activity_at = time.monotonic()
                self._log("in", frame)
                continue

            if ftype in ("conversation_initiation_metadata", "agent_response"):
                self._log("in", frame)
            else:
                # Anything else the agent emits mid-turn (agent_tool_response is the one
                # actually observed) also proves it is still working on this turn.
                self._agent_activity_at = time.monotonic()
                self._count_unknown(ftype)
                self._log("in", frame)

            if ftype == want:
                return frame
            if ftype == "agent_response" and self._pending_agent_response is None:
                # Arrived while we were waiting for something else — keep it.
                self._pending_agent_response = (frame, time.monotonic())

    def _count_unknown(self, ftype: str) -> None:
        self.unknown_events[ftype] = self.unknown_events.get(ftype, 0) + 1

    # ── raw event log (§3.4) ─────────────────────────────────────────────────

    def _open_raw_log(self) -> None:
        if self.raw_log_path is None:
            return
        self.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        # Append: a retried conversation must not erase the failed attempt's frames.
        self._log_fh = self.raw_log_path.open("a", encoding="utf-8")

    def _log(self, direction: str, payload: dict) -> None:
        """One JSON object per line. No API key or signed-URL token, ever."""
        if self._log_fh is None:
            return
        try:
            line = json.dumps(
                {"t": round(time.time(), 3), "dir": direction,
                 "payload": _redact(payload)},
                ensure_ascii=False,
            )
            self._log_fh.write(line + "\n")
            self._log_fh.flush()
        except Exception:
            # Logging must never break a live conversation.
            pass


def _close_code(exc: ConnectionClosed) -> int | None:
    """The WebSocket close code the peer sent, if any.

    OBSERVED 25 July 2026, and it contradicts the spike's §8 ("the server never closed the
    socket"): after a farewell turn the server DOES sometimes close cleanly with 1000. The
    code is the only way to tell "the agent hung up" from "the socket dropped", so it is
    carried on the exception rather than buried in a formatted string.
    """
    for frame in (getattr(exc, "rcvd", None), getattr(exc, "sent", None)):
        code = getattr(frame, "code", None)
        if isinstance(code, int):
            return code
    return None


def _redact(payload: dict) -> dict:
    """Strip audio payloads. Nothing else in this protocol carries a secret."""
    ev = payload.get("audio_event")
    if payload.get("type") == "audio" and isinstance(ev, dict):
        b64 = ev.get("audio_base_64") or ""
        scrubbed = dict(ev)
        scrubbed["audio_base_64"] = f"<discarded {_b64_decoded_len(b64)} bytes>"
        return {**payload, "audio_event": scrubbed}
    return payload


__all__ = [
    "ElevenLabsTarget",
    "AgentTurn",
    "TargetError",
    "TargetTimeout",
    "TargetClosed",
    "TargetProtocolError",
]


if __name__ == "__main__":  # pragma: no cover
    # Offline smoke check: the Target surface is present and the init frame has
    # the exact verified shape. No network, no credentials used.
    t = ElevenLabsTarget(api_key="dummy", agent_id="agent_dummy")
    for m in ("open", "recv_agent_turn", "send_user_turn", "close"):
        assert callable(getattr(t, m)), m
    frame = t._init_frame({k: "x" for k in SCENARIO_VAR_KEYS})
    assert frame["type"] == "conversation_initiation_client_data"
    assert "dynamic_variables" in frame                       # top-level sibling
    assert "dynamic_variables" not in frame["conversation_config_override"]
    assert frame["conversation_config_override"]["conversation"]["text_only"] is True
    print(json.dumps(frame, indent=2))
