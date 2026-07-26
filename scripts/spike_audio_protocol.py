#!/usr/bin/env python3
"""
spike_audio_protocol.py — LEVEL 1 PROTOCOL MAP.

Maps the ElevenLabs VOICE-mode WebSocket protocol against the live agent
(jiohotstar-tara-winback-recovery) precisely enough that the audio adapter can be
written from the notes without re-running anything.

Answers, each from a real capture:
  1. asr.user_input_audio_format / tts.agent_output_audio_format, and whether
     platform_settings lets us override them.
  2. THE 60-SECOND QUESTION. In TEXT mode the server closes with 1002
     ("No user message received for 60 seconds") if nothing arrives from us.
     Does streaming `user_audio_chunk` reset that clock?
     ANSWER (26 Jul 2026, all six arms below run live): NO — and the premise is
     wrong. What keeps the socket alive is PONGING, not user input. Every arm
     that kept ponging survived 100+ s of total user silence, in BOTH modes.
     Every arm that stopped ponging died, in BOTH modes. Streaming silence
     chunks was actively HARMFUL: it endpoints as an empty user turn every
     ~turn_timeout (10 s), Tara nudges twice, then calls the `end_call` system
     tool and the server closes 1000 at 59 s.
  3. Does agent_response TEXT still arrive in voice mode?
  4. Does user_transcript come back, showing what Tara's ASR heard?
  5. What is the definitive end-of-agent-turn signal?
  6. Full event inventory + which frames carry audio, at what amplitude.

NEVER modifies the agent. NEVER touches simulate-conversation. Read-only GET only.

Run one phase at a time — each is ONE short live conversation and costs real money.
The six arms actually run, and what each one showed:

  --phase config                              read-only GET: formats, turn cfg, quota
  --phase silent                    VOICE, never speak, pong    -> ALIVE at 102.0 s
  --phase silent_text               TEXT,  never speak, pong    -> ALIVE at 101.4 s
  --phase text_turn_silence --after silence
                                    TEXT,  1 user_message, pong -> ALIVE at 102.6 s
  --phase text_turn_silence --after nopong
                                    TEXT,  1 user_message, NO pong -> DEAD
  --phase turn --after silence      VOICE, 1 real spoken turn, pong, then silent
                                                               -> ALIVE at 133.7 s
                                                                  (112 s idle)
  --phase turn --after chunks       VOICE, 1 real spoken turn, then stream silence
                                                               -> DEAD 59.1 s, 1000,
                                                                  agent end_call
  --phase turn --after nopong       VOICE, 1 real spoken turn, NO pong -> DEAD

    PYTHONPATH=. uv run --python 3.12 python scripts/spike_audio_protocol.py --phase turn --after silence
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import struct
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "runs" / "_spike_audio"
OUT_DIR.mkdir(parents=True, exist_ok=True)

API_HOST = "api.elevenlabs.io"
AGENT_URL = f"https://{API_HOST}/v1/convai/agents/{{agent_id}}"
WS_URL = f"wss://{API_HOST}/v1/convai/conversation"
SARVAM_TTS = "https://api.sarvam.ai/text-to-speech"

# Verified from the live agent config (phase `config`): both directions are
# pcm_16000 == raw signed 16-bit little-endian mono PCM at 16 kHz, no container.
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_MS // 1000   # 3200

# MEASURED (phase `silent`, 26 Jul 2026): the server streams a CONTINUOUS audio
# carrier — one 9600-byte frame every ~300 ms, forever, whether or not anyone is
# speaking. It is the agent's `background_sound: office1 @ 0.08` preset. Speech
# frames peak 19-50% of full scale; carrier-only frames peak 0.7-4.1%. So the end
# of an agent turn is an AMPLITUDE floor, never an absence of frames.
#
# CALIBRATION over all 8 captured agent turns:
#   min per-turn speech peak        3266  (10.0% FS)
#   max carrier-only peak           2942  (9.0% FS, a single frame; usually <=2044)
#   longest sub-threshold run INSIDE a turn   exactly 1 frame, every turn, no exceptions
#   longest wall gap between speech frames inside a turn      0.918 s
# So 3000 separates them, but only just — pair it with a multi-frame hold, and
# prefer >=5 frames (~1.5 s). 0.9 s was measured too tight: it split one real turn.
SPEECH_PEAK = 3000            # ~9.2% FS
TURN_END_QUIET_S = 0.9        # PROBE VALUE. Production should use 1.5 (see above).

DYNAMIC_VARIABLES = {
    "subscriber_name": "Aravinth",
    "call_reason": "win_back",
    "call_intro": (
        "I'm calling because your JioHotstar Super annual plan lapsed on 20 June "
        "and I'd love to get you back before the cricket ends."
    ),
    "plan_name": "JioHotstar Super (annual)",
    "amount_inr": "1499",
    "expiry_date": "20 June",
    "content_hook": "the ICC Women's T20 World Cup, live through 5 July",
    "offer_text": "10% off if you reactivate before 20 June",
    "renewal_date": "",
    "next_retry_date": "",
    "failure_reason": "",
}

PROBE_UTTERANCE = "English please. Fourteen ninety nine is too much yaar, kuch discount milega kya?"


# ── env / http ───────────────────────────────────────────────────────────────


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def http_json(url: str, headers: dict, data: bytes | None = None, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ── audio helpers (stdlib only — no numpy at Level 0/1 probe stage) ───────────


def peak_amplitude(raw: bytes) -> int:
    n = len(raw) // 2
    if not n:
        return 0
    return max(abs(s) for s in struct.unpack(f"<{n}h", raw[: n * 2]))


def silence_chunk() -> str:
    return base64.b64encode(b"\x00" * CHUNK_BYTES).decode()


def sarvam_tts_pcm16k(api_key: str, text: str) -> bytes:
    """Bulbul v2 -> WAV(PCM16 mono 16k) -> raw PCM body. VERIFIED 26 Jul 2026."""
    body = json.dumps({
        "text": text,
        "target_language_code": "en-IN",
        "speaker": "anushka",
        "model": "bulbul:v2",
        "speech_sample_rate": SAMPLE_RATE,
    }).encode()
    d = http_json(SARVAM_TTS, {"api-subscription-key": api_key,
                               "Content-Type": "application/json"}, data=body)
    wav = base64.b64decode(d["audios"][0])
    # Walk RIFF chunks rather than assuming a 44-byte header.
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", "not a RIFF/WAVE payload"
    pos = 12
    while pos + 8 <= len(wav):
        cid = wav[pos:pos + 4]
        size = int.from_bytes(wav[pos + 4:pos + 8], "little")
        if cid == b"data":
            return wav[pos + 8: pos + 8 + size]
        pos += 8 + size + (size & 1)
    raise AssertionError("no data chunk in WAV")


# ── the capture ──────────────────────────────────────────────────────────────


class Capture:
    """One live voice conversation, fully instrumented."""

    def __init__(self, phase: str, api_key: str, agent_id: str, text_only: bool = False):
        self.phase = phase
        self.text_only = text_only
        self.last_speech_at: float | None = None   # monotonic, last frame above SPEECH_PEAK
        self.speech_spans: list[dict] = []         # per event_id speech start/end
        self.api_key = api_key
        self.agent_id = agent_id
        self.t0 = 0.0
        self.ws = None
        self.log_path = OUT_DIR / f"events_{phase}.jsonl"
        self.fh = self.log_path.open("w", encoding="utf-8")
        self.events: Counter[str] = Counter()
        self.timeline: list[dict] = []          # compact, ordered, for the report
        self.audio_by_event: dict[int, list[dict]] = defaultdict(list)
        self.agent_responses: list[dict] = []
        self.user_transcripts: list[dict] = []
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.closed_at: float | None = None
        self.last_user_frame_at: float | None = None
        self.pings = 0
        self.stop = asyncio.Event()
        #: When set, the receiver stops reading and therefore stops PONGING —
        #: exactly the state runner/loop.py is in while it awaits a slow Sarvam turn.
        self.paused = False

    # -- plumbing --

    def el(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def rec(self, direction: str, payload: dict, note: dict | None = None) -> None:
        row = {"el": self.el(), "dir": direction, "payload": payload}
        if note:
            row["note"] = note
        self.fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.fh.flush()

    def mark(self, kind: str, **kw) -> None:
        row = {"el": self.el(), "kind": kind, **kw}
        self.timeline.append(row)
        print(f"  [{row['el']:>7.3f}s] {kind}"
              + ("  " + json.dumps(kw, ensure_ascii=False) if kw else ""))

    async def send(self, frame: dict, *, is_user_input: bool = False) -> None:
        await self.ws.send(json.dumps(frame, ensure_ascii=False))
        if is_user_input:
            self.last_user_frame_at = time.monotonic()
        if frame.get("user_audio_chunk") is not None:
            self.rec("out", {"user_audio_chunk": f"<{CHUNK_BYTES} bytes pcm16>"})
        else:
            self.rec("out", frame)

    # -- lifecycle --

    async def open(self) -> str:
        self.t0 = time.monotonic()
        self.ws = await websockets.connect(
            f"{WS_URL}?agent_id={self.agent_id}",
            additional_headers={"xi-api-key": self.api_key},
            ping_interval=None,
            max_size=16 * 1024 * 1024,
            open_timeout=30,
        )
        self.mark("socket_open")
        # VOICE MODE: no text_only override. dynamic_variables stays TOP-LEVEL.
        init: dict = {"type": "conversation_initiation_client_data",
                      "dynamic_variables": DYNAMIC_VARIABLES}
        if self.text_only:
            init["conversation_config_override"] = {"conversation": {"text_only": True}}
        await self.send(init)
        self.mark("init_sent", text_only=self.text_only)
        return ""

    async def receiver(self) -> None:
        """Single reader. Pongs inline. Never sends user input."""
        try:
            while True:
                if self.paused:
                    await asyncio.sleep(0.05)
                    continue
                raw = await self.ws.recv()
                if isinstance(raw, (bytes, bytearray)):
                    self.events["<binary_frame>"] += 1
                    self.rec("in", {"type": "<binary_frame>", "bytes": len(raw)})
                    self.mark("binary_frame", bytes=len(raw))
                    continue
                msg = json.loads(raw)
                await self.on_frame(msg)
        except websockets.exceptions.ConnectionClosed as e:
            self.closed_at = self.el()
            rcvd = getattr(e, "rcvd", None)
            self.close_code = getattr(rcvd, "code", None)
            self.close_reason = getattr(rcvd, "reason", None)
            self.mark("SOCKET_CLOSED", code=self.close_code, reason=self.close_reason)
            self.rec("meta", {"event": "closed", "code": self.close_code,
                              "reason": self.close_reason})
        finally:
            self.stop.set()

    async def on_frame(self, msg: dict) -> None:
        t = msg.get("type") or "<no_type>"
        self.events[t] += 1

        if t == "audio":
            ev = msg.get("audio_event") or {}
            b64 = ev.get("audio_base_64") or ""
            raw = base64.b64decode(b64) if b64 else b""
            eid = ev.get("event_id")
            pk = peak_amplitude(raw)
            self.audio_by_event[eid].append({"el": self.el(), "bytes": len(raw), "peak": pk})
            if pk >= SPEECH_PEAK:
                now = time.monotonic()
                if (self.last_speech_at is None
                        or now - self.last_speech_at > TURN_END_QUIET_S
                        or not self.speech_spans
                        or self.speech_spans[-1]["event_id"] != eid):
                    self.speech_spans.append({"event_id": eid, "start_el": self.el(),
                                              "end_el": self.el()})
                    self.mark("SPEECH_START", event_id=eid, peak_pct=round(pk / 32768 * 100, 1))
                else:
                    self.speech_spans[-1]["end_el"] = self.el()
                self.last_speech_at = now
            self.rec("in", {**msg, "audio_event": {**ev,
                     "audio_base_64": f"<{len(raw)} bytes, peak={pk}>"}})
            return

        if t == "ping":
            ev = msg.get("ping_event") or {}
            self.pings += 1
            self.rec("in", msg)
            # Pong IMMEDIATELY. Never sleep on ping_ms. NOT counted as user input.
            await self.send({"type": "pong", "event_id": ev.get("event_id")})
            return

        self.rec("in", msg)

        if t == "conversation_initiation_metadata":
            ev = msg.get("conversation_initiation_metadata_event") or {}
            self.mark("metadata", **{k: v for k, v in ev.items() if k != "agent_output_audio_format"
                                     or True})
        elif t == "agent_response":
            ev = msg.get("agent_response_event") or {}
            self.agent_responses.append({"el": self.el(), **ev})
            self.mark("agent_response", event_id=ev.get("event_id"),
                      text=(ev.get("agent_response") or "")[:120])
        elif t == "user_transcript":
            ev = msg.get("user_transcription_event") or {}
            self.user_transcripts.append({"el": self.el(), **ev})
            self.mark("USER_TRANSCRIPT", **ev)
        elif t == "agent_chat_response_part":
            pass  # too chatty for the timeline; it is in the jsonl
        else:
            self.mark(t, keys=list(msg.keys()))

    async def close(self) -> None:
        try:
            self.fh.flush()
            self.fh.close()
        except Exception:
            pass
        try:
            if self.ws is not None:
                await self.ws.close()
        except Exception:
            pass

    # -- analysis --

    def audio_summary(self) -> dict:
        out = {}
        for eid, frames in sorted(self.audio_by_event.items(),
                                  key=lambda kv: (kv[0] is None, kv[0])):
            tot = sum(f["bytes"] for f in frames)
            out[str(eid)] = {
                "frames": len(frames),
                "bytes": tot,
                "seconds_of_pcm16k": round(tot / 2 / SAMPLE_RATE, 2),
                "peak": max(f["peak"] for f in frames),
                "peak_pct_full_scale": round(max(f["peak"] for f in frames) / 32768 * 100, 2),
                "distinct_frame_sizes": sorted({f["bytes"] for f in frames})[:6],
                "first_el": frames[0]["el"],
                "last_el": frames[-1]["el"],
            }
        return out

    def result(self) -> dict:
        return {
            "phase": self.phase,
            "text_only": self.text_only,
            "event_types": dict(self.events),
            "speech_spans": self.speech_spans,
            "user_turn_done_at_s": getattr(self, "user_turn_done_at", None),
            "pings_ponged": self.pings,
            "closed_at_s": self.closed_at,
            "close_code": self.close_code,
            "close_reason": self.close_reason,
            "agent_responses": self.agent_responses,
            "user_transcripts": self.user_transcripts,
            "audio_by_event_id": self.audio_summary(),
            "timeline": self.timeline,
        }


# ── phases ───────────────────────────────────────────────────────────────────


async def wait_for_opening(cap: Capture, timeout: float = 30.0) -> None:
    """Block until Tara's opening agent_response lands (she speaks first)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not cap.stop.is_set():
        if cap.agent_responses:
            return
        await asyncio.sleep(0.05)
    cap.mark("no_opening_agent_response_within", s=timeout)


async def wait_for_agent_turn_end(cap: Capture, max_wait: float = 40.0) -> float | None:
    """Block until the agent's audio drops below SPEECH_PEAK for TURN_END_QUIET_S.

    This IS the end-of-turn detector — the frames themselves never stop.
    Returns the elapsed time at which the turn ended, or None if it never started.
    """
    started = time.monotonic()
    while time.monotonic() - started < max_wait and not cap.stop.is_set():
        if (cap.last_speech_at is not None
                and time.monotonic() - cap.last_speech_at > TURN_END_QUIET_S):
            end_el = cap.speech_spans[-1]["end_el"] if cap.speech_spans else None
            cap.mark("AGENT_TURN_END", quiet_for_s=TURN_END_QUIET_S,
                     last_speech_el=end_el)
            return end_el
        await asyncio.sleep(0.1)
    cap.mark("agent_turn_end_not_detected", waited=round(time.monotonic() - started, 1))
    return None


async def hold_nopong(cap: Capture, hold_s: float) -> None:
    """THE MECHANISM ARM. Send nothing AND stop ponging for hold_s — the exact
    state runner/loop.py is in during a slow persona turn. Then try to speak and
    see whether the socket is still there."""
    cap.mark("PAUSING_RECEIVER_no_pongs_no_user_input", for_s=hold_s)
    cap.paused = True
    await asyncio.sleep(hold_s)
    cap.paused = False
    cap.mark("receiver_resumed_probing_socket")
    await asyncio.sleep(1.0)
    try:
        await cap.send({"type": "user_message", "text": "Are you still there?"},
                       is_user_input=True)
        cap.mark("post_pause_send_ok")
    except Exception as e:                                   # noqa: BLE001
        cap.mark("post_pause_send_failed", err=f"{type(e).__name__}: {e}"[:300])
    await asyncio.sleep(8.0)
    cap.mark("nopong_arm_done", socket_alive=not cap.stop.is_set())


async def hold_silent(cap: Capture, hold_s: float) -> None:
    """CONTROL arm: send NOTHING for hold_s. Pongs still go out (receiver task)."""
    cap.mark("GOING_SILENT", for_s=hold_s)
    deadline = time.monotonic() + hold_s
    while time.monotonic() < deadline and not cap.stop.is_set():
        await asyncio.sleep(0.25)
    cap.mark("hold_elapsed_socket_still_open" if not cap.stop.is_set()
             else "hold_ended_socket_dead")


async def hold_chunks(cap: Capture, hold_s: float) -> None:
    """EXPERIMENT arm: stream digital-silence user_audio_chunk frames continuously
    for hold_s, never completing an utterance. Survival past 60 s == the chunks
    reset the server's user-message clock."""
    cap.mark("STREAMING_SILENCE_CHUNKS", chunk_ms=CHUNK_MS, chunk_bytes=CHUNK_BYTES,
             for_s=hold_s)
    chunk = silence_chunk()
    sent = 0
    start = time.monotonic()
    next_at = start
    while time.monotonic() - start < hold_s and not cap.stop.is_set():
        try:
            await cap.send({"user_audio_chunk": chunk}, is_user_input=True)
        except websockets.exceptions.ConnectionClosed:
            cap.mark("send_failed_socket_closed", chunks_sent=sent)
            break
        sent += 1
        if sent % 100 == 0:
            cap.mark("chunks_sent", n=sent, elapsed=round(time.monotonic() - start, 1))
        next_at += CHUNK_MS / 1000
        await asyncio.sleep(max(0.0, next_at - time.monotonic()))
    cap.mark("streaming_done", chunks_sent=sent,
             socket_alive=not cap.stop.is_set(),
             audio_seconds_streamed=round(sent * CHUNK_MS / 1000, 1))


async def phase_text_turn(cap: Capture, hold_s: float, after: str = "silence") -> None:
    """REPLICATION of the known text-mode kill, and the control that makes the
    voice result mean anything: TEXT mode, send ONE user_message so the server's
    user-message clock is armed, then send nothing for hold_s."""
    await wait_for_opening(cap)
    await cap.send({"type": "user_message", "text": PROBE_UTTERANCE}, is_user_input=True)
    cap.mark("user_message_sent", text=PROBE_UTTERANCE)
    cap.user_turn_done_at = cap.el()
    n = len(cap.agent_responses)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not cap.stop.is_set():
        if len(cap.agent_responses) > n:
            break
        await asyncio.sleep(0.1)
    cap.mark("reply_received", n=len(cap.agent_responses))
    if after == "nopong":
        await hold_nopong(cap, hold_s)
    else:
        await hold_silent(cap, hold_s)


async def phase_turn(cap: Capture, sarvam_key: str, after: str, hold_s: float) -> None:
    """Speak ONE real utterance, capture the full end-to-end turn, then run the
    requested 60-s-timer arm (`none` | `silence` | `chunks`) AFTER a real user
    turn has been registered — which is the state the text-mode kill was seen in."""
    await wait_for_opening(cap)
    await wait_for_agent_turn_end(cap)          # do not interrupt her opening
    cap.mark("speaking")
    pcm = sarvam_tts_pcm16k(sarvam_key, PROBE_UTTERANCE)
    cap.mark("tts_ready", pcm_bytes=len(pcm),
             seconds=round(len(pcm) / 2 / SAMPLE_RATE, 2))
    # 300 ms lead-in silence, the utterance, then 1.5 s trailing silence so the
    # turn model sees an end-of-speech pause.
    stream = b"\x00" * (CHUNK_BYTES * 3) + pcm + b"\x00" * (CHUNK_BYTES * 15)
    start = time.monotonic()
    next_at = start
    for i in range(0, len(stream), CHUNK_BYTES):
        if cap.stop.is_set():
            break
        piece = stream[i:i + CHUNK_BYTES]
        await cap.send({"user_audio_chunk": base64.b64encode(piece).decode()},
                       is_user_input=True)
        next_at += CHUNK_MS / 1000
        await asyncio.sleep(max(0.0, next_at - time.monotonic()))
    cap.mark("utterance_streamed", wall_s=round(time.monotonic() - start, 2))
    cap.user_turn_done_at = cap.el()
    # Listen for the whole reply turn: user_transcript, agent_response, audio.
    n_resp = len(cap.agent_responses)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and not cap.stop.is_set():
        if len(cap.agent_responses) > n_resp:
            break
        await asyncio.sleep(0.1)
    await wait_for_agent_turn_end(cap)
    cap.mark("reply_turn_captured", socket_alive=not cap.stop.is_set())

    if after == "silence":
        await hold_silent(cap, hold_s)
    elif after == "chunks":
        await hold_chunks(cap, hold_s)
    elif after == "nopong":
        await hold_nopong(cap, hold_s)
    else:
        cap.mark("no_hold_arm_requested")


# ── config phase (read-only GET) ─────────────────────────────────────────────


def phase_config(env: dict) -> dict:
    key, aid = env["ELEVENLABS_API_KEY"], env["ELEVENLABS_AGENT_ID"]
    body = http_json(AGENT_URL.format(agent_id=aid), {"xi-api-key": key})
    cc = body.get("conversation_config") or {}
    ov = ((body.get("platform_settings") or {}).get("overrides") or {}) \
        .get("conversation_config_override") or {}
    out = {
        "agent_name": body.get("name"),
        "asr": cc.get("asr"),
        "tts": cc.get("tts"),
        "turn": cc.get("turn"),
        "vad": cc.get("vad"),
        "conversation": cc.get("conversation"),
        "overrides_conversation_config_override": ov,
        "audio_format_overridable": {
            "asr.user_input_audio_format": (ov.get("asr") or {}).get("user_input_audio_format", "ABSENT -> not overridable"),
            "tts.agent_output_audio_format": (ov.get("tts") or {}).get("agent_output_audio_format", "ABSENT -> not overridable"),
        },
    }
    quota = http_json(f"https://{API_HOST}/v1/user/subscription", {"xi-api-key": key})
    out["character_quota"] = {
        "used": quota.get("character_count"),
        "limit": quota.get("character_limit"),
        "tier": quota.get("tier"),
    }
    return out


# ── main ─────────────────────────────────────────────────────────────────────


async def run_live(phase: str, env: dict, hold_s: float, after: str, tag: str) -> dict:
    cap = Capture(tag, env["ELEVENLABS_API_KEY"], env["ELEVENLABS_AGENT_ID"],
                  text_only=phase in ("silent_text", "text_turn_silence"))
    await cap.open()
    rx = asyncio.create_task(cap.receiver())
    try:
        if phase in ("silent", "silent_text"):
            await wait_for_opening(cap)
            await hold_silent(cap, hold_s)
        elif phase == "text_turn_silence":
            await phase_text_turn(cap, hold_s, after)
        elif phase == "turn":
            await phase_turn(cap, env["SARVAM_API_KEY"], after, hold_s)
    finally:
        await cap.close()
        rx.cancel()
        try:
            await rx
        except (asyncio.CancelledError, Exception):
            pass
    return cap.result()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["config", "silent", "silent_text",
                             "text_turn_silence", "turn"])
    ap.add_argument("--after", default="none", choices=["none", "silence", "chunks", "nopong"],
                    help="phase=turn only: which 60s-timer arm to run after the turn")
    ap.add_argument("--hold", type=float, default=100.0,
                    help="seconds to hold the 60s-timer arms (default 100)")
    args = ap.parse_args()

    tag = args.phase if args.phase not in ("turn", "text_turn_silence") \
        else f"{args.phase}_{args.after}"
    env = load_env()
    if args.phase == "config":
        res = phase_config(env)
    else:
        res = asyncio.run(run_live(args.phase, env, args.hold, args.after, tag))

    path = OUT_DIR / f"result_{tag}.json"
    path.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 72)
    print(json.dumps({k: v for k, v in res.items() if k != "timeline"},
                     indent=2, ensure_ascii=False)[:6000])
    print(f"\nresult  -> {path}")
    if args.phase != "config":
        print(f"events  -> {OUT_DIR / f'events_{tag}.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
