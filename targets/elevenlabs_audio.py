"""ElevenLabs Conversational-AI WebSocket target — LEVEL 1, half-duplex AUDIO.

Contract: docs/LEVEL1_SPEC.md. Every rule below is a MEASUREMENT from one of the
three live spikes, not a design preference. Where a comment says "measured", the
number came off the wire; where it says "a spike died without it", it did.

THE THREE FINDINGS THIS FILE IS BUILT AROUND (LEVEL1_SPEC §0)
-------------------------------------------------------------

1. THE READER TASK IS PERMANENTLY LIVE. The server's "No user message received
   for 60 seconds" 1002 close is a PONG rule, not a user-message rule. A clean
   2x2 proved it: every arm that kept ponging survived 100+ s of total user
   silence (112 s of pure idle after a real spoken turn); every arm that stopped
   ponging died. Level 0's bug is structural — `recv_agent_turn()` returns and
   the caller then goes away for 40+ s doing LLM work while NOBODY reads the
   socket. So here the reader is an asyncio task alive from open() to close(),
   draining frames and ponging inline, publishing into internal queues.
   **No compute step ever owns the socket.** Level 0's 40 s persona bound treated
   the symptom and is retired.

2. THE END-OF-TURN SIGNAL IS AN AMPLITUDE FLOOR. There is no end-of-turn event,
   and audio frames NEVER stop: the agent has `background_sound: office1` @ 0.08
   streaming a 9600-byte carrier forever (~3.3 frames/s, even in text mode), so
   "frames stopped" is meaningless. `agent_response` is a turn-START marker — it
   arrives 0.31-0.83 s AFTER the first speech frame and 9-11 s BEFORE the last
   one, so using it as turn-end truncates every turn by ~10 s and you talk over
   her. `event_id` does not reset either (it is a global counter shared with
   pings: 1, 40, 97, 145).
   See `TurnDetector` for the calibration.

3. NEVER STREAM SILENCE AS A KEEPALIVE. It is actively harmful and it is the one
   experimental arm that DIED — at 59 s, faster than the problem it was meant to
   solve. Zero-filled chunks tell `scribe_realtime` the mic is live; the turn
   model endpoints them as empty user turns on the `turn_timeout=10.0` cadence,
   Tara asks "Are you still there?", then invokes `end_call` and hangs up.
   Between turns we send NOTHING. Silence is streamed in exactly one place —
   `speak_and_hold()`'s bounded, transcript-triggered mic hold (§4.3).

WHAT THIS FILE DELIBERATELY DOES NOT PORT FROM `targets/elevenlabs.py` (§5)
--------------------------------------------------------------------------
  * the `_agent_activity_at` settle heuristic — it keys off
    `agent_chat_response_part`, which has 0 occurrences across every voice
    capture and is absent from the agent's `client_events`. It would silently
    degrade to a bare 0.5 s settle.
  * the `event_id_regression` RunError — `event_id` is a global counter in voice
    mode, so +1 semantics are a text-mode-only fact.
  * the "presence of `agent_chat_response_part` proves text_only" assertion — in
    voice its ABSENCE is the expected state, not a failure.

Requires: websockets>=14,<16 (v14 renamed extra_headers -> additional_headers).
"""

from __future__ import annotations

import array
import asyncio
import base64
import inspect
import json
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from targets.base import (
    AgentTurn,
    TargetClosed,
    TargetError,
    TargetProtocolError,
    TargetTimeout,
)
from targets.elevenlabs import SCENARIO_VAR_KEYS

API_HOST = "api.elevenlabs.io"
WS_URL = f"wss://{API_HOST}/v1/convai/conversation"
SIGNED_URL_ENDPOINT = f"https://{API_HOST}/v1/convai/conversation/get-signed-url"

OPEN_TIMEOUT_S = 30.0
METADATA_TIMEOUT_S = 30.0
MAX_FRAME_BYTES = 16 * 1024 * 1024

# ── wire format: VERIFIED both directions on the live agent ──────────────────
# asr.user_input_audio_format   == pcm_16000
# tts.agent_output_audio_format == pcm_16000
# == raw signed 16-bit little-endian mono PCM @ 16 kHz, no container.
AUDIO_FORMAT = "pcm_16000"
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_MS // 1000   # 3200

# ── turn detector defaults, calibrated over 8 turns, confirmed over 11 more ──
# min per-turn speech peak 3266; max carrier-only peak 2942 (2044 in every
# capture on disk); longest sub-threshold run INSIDE a real turn was 1 frame;
# longest intra-turn wall gap 0.918 s. 0.9 s was measured TOO TIGHT — it split a
# real turn at el 28.876 of runs/_spike_audio/events_turn_nopong.jsonl, which is
# regression fixture 2 (§8) and is asserted in scripts/smoke_audio_offline.py.
SPEECH_PEAK_MIN = 3000        # ~9.2 % of full scale
QUIET_FRAMES = 5              # ~1.5 s at ~300 ms/frame
QUIET_WALL_S = 1.5

# ── mic hold (§0.3, §4.3) ────────────────────────────────────────────────────
# Measured with the transcript trigger: 2.2-2.8 s per turn; the endpoint fires
# 2.1-2.4 s after the last real chunk. Safe window ~[3, 8] s. The spike's 12 s
# was noted as too generous; past ~10 s you walk into the empty-turn hangup.
MIC_HOLD_BOUND_S = 8.0
LEAD_MS = 300                 # short silence lead-in before the utterance
TAIL_MS = 1500                # §4.1's trailing silence — see speak_and_hold()

OPENING_TIMEOUT_S = 25.0      # speech began at 1.3 s in every capture
TURN_TIMEOUT_S = 90.0

DEFAULT_VOICE = {"model": "bulbul:v2", "speaker": "anushka"}


def utc_now() -> str:
    """ISO-8601 UTC, millisecond precision, 'Z' suffix (INTERFACES §8.3)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def peak_amplitude(pcm: bytes) -> int:
    """Peak |sample| of signed 16-bit LE mono PCM. stdlib only, C-speed.

    `array` rather than `struct.unpack` + a genexp because this runs on every
    inbound frame for the whole conversation (~3.3 frames/s x 4800 samples).
    `-min(a)` rather than `abs(min(a))` so a full-negative -32768 sample cannot
    overflow into a wrong answer.
    """
    n = len(pcm) // 2
    if n == 0:
        return 0
    a = array.array("h")
    a.frombytes(pcm[: n * 2])
    if sys.byteorder != "little":
        a.byteswap()
    return max(max(a), -min(a))


# ======================================================================================
# The turn detector — the whole of finding §0.2, isolated so it can be replayed offline
# ======================================================================================


@dataclass
class DetectedTurn:
    """One completed agent speech turn, as the amplitude detector saw it."""
    # repr=False: a DetectedTurn carries ~400 KB of PCM and it WILL end up inside
    # an assertion message or a log line otherwise.
    pcm: bytes = field(repr=False)   # speech frames only; the trailing quiet run is trimmed
    start_s: float                # detector clock (monotonic live, capture `el` in replay)
    end_s: float
    peak: int                     # per-turn peak, LOGGED so calibration stays auditable
    speech_frames: int
    event_id: int | None          # event_id of the audio frame that opened the turn
    reason: Literal["quiet_frames", "quiet_wall"]

    @property
    def speech_s(self) -> float:
        return round(len(self.pcm) / BYTES_PER_SAMPLE / SAMPLE_RATE, 2)


class TurnDetector:
    """Amplitude-floor end-of-turn detection. No event tells us when Tara stops.

    speech      iff  frame peak >= `speech_peak_min`
    turn is over when `quiet_frames` consecutive sub-threshold frames arrive,
                 OR'd with a `quiet_wall_s` wall-clock backstop since the last
                 speech frame (which also covers frames stalling entirely).

    THE MULTI-FRAME HOLD IS WHAT MAKES THIS ROBUST AND IT IS NOT OPTIONAL: the
    worst-case margin is thin (carrier max 2942 vs speech min 3266) even though
    the typical margin is 7x (carrier <= 2044, speech >= 15236). A single-frame
    test is forbidden.

    The clock is passed in rather than read, so `scripts/smoke_audio_offline.py`
    can replay real captured frames at their real captured timestamps. Same code
    path, zero network.
    """

    def __init__(
        self,
        *,
        speech_peak_min: int = SPEECH_PEAK_MIN,
        quiet_frames: int = QUIET_FRAMES,
        quiet_wall_s: float = QUIET_WALL_S,
        keep_audio: bool = True,
    ) -> None:
        if quiet_frames < 2:
            raise ValueError(
                "quiet_frames must be >= 2: a single-frame end-of-turn test is "
                "forbidden (LEVEL1_SPEC §9.4 — the carrier/speech margin is thin)"
            )
        self.speech_peak_min = int(speech_peak_min)
        self.quiet_frames = int(quiet_frames)
        self.quiet_wall_s = float(quiet_wall_s)
        self.keep_audio = keep_audio

        self.frames_seen = 0
        self.speech_frames_seen = 0
        self.carrier_peak_max = 0     # drift tripwire: background_sound is server config

        self._in_turn = False
        self._quiet_run = 0
        self._last_speech_at: float | None = None
        self._buf: list[bytes] = []
        self._peak = 0
        self._start_s: float | None = None
        self._event_id: int | None = None

    @property
    def in_turn(self) -> bool:
        return self._in_turn

    def feed(self, pcm: bytes, *, now: float, event_id: int | None = None) -> DetectedTurn | None:
        """Decode one inbound audio frame. Returns a turn iff this frame ended one."""
        return self.feed_peak(peak_amplitude(pcm), pcm=pcm, now=now, event_id=event_id)

    def feed_peak(
        self,
        peak: int,
        *,
        now: float,
        pcm: bytes | None = None,
        event_id: int | None = None,
    ) -> DetectedTurn | None:
        """Same as feed(), for replaying captures that recorded the peak, not the bytes.

        The protocol-spike logs store `"<9600 bytes, peak=305>"` instead of the
        base64 payload, so this is the entry point fixture 2 uses for them.
        """
        self.frames_seen += 1

        # The wall-clock backstop is evaluated on EVERY frame, before the frame is
        # classified — not only on quiet frames. That ordering is exactly what the
        # el-28.876 calibration measures: the gap there is 0.918 s between two
        # SPEECH frames straddling one quiet frame, so a 0.9 s backstop splits a
        # real turn and a 1.5 s one does not. Check it on quiet frames only and
        # 0.9 s would look safe, and the calibration would be meaningless.
        finished: DetectedTurn | None = None
        if (
            self._in_turn
            and self._last_speech_at is not None
            and (now - self._last_speech_at) >= self.quiet_wall_s
        ):
            finished = self._close(now, "quiet_wall")

        if peak >= self.speech_peak_min:
            self.speech_frames_seen += 1
            if not self._in_turn:
                self._in_turn = True
                self._buf = []
                self._peak = 0
                self._quiet_run = 0
                self._start_s = now
                self._event_id = event_id
            self._quiet_run = 0
            self._last_speech_at = now
            self._peak = max(self._peak, peak)
            if self.keep_audio and pcm is not None:
                self._buf.append(pcm)
            else:
                self._buf.append(b"")
        elif self._in_turn:
            self._quiet_run += 1
            self._buf.append(pcm if (self.keep_audio and pcm is not None) else b"")
            if self._quiet_run >= self.quiet_frames:
                finished = self._close(now, "quiet_frames")
        else:
            self.carrier_peak_max = max(self.carrier_peak_max, peak)

        return finished

    def tick(self, now: float) -> DetectedTurn | None:
        """Wall-clock backstop for the case frames stop arriving altogether.

        Never observed live (the carrier is continuous), but a dying socket makes
        it reachable and a turn stuck open forever is a hang, not an error.
        """
        if (
            self._in_turn
            and self._last_speech_at is not None
            and (now - self._last_speech_at) >= self.quiet_wall_s
        ):
            return self._close(now, "quiet_wall")
        return None

    def flush(self, now: float) -> DetectedTurn | None:
        """End-of-stream: emit a turn that is still open. Replay/teardown only."""
        return self._close(now, "quiet_wall") if self._in_turn else None

    def _close(self, now: float, reason: str) -> DetectedTurn:
        # Trim the trailing quiet run — those frames are carrier, not speech, and
        # shipping them to Saaras just pays for silence.
        keep = self._buf[: len(self._buf) - self._quiet_run] if self._quiet_run else self._buf
        turn = DetectedTurn(
            pcm=b"".join(keep),
            start_s=self._start_s if self._start_s is not None else now,
            end_s=now,
            peak=self._peak,
            speech_frames=len(keep),
            event_id=self._event_id,
            reason=reason,  # type: ignore[arg-type]
        )
        self._in_turn = False
        self._quiet_run = 0
        self._buf = []
        self._peak = 0
        self._start_s = None
        self._event_id = None
        return turn


# ======================================================================================
# Results handed back to runner/loop.py
# ======================================================================================


@dataclass(frozen=True)
class AudioAgentTurn(AgentTurn):
    """An `AgentTurn` plus the per-turn audio evidence of LEVEL1_SPEC §3.2.

    A SUBCLASS on purpose: `isinstance(t, AgentTurn)` still holds, every Level 0
    reader keeps working untouched, and a caller that wants the audio meta asks
    for it with `getattr(turn, "audio_meta", {})`. `targets/base.py` is not
    edited — Level 1 is additive or it is not backward compatible.
    """
    audio_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PersonaTurnResult:
    """What `send_persona_turn()` did, as the §3.2 persona-turn `meta` block."""
    text: str
    meta: dict[str, Any]


class MicHoldTimeout(TargetError):
    """`no_user_transcript` — the turn-2 deadlock (§0.3, §1.1 step 10c, §9.1).

    THIS IS A BUG, NEVER "a slow turn". Stream-then-stop works for exactly one
    turn per conversation and then deadlocks silently forever: the socket stays
    healthy, nothing errors, the run just looks short. Two full conversations
    were burned proving it. If this fires, the mic hold is broken — do not raise
    the bound past 8 s to "fix" it, that only trades this failure for the
    empty-turn hangup at 59 s.
    """
    code = "no_user_transcript"


# ======================================================================================
# The target
# ======================================================================================


class ElevenLabsAudioTarget:
    """One half-duplex VOICE conversation with the live agent. Not reusable after close().

    Public surface (this is what `runner/loop.py` integrates against):

        conversation_id = await target.open(scenario_vars)   # agent speaks FIRST
        turn   = await target.recv_agent_turn(25.0)          # AudioAgentTurn
        result = await target.send_persona_turn(text)        # TTS + stream + mic hold
        await target.close("turns_over")

    `recv_agent_turn()` keeps the Level 0 signature and returns an `AgentTurn`
    subclass, so the loop's call shape barely moves. `send_persona_turn()`
    replaces `send_user_turn()`: it does TTS, paced streaming and the mic hold
    internally, because all three are one absolute-clock operation (§4.2) and
    splitting them across the caller would reintroduce drift.
    """

    def __init__(
        self,
        *,
        api_key: str,
        agent_id: str,
        sarvam_api_key: str | None = None,
        raw_log_path: Path | None = None,
        audio_dir: Path | None = None,
        auth: Literal["header", "signed"] = "header",
        voice: dict[str, str] | None = None,
        speech_peak_min: int = SPEECH_PEAK_MIN,
        quiet_frames: int = QUIET_FRAMES,
        quiet_wall_s: float = QUIET_WALL_S,
        mic_hold_bound_s: float = MIC_HOLD_BOUND_S,
        lead_ms: int = LEAD_MS,
        tail_ms: int = TAIL_MS,
        stt_cross_check: bool = False,
        tts: Any = None,
        stt: Any = None,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        if auth != "header":
            # §9.9: only the header path was ever exercised in voice mode. The
            # signed-URL fallback stays text-mode-only until it is probed.
            raise ValueError(
                f"auth={auth!r} is unsupported in audio mode: only the header auth "
                "path has been exercised in voice mode (LEVEL1_SPEC §9.9)"
            )
        if not api_key:
            raise ValueError("api_key is empty")
        if not agent_id:
            raise ValueError("agent_id is empty")
        if mic_hold_bound_s > 8.0:
            # §9.1: raising the bound past ~10 s trades the deadlock for the
            # empty-turn hangup, which is a WORSE failure (the agent hangs up).
            raise ValueError(
                f"mic_hold_bound_s={mic_hold_bound_s} exceeds the 8.0 s ceiling "
                "(LEVEL1_SPEC §4.3: past ~10 s the empty-turn endpointing hangs up "
                "the call at 59 s)"
            )

        self._api_key = api_key
        self.agent_id = agent_id
        self.auth_method = auth
        self.mode = "audio"
        self.text_only = False
        #: Always False in voice mode: NO conversation_config_override is sent at all.
        self.text_only_override_sent = False
        self.raw_log_path = Path(raw_log_path) if raw_log_path else None
        self.audio_dir = Path(audio_dir) if audio_dir else None
        self.voice = dict(voice or DEFAULT_VOICE)
        self.mic_hold_bound_s = float(mic_hold_bound_s)
        self.lead_ms = int(lead_ms)
        self.tail_ms = int(tail_ms)
        self.stt_cross_check = bool(stt_cross_check)
        self._sarvam_api_key = sarvam_api_key
        self._tts = tts
        self._stt = stt
        self._owns_tts = False        # an injected client belongs to the caller
        self._owns_stt = False
        self._connect = connect or websockets.connect

        # ── read-only after open() ───────────────────────────────────────────
        self.conversation_id: str | None = None
        self.user_input_audio_format: str | None = None
        self.agent_output_audio_format: str | None = None
        self.audio_frames_received: int = 0
        self.audio_bytes_received: int = 0
        self.audio_chunks_sent: int = 0
        self.pings_received: int = 0
        self.pongs_sent: int = 0
        self.unknown_events: dict[str, int] = {}
        self.agent_turns: int = 0
        self.agent_characters: int = 0
        self.user_characters: int = 0
        #: Level 0 parity: audio frames are never "discarded" here, they are the signal.
        self.audio_frames_discarded: int = 0
        self.event_id_regressions: int = 0        # never incremented in voice mode (§5)
        self.agent_response_parts_merged: int = 0
        self.conversation_over: bool = False      # end_call seen
        self.end_call_evidence: dict[str, Any] | None = None
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.interruptions: int = 0               # §9.5 — never observed; counted, not fatal
        self.warnings: list[str] = []

        self.detector = TurnDetector(
            speech_peak_min=speech_peak_min,
            quiet_frames=quiet_frames,
            quiet_wall_s=quiet_wall_s,
        )

        self._ws: Any = None
        self._log_fh: Any = None
        self._reader: asyncio.Task | None = None
        self._opened = False
        self._closed = False
        self._first_turn_received = False
        self._t0 = 0.0
        self._turn_idx = 0
        self._last_outbound_at: float | None = None
        self._stop = asyncio.Event()
        self._metadata_evt = asyncio.Event()
        self._metadata: dict[str, Any] = {}
        self._turns: asyncio.Queue[DetectedTurn] = asyncio.Queue()
        self._agent_responses: list[dict[str, Any]] = []
        self._consumed_responses: set[int] = set()
        self.user_transcripts: list[dict[str, Any]] = []
        self._reader_error: BaseException | None = None
        self._send_lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "ElevenLabsAudioTarget":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close("context_exit")

    async def open(self, scenario_vars: dict[str, str]) -> str:
        """Connect, send the init frame, START THE READER, return the conversation_id.

        Does NOT consume the agent's opening turn — that is
        `recv_agent_turn(25)`. The agent speaks first, unprompted; speaking first
        deadlocks or double-speaks turn one.
        """
        if self._opened:
            raise TargetProtocolError("open() called twice on the same target")
        if self._closed:
            raise TargetProtocolError("open() called after close()")

        self._open_raw_log()
        self._t0 = time.monotonic()

        url = f"{WS_URL}?agent_id={self.agent_id}"
        headers = {"xi-api-key": self._api_key}
        try:
            self._ws = await self._connect(
                url,
                additional_headers=headers,
                ping_interval=None,   # MANDATORY: the server drives its own app-level ping
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
        self._log("meta", {"event": "socket_open", "mode": "audio",
                           "agent_id": self.agent_id, "auth": self.auth_method,
                           "voice": self.voice})

        await self._send(self._init_frame(scenario_vars))

        # THE READER IS LIVE FROM HERE UNTIL close(). Nothing else reads the socket.
        self._reader = asyncio.create_task(self._read_forever(), name="el_audio_reader")

        try:
            await asyncio.wait_for(self._metadata_evt.wait(), timeout=METADATA_TIMEOUT_S)
        except (asyncio.TimeoutError, TimeoutError):
            await self.close("no_metadata")
            raise TargetTimeout(
                f"no conversation_initiation_metadata within {METADATA_TIMEOUT_S}s"
            ) from None

        self.conversation_id = self._metadata.get("conversation_id")
        if not self.conversation_id:
            raise TargetProtocolError(
                "conversation_initiation_metadata carried no conversation_id"
            )

        # §1.1: assert BOTH formats, never assume. A silent format mismatch is
        # wrong-speed audio in both directions with no error anywhere.
        self.user_input_audio_format = self._metadata.get("user_input_audio_format")
        self.agent_output_audio_format = self._metadata.get("agent_output_audio_format")
        for name, got in (("user_input_audio_format", self.user_input_audio_format),
                          ("agent_output_audio_format", self.agent_output_audio_format)):
            if got != AUDIO_FORMAT:
                raise TargetProtocolError(
                    f"{name}={got!r}, expected {AUDIO_FORMAT!r}. Every byte of pacing, "
                    "chunking and amplitude calibration in this module assumes signed "
                    "16-bit LE mono PCM @ 16 kHz."
                )
        return self.conversation_id

    async def close(self, reason: str = "runner_decided") -> None:
        """Client-side close. Idempotent. Never raises.

        TEARDOWN ORDER IS LOAD-BEARING: socket, then reader task, then the log
        file. Closing the log first raises ValueError('I/O operation on closed
        file') inside the still-draining reader — measured.
        """
        if self._closed:
            return
        self._closed = True
        self._log("meta", {"event": "close", "reason": reason,
                           "conversation_id": self.conversation_id,
                           "audio_frames_received": self.audio_frames_received,
                           "audio_chunks_sent": self.audio_chunks_sent,
                           "pings_received": self.pings_received,
                           "pongs_sent": self.pongs_sent,
                           "carrier_peak_max": self.detector.carrier_peak_max,
                           "unknown_events": self.unknown_events,
                           "conversation_over": self.conversation_over})

        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):   # noqa: B014
                pass

        await self._close_speech_clients()

        # ONLY NOW. The reader is provably done writing to it.
        if self._log_fh is not None:
            try:
                self._log_fh.flush()
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    # ── the permanently-live reader ──────────────────────────────────────────

    async def _read_forever(self) -> None:
        """Drain frames and pong, from open() to close(). NEVER awaits compute.

        No STT, no TTS, no LLM, no disk-heavy work happens on this task. That is
        finding §0.1 expressed as code: the moment this task stops running, the
        server kills the socket ~60 s later with a 1002 that reads like a
        user-message timeout and is not one.
        """
        try:
            while True:
                try:
                    # A bounded recv so the wall-clock backstop still runs if
                    # frames stop arriving altogether (a dying socket). All
                    # detector mutation stays on this one task.
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=0.25)
                except (asyncio.TimeoutError, TimeoutError):
                    done = self.detector.tick(self._el())
                    if done is not None:
                        self._publish_turn(done)
                    continue

                if isinstance(raw, (bytes, bytearray)):
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
                await self._on_frame(frame)
        except asyncio.CancelledError:
            raise
        except ConnectionClosed as e:
            self.close_code = _close_code(e)
            self.close_reason = getattr(getattr(e, "rcvd", None), "reason", None)
            self._log("meta", {"event": "socket_closed_by_peer",
                               "close_code": self.close_code,
                               "close_reason": self.close_reason,
                               "conversation_over": self.conversation_over})
        except Exception as e:                                        # noqa: BLE001
            self._reader_error = e
            self._log("meta", {"event": "reader_error", "error": f"{type(e).__name__}: {e}"})
        finally:
            self._stop.set()

    async def _on_frame(self, frame: dict) -> None:
        ftype = frame.get("type") or "<no_type>"

        if ftype == "audio":
            ev = frame.get("audio_event") or {}
            pcm = base64.b64decode(ev.get("audio_base_64") or "")
            self.audio_frames_received += 1
            self.audio_bytes_received += len(pcm)
            pk = peak_amplitude(pcm)
            # Log the peak, never the payload: the raw log stays small AND stays
            # replayable through this same detector (that is fixture 2's input
            # format). Per-turn peaks are what makes any carrier drift visible
            # before it flips a verdict (§9.4).
            self._log("in", {"type": "audio",
                             "audio_event": {"audio_base_64": f"<{len(pcm)} bytes, peak={pk}>",
                                             "event_id": ev.get("event_id"),
                                             "is_final": ev.get("is_final")}})
            done = self.detector.feed_peak(pk, pcm=pcm, now=self._el(),
                                           event_id=ev.get("event_id"))
            if done is not None:
                self._publish_turn(done)
            return

        if ftype == "ping":
            ev = frame.get("ping_event") or {}
            self.pings_received += 1
            self._log("in", frame)
            # PONG IMMEDIATELY. `ping_ms` is the server's own RTT estimate, not an
            # instruction — sleeping on it feeds it back into itself and it climbs
            # forever. The first ping always has ping_ms: null. THIS is the keepalive.
            await self._send({"type": "pong", "event_id": ev.get("event_id")})
            self.pongs_sent += 1
            return

        self._log("in", frame)

        if ftype == "conversation_initiation_metadata":
            self._metadata = frame.get("conversation_initiation_metadata_event") or {}
            self._metadata_evt.set()
            return

        if ftype == "agent_response":
            # A turn-START marker, NOT a turn end. It lands 0.31-0.83 s after the
            # first speech frame and 9-11 s before the last one. Record it and
            # keep listening to the amplitude; ending the turn here truncates
            # every turn by ~10 s and we talk over her.
            ev = frame.get("agent_response_event") or {}
            self._agent_responses.append({
                "el": self._el(),
                "at": utc_now(),
                "event_id": ev.get("event_id"),
                "text": ev.get("agent_response") or "",
                "raw": frame,
            })
            return

        if ftype == "user_transcript":
            # Tara's scribe_realtime ASR of OUR audio. Two jobs: it is the ONLY
            # control signal that closes the mic hold (§0.3), and it is a
            # first-class product finding (§2.2) — never an input to any
            # deterministic check.
            ev = frame.get("user_transcription_event") or {}
            self.user_transcripts.append({
                "el": self._el(),
                "at": utc_now(),
                "text": ev.get("user_transcript") or "",
                "event_id": ev.get("event_id"),
            })
            return

        if ftype == "agent_tool_response":
            # OBSERVED shape: the payload sits under `agent_tool_response`, NOT
            # `agent_tool_response_event` like every other event on this socket.
            ev = frame.get("agent_tool_response") or frame.get("agent_tool_response_event") or {}
            if ev.get("tool_name") == "end_call":
                # THE explicit end-of-conversation signal text mode never had. A
                # real ending, followed ~0.4 s later by a clean 1000 close — not
                # the `target_disconnected` error it would otherwise be recorded as.
                self.conversation_over = True
                self.end_call_evidence = dict(ev)
                self._log("meta", {"event": "end_call", "tool": ev})
            self._count_unknown(ftype)
            return

        if ftype == "interruption":
            # §9.5: declared in client_events, never observed. Count it, warn,
            # keep going — barge-in is unmapped and we always wait for turn-end.
            self.interruptions += 1
            self.warnings.append("unexpected_interruption: barge-in behaviour is unmapped")
            self._count_unknown(ftype)
            return

        self._count_unknown(ftype)

    def _publish_turn(self, turn: DetectedTurn) -> None:
        self._turns.put_nowait(turn)
        self._log("meta", {"event": "agent_turn_end",
                           "start_s": round(turn.start_s, 3),
                           "end_s": round(turn.end_s, 3),
                           "speech_s": turn.speech_s,
                           "speech_frames": turn.speech_frames,
                           "peak": turn.peak,               # AUDITABLE CALIBRATION
                           "event_id": turn.event_id,
                           "reason": turn.reason})

    # ── receiving Tara ───────────────────────────────────────────────────────

    async def recv_agent_turn(self, timeout_s: float = TURN_TIMEOUT_S) -> AudioAgentTurn:
        """Wait for Tara's turn to END (amplitude), then return it.

        `.text` is the `agent_response` text VERBATIM — Tara's own words, not our
        transcription of her audio (§2.1). `judge/checks.py` treats a violation as
        a FACT, and that guarantee only holds if the text is what the agent's LLM
        actually produced; our own Saaras pass measurably injects errors ("I hear
        you on the price" -> "I hear you want the price", "10%" could become
        "50%") which would mint false hallucination violations with the full
        weight of a deterministic fact.

        timeout_s: 25 for the unprompted opening, 90 for every later turn.
        """
        self._require_live("recv_agent_turn")
        deadline = time.monotonic() + timeout_s

        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                turn = await asyncio.wait_for(self._turns.get(), timeout=min(remaining, 0.2))
                break
            except (asyncio.TimeoutError, TimeoutError):
                if not self._turns.empty():
                    turn = self._turns.get_nowait()
                    break
                if self._stop.is_set():
                    # The socket is gone. If `end_call` came first this is a real
                    # ending and the caller decides; otherwise it is a disconnect.
                    if self.conversation_over:
                        raise TargetClosed(
                            "the agent invoked end_call and hung up",
                            close_code=self.close_code,
                        )
                    raise TargetClosed(
                        f"socket died while waiting for an agent turn "
                        f"(close_code={self.close_code}, reason={self.close_reason!r})",
                        close_code=self.close_code,
                    )
                if time.monotonic() >= deadline:
                    raise TargetTimeout(
                        f"no agent speech turn completed within {timeout_s}s "
                        f"(conversation_id={self.conversation_id}, "
                        f"audio_frames={self.audio_frames_received}, "
                        f"carrier_peak_max={self.detector.carrier_peak_max}). "
                        "If carrier_peak_max is near the speech floor the agent's "
                        "background_sound level has drifted — see LEVEL1_SPEC §9.4."
                    ) from None

        resp = self._pair_agent_response(turn)
        text = resp["text"] if resp else ""
        provenance = "agent_emitted"
        if not resp:
            # NEVER observed (agent_response landed on 4/4, 3/3, 2/2 turns across
            # every capture) but §2.1 requires it be handled: fall back to the
            # Saaras cross-check and mark the turn ASR-derived, which makes
            # judge/checks.py degrade any violation on it to `review` (§3.3).
            provenance = "asr"
            self.warnings.append(
                f"agent_response missing for agent turn {self._turn_idx}; "
                "text falls back to ASR and its provenance is degraded"
            )

        audio_path = self._save_audio(turn.pcm, self._turn_idx, "agent")

        cross: dict[str, Any] | None = None
        if self.stt_cross_check or provenance == "asr":
            cross = await self._transcribe(turn.pcm)
            if provenance == "asr":
                text = (cross or {}).get("text") or ""

        started = self._ts_for(turn.start_s)
        ended = self._ts_for(turn.end_s)
        base = self._last_outbound_at if self._last_outbound_at is not None else turn.start_s
        latency_ms = max(0, int((turn.start_s - base) * 1000))

        meta = {
            "text_provenance": provenance,
            "audio_path": audio_path,
            "speech_started_ts": started,
            "speech_ended_ts": ended,
            "speech_s": turn.speech_s,
            "speech_frames": turn.speech_frames,
            "peak": turn.peak,                    # §9.4 drift tripwire, per turn
            "turn_end_reason": turn.reason,
            "carrier_peak_max": self.detector.carrier_peak_max,
        }
        if cross is not None:
            meta["asr_cross_check"] = cross

        self._first_turn_received = True
        self._turn_idx += 1
        self.agent_turns += 1
        self.agent_characters += len(text)
        return AudioAgentTurn(
            text=text,
            event_id=int(turn.event_id) if isinstance(turn.event_id, (int, float)) else 0,
            latency_ms=latency_ms,
            raw=(resp or {}).get("raw", {}),
            parts=1,
            audio_meta=meta,
        )

    def _pair_agent_response(self, turn: DetectedTurn) -> dict[str, Any] | None:
        """Match a detected speech turn to the `agent_response` that announced it.

        PRIMARY KEY: `event_id`. The audio frame that OPENS a turn carries the
        same `event_id` as that turn's `agent_response` — 100% across every voice
        capture on disk (1/1, 40/40, 96/96; 1/41/104/178; 1/38). `event_id` is a
        global counter shared with pings, so it never resets and cannot collide
        inside one conversation.

        FALLBACK: the first unconsumed response that arrived at or after the turn
        opened (it lands 0.31-0.83 s in, so it is always already here), else the
        first unconsumed one at all.
        """
        for i, r in enumerate(self._agent_responses):
            if i in self._consumed_responses:
                continue
            if turn.event_id is not None and r.get("event_id") == turn.event_id:
                self._consumed_responses.add(i)
                return r
        for i, r in enumerate(self._agent_responses):
            if i in self._consumed_responses:
                continue
            if r["el"] >= turn.start_s - 0.05:
                self._consumed_responses.add(i)
                return r
        for i, r in enumerate(self._agent_responses):
            if i not in self._consumed_responses:
                self._consumed_responses.add(i)
                return r
        return None

    # ── speaking ─────────────────────────────────────────────────────────────

    async def send_persona_turn(
        self,
        text: str,
        voice: dict[str, str] | None = None,
    ) -> PersonaTurnResult:
        """Synthesise the persona's line, stream it, hold the mic. One operation.

        TTS runs on a WORKER THREAD (`asyncio.to_thread`) so the reader keeps
        draining and ponging throughout — that is the whole point of §0.1.

        Returns the §3.2 persona-turn `meta` block. `turns[].text` stays the
        persona's INTENDED line (provenance `persona_intended`), which is what
        Level 0 recorded, so judge behaviour is continuous across levels.
        `meta.tara_heard` is what Tara's ASR made of it — a first-class product
        finding, and NEVER an input to a deterministic check (§2.2).
        """
        self._require_live("send_persona_turn")
        if not self._first_turn_received:
            raise TargetProtocolError(
                "send_persona_turn() before the first recv_agent_turn(). The agent "
                "speaks first, unprompted; speaking first deadlocks turn one."
            )
        combo = dict(voice or self.voice)

        t0 = time.monotonic()
        pcm, tts_meta = await self._synthesise(text, combo)
        if tts_meta.get("synth_ms") is None:
            tts_meta["synth_ms"] = int((time.monotonic() - t0) * 1000)
        if not pcm:
            raise TargetError(f"TTS returned no audio for {len(text)} characters")

        audio_path = self._save_audio(pcm, self._turn_idx, "persona")
        stream = await self.speak_and_hold(pcm)

        heard = self.user_transcripts[-1] if self.user_transcripts else None
        heard_meta = None
        if heard is not None:
            intended_len = max(1, len(text))
            heard_meta = {
                "text": heard["text"],
                "event_id": heard["event_id"],
                "provenance": "asr",
                # HEURISTIC, and labelled one on purpose. Turn 3 of the spike lost
                # 56% of the utterance including a cancellation threat with zero
                # error surface; this makes that loud in the report without
                # pretending to be a measurement.
                "truncation_suspect": len(heard["text"]) < 0.6 * intended_len,
            }

        self.user_characters += len(text)
        self._turn_idx += 1
        meta: dict[str, Any] = {
            "text_provenance": "persona_intended",
            "audio_path": audio_path,
            "tts": tts_meta,
            "playout_s": round(len(pcm) / BYTES_PER_SAMPLE / SAMPLE_RATE, 2),
            **stream,
        }
        if heard_meta is not None:
            meta["tara_heard"] = heard_meta
        return PersonaTurnResult(text=text, meta=meta)

    async def speak_and_hold(self, pcm: bytes) -> dict[str, Any]:
        """Stream raw pcm_16000 in real time, then HOLD THE MIC until endpointed.

        PHASE A — the utterance, paced against an ABSOLUTE monotonic clock (§4.2).
        Never `sleep(chunk_duration)` in a loop: the per-iteration cost (json
        encode + ws.send + scheduler) is 0.5-1 ms and ACCUMULATES, so over a
        ~170-chunk turn it is a systematic 100-200 ms stretch of the utterance
        that desynchronises `scribe_realtime`'s turn model WITH NO ERROR MESSAGE.
        Measured with the absolute form: 0.000 s drift on 7 of 8 utterances
        (0.051 s once, cold start).

        PHASE B — the mic hold, on THE SAME CLOCK: the chunk index keeps
        incrementing into zero-filled chunks until the reader signals
        `user_transcript`, then stops IMMEDIATELY.
          * Stopping dead instead works for exactly ONE turn per conversation and
            then deadlocks silently forever (two conversations burned proving it).
          * Holding open-endedly is the opposite failure: empty user turns on the
            `turn_timeout=10 s` cadence -> "Are you still there?" -> `end_call` ->
            close at 59 s. That arm died FASTER than the problem it addressed.
          * So the hold is bounded at `mic_hold_bound_s` (8.0 s) from the last
            real audio chunk, and expiry is the HARD ERROR `no_user_transcript`.

        `tail_ms` (§4.1's 1.5 s of trailing silence) is the first
        `tail_ms / CHUNK_MS` chunks of phase B — identical bytes on the wire, one
        clock, and it degrades to exactly the measured-working spike behaviour if
        the transcript arrives inside it.

        The stop event is checked every chunk so a server close mid-utterance
        breaks out instead of raising ConnectionClosed once per chunk.
        """
        n_before = len(self.user_transcripts)
        lead = b"\x00" * (CHUNK_BYTES * (self.lead_ms // CHUNK_MS))
        stream = lead + pcm
        n = (len(stream) + CHUNK_BYTES - 1) // CHUNK_BYTES

        t_start = time.monotonic()
        max_drift = 0.0
        sent = 0
        for i in range(n):
            if self._stop.is_set():
                break
            piece = stream[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]
            await self._send_audio_chunk(piece)
            sent += 1
            # ABSOLUTE clock: target is derived from t_start, NEVER accumulated.
            target = t_start + (i + 1) * (CHUNK_MS / 1000.0)
            max_drift = max(max_drift, time.monotonic() - target)
            await asyncio.sleep(max(0.0, target - time.monotonic()))

        utterance_wall_s = time.monotonic() - t_start
        last_real_chunk_at = time.monotonic()

        quiet = b"\x00" * CHUNK_BYTES
        hold_chunks = 0
        i = n
        bound_expired = False
        while True:
            if len(self.user_transcripts) > n_before:
                break                                    # endpointed: stop IMMEDIATELY
            if self._stop.is_set():
                break                                    # socket gone; not our failure
            if (time.monotonic() - last_real_chunk_at) >= self.mic_hold_bound_s:
                bound_expired = True
                break
            await self._send_audio_chunk(quiet)
            hold_chunks += 1
            i += 1
            target = t_start + (i + 1) * (CHUNK_MS / 1000.0)   # SAME clock
            await asyncio.sleep(max(0.0, target - time.monotonic()))

        endpointed = len(self.user_transcripts) > n_before
        hold_s = time.monotonic() - last_real_chunk_at
        if bound_expired and not endpointed:
            self._log("meta", {"event": "no_user_transcript",
                               "hold_s": round(hold_s, 3),
                               "hold_chunks": hold_chunks,
                               "bound_s": self.mic_hold_bound_s})
            raise MicHoldTimeout(
                f"no user_transcript within {self.mic_hold_bound_s}s of the last real "
                f"audio chunk ({hold_chunks} silence chunks sent). This is the turn-2 "
                "deadlock (LEVEL1_SPEC §0.3/§9.1) — a BUG, never 'a slow turn'. Do not "
                "raise the bound: past ~10 s the empty-turn endpointing hangs up the call."
            )

        drift_s = round(utterance_wall_s - (n * CHUNK_MS / 1000.0), 4)
        self._log("meta", {"event": "spoke", "chunks": sent, "hold_chunks": hold_chunks,
                           "utterance_wall_s": round(utterance_wall_s, 3),
                           "hold_s": round(hold_s, 3), "endpointed": endpointed,
                           "pacing_drift_s": drift_s})
        return {
            "chunks_sent": sent,
            "hold_chunks": hold_chunks,
            "utterance_wall_s": round(utterance_wall_s, 3),
            "mic_hold_s": round(hold_s, 3),
            "pacing_drift_s": drift_s,
            "max_instant_drift_s": round(max_drift, 4),
            "endpointed_while_mic_open": endpointed,
        }

    async def send_user_turn(self, text: str) -> None:
        """DEBUG ESCAPE HATCH — UNTESTED IN VOICE MODE. Not part of the loop.

        `user_message` text frames reportedly still work in voice mode, but our
        flow never sends one and we have never probed it (§9.12). Reaching for
        this as a shortcut skips TTS, skips the mic hold, and produces a turn
        `scribe_realtime` never saw. Use `send_persona_turn()`.
        """
        self._require_live("send_user_turn")
        self.warnings.append("send_user_turn() used in audio mode — untested path (§9.12)")
        await self._send({"type": "user_message", "text": text})
        self.user_characters += len(text)

    # ── speech services (speech/sarvam_speech.py) ────────────────────────────
    #
    # Both clients are natively async on one httpx.AsyncClient, so they yield to
    # the event loop while the request is in flight and the reader keeps draining
    # and ponging throughout. That is finding §0.1 satisfied for free — no
    # asyncio.to_thread, no worker pool, nothing that can own the socket.
    # `_await()` also tolerates a synchronous stub, which is how the offline
    # smoke test injects a fake without importing httpx.

    @staticmethod
    async def _await(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    async def _synthesise(self, text: str, combo: dict[str, str]) -> tuple[bytes, dict[str, Any]]:
        """Persona line -> headerless pcm_16000, ready for `user_audio_chunk`."""
        tts = self._ensure_tts()
        want_model = combo.get("model")
        if want_model and want_model != self.voice.get("model"):
            # §9.6: ship with ONE fixed voice per persona. A per-turn-varying voice
            # was never run as a full conversation, and the model is bound into the
            # client's config, so swapping it mid-conversation is not supported.
            raise TargetError(
                f"per-turn model override {want_model!r} != the target's {self.voice.get('model')!r}. "
                "One fixed voice per conversation (LEVEL1_SPEC §9.6)."
            )
        res = await self._await(tts.synthesize(
            text,
            speaker=combo.get("speaker"),
            sample_rate=SAMPLE_RATE,
            language_code=combo.get("language_code"),
        ))
        pcm = getattr(res, "pcm", res)
        rate = getattr(res, "sample_rate", SAMPLE_RATE)
        if rate != SAMPLE_RATE:
            # Bulbul's default is 22050. Tara then plays us at the wrong speed and
            # NOTHING errors anywhere — the only symptom is a garbled transcript.
            raise TargetError(
                f"TTS returned {rate} Hz, not {SAMPLE_RATE} Hz. Tara's socket is "
                "pcm_16000 and wrong-speed audio fails silently (LEVEL1_SPEC §6)."
            )
        meta = {
            "model": getattr(res, "model", self.voice.get("model")),
            "speaker": getattr(res, "speaker", combo.get("speaker") or self.voice.get("speaker")),
            "synth_ms": getattr(res, "latency_ms", None),
            "chars": getattr(res, "chars", len(text)),
            "sample_rate": rate,
        }
        return (pcm if isinstance(pcm, bytes) else bytes(pcm)), meta

    def _ensure_tts(self) -> Any:
        if self._tts is None:
            try:
                from speech.sarvam_speech import BulbulTTS, TTSConfig   # noqa: PLC0415
            except ImportError as e:                                     # pragma: no cover
                raise TargetError(
                    "speech.sarvam_speech.BulbulTTS is unavailable and no `tts=` was "
                    f"injected: {e}"
                ) from e
            if not self._sarvam_api_key:
                raise TargetError("sarvam_api_key is required to synthesise persona turns")
            cfg_kw: dict[str, Any] = {"sample_rate": SAMPLE_RATE}
            for key, field_name in (("model", "model"), ("speaker", "speaker"),
                                    ("pace", "pace"),
                                    ("language_code", "target_language_code")):
                if self.voice.get(key) is not None:
                    cfg_kw[field_name] = self.voice[key]
            self._tts = BulbulTTS(self._sarvam_api_key, TTSConfig(**cfg_kw))
            self._owns_tts = True
        return self._tts

    async def _transcribe(self, pcm: bytes) -> dict[str, Any] | None:
        """Saaras cross-check. CROSS-CHECK ONLY — never scored, never a violation.

        It answers "what would a human listener have heard?", which is a fidelity
        finding about Tara's TTS, not about her claims. It measurably injects
        errors ("I hear you on the price" -> "I hear you want the price",
        "Aravinth" -> "Arvind"), and a mis-heard "10%" as "50%" would mint a false
        hallucination violation with the full weight of a deterministic fact.
        A failure here is a warning, never a dead conversation.
        """
        try:
            stt = self._ensure_stt()
        except TargetError as e:
            self.warnings.append(f"stt_cross_check unavailable: {e}")
            return None
        try:
            res = await self._await(stt.transcribe(pcm, sample_rate=SAMPLE_RATE))
        except Exception as e:                                        # noqa: BLE001
            self.warnings.append(f"stt_cross_check failed: {type(e).__name__}: {e}")
            return None
        return {
            "text": getattr(res, "text", res),
            "model": getattr(res, "model", "saarika:v2.5"),
            "language_code": getattr(res, "language_code", None),
            "latency_ms": getattr(res, "latency_ms", None),
        }

    def _ensure_stt(self) -> Any:
        if self._stt is None:
            try:
                from speech.sarvam_speech import SaarasSTT    # noqa: PLC0415
            except ImportError as e:                           # pragma: no cover
                raise TargetError(f"speech.sarvam_speech.SaarasSTT is unavailable: {e}") from e
            if not self._sarvam_api_key:
                raise TargetError("sarvam_api_key is required for the STT cross-check")
            self._stt = SaarasSTT(self._sarvam_api_key)
            self._owns_stt = True
        return self._stt

    async def _close_speech_clients(self) -> None:
        """Only clients WE built are closed — an injected one belongs to the caller."""
        for owned, client in ((self._owns_tts, self._tts), (self._owns_stt, self._stt)):
            if owned and client is not None and hasattr(client, "aclose"):
                try:
                    await client.aclose()
                except Exception:
                    pass

    # ── internals ────────────────────────────────────────────────────────────

    def _el(self) -> float:
        return time.monotonic() - self._t0

    def _ts_for(self, el: float) -> str:
        """Detector-clock elapsed seconds -> ISO-8601 UTC wall time."""
        wall = time.time() - (self._el() - el)
        return (datetime.fromtimestamp(wall, tz=timezone.utc)
                .isoformat(timespec="milliseconds").replace("+00:00", "Z"))

    def _require_live(self, what: str) -> None:
        if not self._opened:
            raise TargetProtocolError(f"{what}() before open()")
        if self._closed or self._ws is None:
            raise TargetClosed(f"{what}() after the socket was closed")

    def _init_frame(self, scenario_vars: dict[str, str]) -> dict:
        """The one and only client frame sent before anything else.

        VOICE MODE: NO `conversation_config_override` AT ALL — `text_only` is
        omitted entirely, not sent as False. `dynamic_variables` is a TOP-LEVEL
        SIBLING; nesting it silently yields unrendered {{placeholders}} in Tara's
        opening. All values are strings ("1499", not 1499).
        """
        dyn = {k: str(v) for k, v in (scenario_vars or {}).items()}
        missing = [k for k in SCENARIO_VAR_KEYS if k not in dyn]
        if missing:
            raise TargetProtocolError(
                f"scenario_vars is missing declared dynamic variables: {missing}. "
                "Unsent placeholders render as literal {{braces}} in Tara's opening."
            )
        return {"type": "conversation_initiation_client_data", "dynamic_variables": dyn}

    async def _send(self, frame: dict) -> None:
        async with self._send_lock:
            ws = self._ws
            if ws is None:
                raise TargetClosed("send after the socket was closed")
            try:
                await ws.send(json.dumps(frame, ensure_ascii=False))
            except ConnectionClosed as e:
                self._stop.set()
                raise TargetClosed(f"socket closed while sending {frame.get('type')}: {e}",
                                   close_code=_close_code(e)) from e
            except (WebSocketException, OSError) as e:
                self._stop.set()
                raise TargetClosed(
                    f"send failed for {frame.get('type')}: {type(e).__name__}: {e}") from e
        self._log("out", frame)
        if frame.get("type") == "conversation_initiation_client_data":
            self._last_outbound_at = self._el()

    async def _send_audio_chunk(self, pcm: bytes) -> None:
        """`{"user_audio_chunk": "<b64>"}` — a BARE TOP-LEVEL KEY.

        There is NO `"type"` field. Wrapping it in one gets the frame SILENTLY
        IGNORED — measured. 3200 bytes == 100 ms of pcm_16000.
        """
        async with self._send_lock:
            ws = self._ws
            if ws is None:
                self._stop.set()
                return
            try:
                await ws.send(json.dumps({"user_audio_chunk": base64.b64encode(pcm).decode()}))
            except ConnectionClosed:
                self._stop.set()
                return
            except (WebSocketException, OSError):
                self._stop.set()
                return
        self.audio_chunks_sent += 1
        self._last_outbound_at = self._el()
        self._log("out", {"user_audio_chunk": f"<{len(pcm)} B pcm16>"})

    def _save_audio(self, pcm: bytes, idx: int, who: str) -> str | None:
        """runs/<run_id>/audio/<persona_id>/turn_<idx>_{agent,persona}.pcm"""
        if self.audio_dir is None or not pcm:
            return None
        try:
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            path = self.audio_dir / f"turn_{idx}_{who}.pcm"
            path.write_bytes(pcm)
            return str(path)
        except OSError as e:
            self.warnings.append(f"could not write turn audio: {e}")
            return None

    def _count_unknown(self, ftype: str) -> None:
        self.unknown_events[ftype] = self.unknown_events.get(ftype, 0) + 1

    # ── raw event log ────────────────────────────────────────────────────────

    def _open_raw_log(self) -> None:
        if self.raw_log_path is None:
            return
        self.raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = self.raw_log_path.open("a", encoding="utf-8")

    def _log(self, direction: str, payload: dict) -> None:
        """One JSON object per line. No API key, no audio payload, ever.

        `el` is included alongside `t` so a production log replays through
        `TurnDetector` unmodified — the detector's clock and this file's clock
        are the same clock.
        """
        if self._log_fh is None:
            return
        try:
            line = json.dumps({"t": round(time.time(), 3), "el": round(self._el(), 3),
                               "dir": direction, "payload": payload}, ensure_ascii=False)
            self._log_fh.write(line + "\n")
            self._log_fh.flush()
        except Exception:
            # Logging must never break a live conversation.
            pass


def _close_code(exc: ConnectionClosed) -> int | None:
    """The WebSocket close code the peer sent, if any.

    1000 after an `end_call` is the AGENT HANGING UP; 1002 is the pong-rule kill;
    anything else is a dropped socket. Only the code tells them apart, so it is
    carried on the exception rather than buried in a formatted string.
    """
    for frame in (getattr(exc, "rcvd", None), getattr(exc, "sent", None)):
        code = getattr(frame, "code", None)
        if isinstance(code, int):
            return code
    return None


def fetch_signed_url(api_key: str, agent_id: str) -> str:      # pragma: no cover
    """UNSUPPORTED IN AUDIO MODE (§9.9) — kept so the text target's helper is not
    silently duplicated somewhere worse. `__init__` rejects auth='signed'."""
    req = urllib.request.Request(f"{SIGNED_URL_ENDPOINT}?agent_id={agent_id}",
                                 headers={"xi-api-key": api_key})
    with urllib.request.urlopen(req, timeout=OPEN_TIMEOUT_S) as r:
        return json.loads(r.read().decode())["signed_url"]


__all__ = [
    "ElevenLabsAudioTarget",
    "TurnDetector",
    "DetectedTurn",
    "AudioAgentTurn",
    "PersonaTurnResult",
    "MicHoldTimeout",
    "peak_amplitude",
    "AUDIO_FORMAT",
    "CHUNK_BYTES",
    "CHUNK_MS",
    "SAMPLE_RATE",
    "SPEECH_PEAK_MIN",
    "QUIET_FRAMES",
    "QUIET_WALL_S",
    "MIC_HOLD_BOUND_S",
    "OPENING_TIMEOUT_S",
    "TURN_TIMEOUT_S",
    "AgentTurn",
    "TargetError",
    "TargetTimeout",
    "TargetClosed",
    "TargetProtocolError",
]
