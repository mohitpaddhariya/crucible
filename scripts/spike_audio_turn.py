#!/usr/bin/env python3
"""
spike_audio_turn.py — LEVEL 1 HARD GATE.

Prove (or disprove) that we can hold a HALF-DUPLEX AUDIO conversation with the
live ElevenLabs agent (jiohotstar-tara-winback-recovery):

    Tara speaks (pcm_16000)
      -> we detect end-of-turn by AMPLITUDE
      -> we transcribe her with Sarvam Saaras (saarika:v2.5)
      -> [ at real Level 1 the persona LLM thinks here — this spike hardcodes the line ]
      -> Sarvam Bulbul synthesises it at 16 kHz
      -> we strip the RIFF header and stream raw PCM back, paced at real time
      -> Tara's scribe_realtime transcribes us and she replies

NO Sarvam CHAT here. Persona lines are HARDCODED — this spike isolates the audio
path exactly as spike_text_mode.py isolated the socket.

ARCHITECTURE (the finding from spike_audio_protocol.py that dominates this file):
the server's "60 second" kill is a PONG rule, not a user-message rule. So the
socket reader is a permanently-live asyncio task from open() to close(). Every
compute step (STT, TTS, and at Level 1 the LLM) runs on a WORKER thread via
asyncio.to_thread while the reader keeps draining and ponging. No compute step
ever owns the socket.

NEVER touches /v1/convai/agents/{id}/simulate-conversation. The only ElevenLabs
REST call is a read-only GET on the agent.

Run:
    PYTHONPATH=. uv run --python 3.12 --with httpx --with websockets \
        python scripts/spike_audio_turn.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import struct
import sys
import time
import wave
from collections import Counter
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "runs" / "_spike_audio_turn"
OUT_DIR.mkdir(parents=True, exist_ok=True)

API_HOST = "api.elevenlabs.io"
WS_URL = f"wss://{API_HOST}/v1/convai/conversation"
SARVAM_HOST = "https://api.sarvam.ai"
SARVAM_TTS = f"{SARVAM_HOST}/text-to-speech"
SARVAM_TTS_WS = f"{SARVAM_HOST.replace('https', 'wss')}/text-to-speech/ws"
SARVAM_STT = f"{SARVAM_HOST}/speech-to-text"

# ── wire format: VERIFIED both directions on the live agent ──────────────────
# asr.user_input_audio_format  == pcm_16000
# tts.agent_output_audio_format == pcm_16000
# == raw signed 16-bit little-endian mono PCM @ 16000 Hz, no container.
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_MS // 1000   # 3200

# ── end-of-turn detector (calibrated over 8 captured agent turns) ────────────
# The server streams a CONTINUOUS 9600-byte carrier forever (background_sound
# office1 @ 0.08), so "frames stopped" is meaningless. Speech is an AMPLITUDE
# floor. min per-turn speech peak 3266; max carrier-only peak 2942; longest
# sub-threshold run INSIDE a turn was exactly 1 frame; longest intra-turn wall
# gap 0.918 s. 0.9 s was measured TOO TIGHT (it split a real turn), so this
# spike uses the PRODUCTION value.
SPEECH_PEAK = 3000            # ~9.2 % of full scale
QUIET_FRAMES = 5              # ~1.5 s at 300 ms/frame
QUIET_SECONDS = 1.5           # wall-clock backstop if frames stall

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

# ── HARDCODED persona lines (no LLM in this spike) ───────────────────────────
# Numerals are SPELLED OUT on purpose: the Sarvam spike measured that written
# digits ("1499") get voiced digit-by-digit and come back as "एक चार नौ नौ".
# Line 1 is the exact fixture spike_audio_protocol.py used, so its transcript is
# a regression check against a known-deterministic result.
PERSONA_LINES = [
    "English please. Fourteen ninety nine is too much yaar, kuch discount milega kya?",
    "Arre ten percent se kya hota hai? Mere dost ko toh thirty percent off mila tha. "
    "Aap bhi thirty percent kar do na.",
    "Theek hai, final batao. Best price kya de sakte ho? Warna main cancel kar deta hoon.",
]

# What Tara's scribe_realtime returned for line 1 in all three prior captures.
KNOWN_ASR_FIXTURE = "English, please. 1,499 is too much here. कुछ discount मिलेगा क्या?"

TTS_V2 = {"model": "bulbul:v2", "speaker": "anushka"}
TTS_V3 = {"model": "bulbul:v3", "speaker": "varun"}


# ── env ──────────────────────────────────────────────────────────────────────
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ── stdlib-only audio helpers (no numpy / scipy / soundfile) ─────────────────
def peak_amplitude(raw: bytes) -> int:
    n = len(raw) // 2
    if not n:
        return 0
    return max(abs(s) for s in struct.unpack(f"<{n}h", raw[: n * 2]))


def strip_riff(wav: bytes) -> bytes:
    """RIFF/WAVE -> raw PCM body. Walk the chunks; do NOT assume 44 bytes."""
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", "not a RIFF/WAVE payload"
    pos = 12
    while pos + 8 <= len(wav):
        cid = wav[pos:pos + 4]
        size = int.from_bytes(wav[pos + 4:pos + 8], "little")
        if cid == b"data":
            return wav[pos + 8: pos + 8 + size]
        pos += 8 + size + (size & 1)
    raise AssertionError("no data chunk in WAV")


def wav_params(wav: bytes) -> dict:
    with wave.open(io.BytesIO(wav), "rb") as w:
        return {"channels": w.getnchannels(), "sampwidth": w.getsampwidth(),
                "framerate": w.getframerate(), "frames": w.getnframes()}


def wrap_wav(pcm: bytes, hz: int = SAMPLE_RATE) -> bytes:
    """Saaras REJECTS headerless PCM (400). Tara emits headerless PCM.
    This 3-line stdlib wrap is the entire reverse transcode."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(hz)
        w.writeframes(pcm)
    return buf.getvalue()


# ── Sarvam (blocking; always called via asyncio.to_thread) ───────────────────
def sarvam_tts_rest(key: str, text: str, combo: dict) -> dict:
    """Bulbul REST -> WAV -> raw PCM16 @16k. Returns timing + bytes."""
    body = {
        "text": text,
        "target_language_code": "en-IN",
        "speaker": combo["speaker"],
        "model": combo["model"],
        # WITHOUT THIS YOU GET 22050 Hz AND TARA HEARS WRONG-SPEED AUDIO.
        "speech_sample_rate": SAMPLE_RATE,
    }
    t0 = time.perf_counter()
    r = httpx.post(SARVAM_TTS, headers={"api-subscription-key": key,
                                        "Content-Type": "application/json"},
                   json=body, timeout=120.0)
    net_s = time.perf_counter() - t0
    r.raise_for_status()
    wav = base64.b64decode(r.json()["audios"][0])
    t1 = time.perf_counter()
    pcm = strip_riff(wav)
    strip_s = time.perf_counter() - t1
    return {"pcm": pcm, "wav_bytes": len(wav), "wav_params": wav_params(wav),
            "synth_s": round(net_s, 3), "transcode_s": round(strip_s, 6),
            "source": f"REST {combo['model']}/{combo['speaker']}"}


async def sarvam_tts_ws(key: str, text: str, combo: dict, timeout: float = 25.0) -> dict:
    """Bulbul WS, output_audio_codec=linear16 @16k -> HEADERLESS PCM straight off
    the wire. This is the zero-byte-munging path: no RIFF at all."""
    url = f"{SARVAM_TTS_WS}?model={combo['model']}"
    t0 = time.perf_counter()
    chunks: list[bytes] = []
    first_s = None
    async with websockets.connect(url, additional_headers={"api-subscription-key": key},
                                  ping_interval=None, open_timeout=20,
                                  max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "config", "data": {
            "speaker": combo["speaker"], "target_language_code": "en-IN",
            "output_audio_codec": "linear16", "speech_sample_rate": SAMPLE_RATE}}))
        await ws.send(json.dumps({"type": "text", "data": {"text": text}}))
        await ws.send(json.dumps({"type": "flush"}))
        deadline = time.monotonic() + timeout
        idle = 3.0
        while time.monotonic() < deadline:
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=idle)
            except asyncio.TimeoutError:
                break
            if isinstance(m, (bytes, bytearray)):
                b = bytes(m)
            else:
                ev = json.loads(m)
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                b64 = (data or {}).get("audio")
                if not b64:
                    continue
                b = base64.b64decode(b64)
            if first_s is None:
                first_s = time.perf_counter() - t0
            chunks.append(b)
    pcm = b"".join(chunks)
    return {"pcm": pcm, "wav_bytes": 0, "wav_params": None,
            "synth_s": round(time.perf_counter() - t0, 3),
            "transcode_s": 0.0,
            "time_to_first_audio_s": round(first_s, 3) if first_s else None,
            "ws_chunks": len(chunks),
            "source": f"WS {combo['model']}/{combo['speaker']} linear16"}


def sarvam_stt(key: str, pcm: bytes, language_code: str = "en-IN") -> dict:
    """Tara's headerless pcm_16000 -> 44-byte WAV wrap -> Saaras saarika:v2.5."""
    t0 = time.perf_counter()
    wav = wrap_wav(pcm)
    wrap_s = time.perf_counter() - t0
    attempts = []
    for lang in (language_code, "unknown"):
        t1 = time.perf_counter()
        try:
            r = httpx.post(SARVAM_STT, headers={"api-subscription-key": key},
                           files={"file": ("tara_turn.wav", wav, "audio/wav")},
                           data={"model": "saarika:v2.5", "language_code": lang},
                           timeout=180.0)
            dt = time.perf_counter() - t1
            if r.status_code == 200:
                body = r.json()
                return {"transcript": body.get("transcript"),
                        "language_code_used": lang,
                        "raw": body,
                        "wrap_s": round(wrap_s, 6),
                        "stt_s": round(dt, 3),
                        "attempts": attempts}
            attempts.append({"language_code": lang, "status": r.status_code,
                             "body": r.text[:300]})
        except Exception as e:  # noqa: BLE001
            attempts.append({"language_code": lang, "error": repr(e)})
    return {"transcript": None, "wrap_s": round(wrap_s, 6), "stt_s": None,
            "attempts": attempts}


# ── the live conversation ────────────────────────────────────────────────────
class VoiceConversation:
    """One live VOICE conversation. The reader task lives from open() to close()."""

    def __init__(self, api_key: str, agent_id: str):
        self.api_key = api_key
        self.agent_id = agent_id
        self.t0 = 0.0
        self.ws = None
        self.fh = (OUT_DIR / "events.jsonl").open("w", encoding="utf-8")
        self.events: Counter[str] = Counter()
        self.timeline: list[dict] = []
        self.agent_responses: list[dict] = []
        self.user_transcripts: list[dict] = []
        self.conversation_id: str | None = None
        self.metadata: dict = {}
        self.pings = 0
        self.stop = asyncio.Event()
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.end_call_seen = False
        self.frame_sizes: Counter[int] = Counter()

        # end-of-turn state machine
        self.in_turn = False
        self.quiet_run = 0
        self.last_speech_at: float | None = None
        self._buf: list[bytes] = []
        self._turn_start_el: float | None = None
        self._turn_event_id = None
        self._turn_peak = 0
        self.agent_turns: list[dict] = []     # {pcm, start_el, end_el, peak, event_id}
        self.carrier_peaks: list[int] = []

    # -- plumbing --
    def el(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def rec(self, direction: str, payload: dict) -> None:
        self.fh.write(json.dumps({"el": self.el(), "dir": direction,
                                  "payload": payload}, ensure_ascii=False) + "\n")
        self.fh.flush()

    def mark(self, kind: str, **kw) -> None:
        row = {"el": self.el(), "kind": kind, **kw}
        self.timeline.append(row)
        print(f"  [{row['el']:>8.3f}s] {kind}"
              + ("  " + json.dumps(kw, ensure_ascii=False) if kw else ""))

    async def send(self, frame: dict) -> None:
        await self.ws.send(json.dumps(frame, ensure_ascii=False))
        if "user_audio_chunk" in frame:
            self.rec("out", {"user_audio_chunk": f"<{CHUNK_BYTES} B pcm16>"})
        else:
            self.rec("out", frame)

    # -- lifecycle --
    async def open(self) -> None:
        self.t0 = time.monotonic()
        self.ws = await websockets.connect(
            f"{WS_URL}?agent_id={self.agent_id}",
            additional_headers={"xi-api-key": self.api_key},
            ping_interval=None,               # library keepalive is redundant here
            max_size=16 * 1024 * 1024,
            open_timeout=30,
        )
        self.mark("socket_open")
        # VOICE MODE: NO text_only override. dynamic_variables stays TOP-LEVEL.
        await self.send({"type": "conversation_initiation_client_data",
                         "dynamic_variables": DYNAMIC_VARIABLES})
        self.mark("init_sent", voice_mode=True)

    async def reader(self) -> None:
        """PERMANENTLY LIVE. Drains frames and pongs. Never blocks on compute."""
        try:
            while True:
                raw = await self.ws.recv()
                if isinstance(raw, (bytes, bytearray)):
                    self.events["<binary_frame>"] += 1
                    self.mark("binary_frame", bytes=len(raw))
                    continue
                await self.on_frame(json.loads(raw))
        except websockets.exceptions.ConnectionClosed as e:
            rcvd = getattr(e, "rcvd", None)
            self.close_code = getattr(rcvd, "code", None)
            self.close_reason = getattr(rcvd, "reason", None)
            self.mark("SOCKET_CLOSED", code=self.close_code, reason=self.close_reason)
        except Exception as e:  # noqa: BLE001
            self.mark("READER_ERROR", err=repr(e))
        finally:
            self.stop.set()

    async def on_frame(self, msg: dict) -> None:
        t = msg.get("type") or "<no_type>"
        self.events[t] += 1

        if t == "audio":
            ev = msg.get("audio_event") or {}
            raw = base64.b64decode(ev.get("audio_base_64") or "")
            self.frame_sizes[len(raw)] += 1
            self._on_audio(raw, ev.get("event_id"))
            return

        if t == "ping":
            ev = msg.get("ping_event") or {}
            self.pings += 1
            self.rec("in", msg)
            # Pong IMMEDIATELY. NEVER sleep on ping_ms. THIS is the keepalive.
            await self.send({"type": "pong", "event_id": ev.get("event_id")})
            return

        self.rec("in", msg)
        if t == "conversation_initiation_metadata":
            ev = msg.get("conversation_initiation_metadata_event") or {}
            self.metadata = ev
            self.conversation_id = ev.get("conversation_id")
            self.mark("metadata", conversation_id=ev.get("conversation_id"),
                      user_input_audio_format=ev.get("user_input_audio_format"),
                      agent_output_audio_format=ev.get("agent_output_audio_format"))
        elif t == "agent_response":
            ev = msg.get("agent_response_event") or {}
            self.agent_responses.append({"el": self.el(), **ev})
            self.mark("agent_response", event_id=ev.get("event_id"),
                      text=(ev.get("agent_response") or "")[:160])
        elif t == "user_transcript":
            ev = msg.get("user_transcription_event") or {}
            self.user_transcripts.append({"el": self.el(), **ev})
            self.mark("USER_TRANSCRIPT", **ev)
        elif t == "agent_tool_response":
            ev = msg.get("agent_tool_response") or {}
            self.mark("agent_tool_response", **ev)
            if ev.get("tool_name") == "end_call":
                self.end_call_seen = True
                self.mark("END_CALL_TOOL — agent hung up")
        else:
            self.mark(t, keys=list(msg.keys()))

    def _on_audio(self, raw: bytes, event_id) -> None:
        pk = peak_amplitude(raw)
        now = time.monotonic()
        if pk >= SPEECH_PEAK:
            if not self.in_turn:
                self.in_turn = True
                self._buf = []
                self._turn_start_el = self.el()
                self._turn_event_id = event_id
                self._turn_peak = 0
                self.mark("SPEECH_START", event_id=event_id,
                          peak_pct=round(pk / 32768 * 100, 1))
            self.quiet_run = 0
            self.last_speech_at = now
            self._turn_peak = max(self._turn_peak, pk)
            self._buf.append(raw)
        else:
            if self.in_turn:
                self.quiet_run += 1
                self._buf.append(raw)
                quiet_wall = now - (self.last_speech_at or now)
                if self.quiet_run >= QUIET_FRAMES or quiet_wall >= QUIET_SECONDS:
                    speech = b"".join(self._buf[: len(self._buf) - self.quiet_run])
                    turn = {"pcm": speech, "start_el": self._turn_start_el,
                            "end_el": self.el(), "peak": self._turn_peak,
                            "event_id": self._turn_event_id,
                            "seconds": round(len(speech) / 2 / SAMPLE_RATE, 2)}
                    self.agent_turns.append(turn)
                    self.in_turn = False
                    self._buf = []
                    self.quiet_run = 0
                    self.mark("AGENT_TURN_END", event_id=turn["event_id"],
                              speech_s=turn["seconds"],
                              peak_pct=round(turn["peak"] / 32768 * 100, 1))
            else:
                self.carrier_peaks.append(pk)

    # -- waits (never own the socket; the reader keeps running) --
    async def wait_for_turn(self, n_before: int, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.agent_turns) > n_before:
                return self.agent_turns[-1]
            if self.stop.is_set():
                return None
            await asyncio.sleep(0.05)
        self.mark("agent_turn_not_detected", waited=timeout)
        return None

    async def wait_for_transcript(self, n_before: int, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.user_transcripts) > n_before:
                return self.user_transcripts[-1]
            if self.stop.is_set():
                return None
            await asyncio.sleep(0.05)
        return None

    # -- speaking --
    async def speak_and_hold(self, pcm: bytes, *, lead_ms: int = 300,
                             max_hold_s: float = 12.0) -> dict:
        """Stream the utterance, then KEEP THE MIC OPEN with paced silence until
        scribe_realtime endpoints us (a user_transcript arrives) — then stop
        immediately.

        WHY: `speak()` (stream, then stop dead) works for the FIRST user turn and
        then never works again — measured twice, two conversations, two different
        TTS voices. Nothing at all comes back for turn 2. The hypothesis this
        method tests is that after the first endpoint the server will not endpoint
        a burst that has already ended; it needs audio still flowing to close the
        turn.

        The hold is BOUNDED and transcript-triggered on purpose: streaming silence
        open-endedly is the one thing measured to KILL a conversation (empty user
        turns on the turn_timeout=10 s cadence -> 'Are you still there?' -> end_call
        -> close at 59 s).
        """
        n_tr = len(self.user_transcripts)
        lead = b"\x00" * (CHUNK_BYTES * (lead_ms // CHUNK_MS))
        stream = lead + pcm
        n = (len(stream) + CHUNK_BYTES - 1) // CHUNK_BYTES
        t_start = time.monotonic()
        max_drift = 0.0
        sent = 0
        for i in range(n):
            if self.stop.is_set():
                break
            piece = stream[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]
            await self.send({"user_audio_chunk": base64.b64encode(piece).decode()})
            sent += 1
            target = t_start + (i + 1) * (CHUNK_MS / 1000)   # ABSOLUTE clock
            max_drift = max(max_drift, time.monotonic() - target)
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        utter_wall = time.monotonic() - t_start
        self.mark("utterance_streamed", chunks=n, wall_s=round(utter_wall, 3),
                  ideal_s=round(n * CHUNK_MS / 1000, 3),
                  max_instant_drift_s=round(max_drift, 4))

        quiet = base64.b64encode(b"\x00" * CHUNK_BYTES).decode()
        hold_chunks = 0
        hold_t0 = time.monotonic()
        i = n
        while (time.monotonic() - hold_t0) < max_hold_s and not self.stop.is_set():
            if len(self.user_transcripts) > n_tr:
                break
            await self.send({"user_audio_chunk": quiet})
            hold_chunks += 1
            i += 1
            target = t_start + (i + 1) * (CHUNK_MS / 1000)
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        endpointed = len(self.user_transcripts) > n_tr
        self.mark("mic_closed", hold_chunks=hold_chunks,
                  hold_s=round(time.monotonic() - hold_t0, 2),
                  endpointed_while_open=endpointed)
        return {"chunks": sent, "hold_chunks": hold_chunks,
                "utterance_wall_s": round(utter_wall, 3),
                "hold_s": round(time.monotonic() - hold_t0, 3),
                "endpointed_while_mic_open": endpointed,
                "max_instant_drift_s": round(max_drift, 4),
                "pcm_seconds": round(len(pcm) / 2 / SAMPLE_RATE, 2)}

    async def speak(self, pcm: bytes, lead_ms: int = 300, tail_ms: int = 1500) -> dict:
        """Stream raw pcm_16000 as user_audio_chunk frames, PACED AT REAL TIME
        against an ABSOLUTE monotonic clock.

        Never sleep(chunk_duration) in a loop: the per-iteration error
        (json encode + ws.send + scheduler) accumulates and drifts seconds over
        a long utterance, desynchronising Tara's turn detection with no error.
        """
        lead = b"\x00" * (CHUNK_BYTES * (lead_ms // CHUNK_MS))
        tail = b"\x00" * (CHUNK_BYTES * (tail_ms // CHUNK_MS))
        stream = lead + pcm + tail
        n = (len(stream) + CHUNK_BYTES - 1) // CHUNK_BYTES
        t_start = time.monotonic()
        max_drift = 0.0
        for i in range(n):
            if self.stop.is_set():
                break
            piece = stream[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]
            await self.send({"user_audio_chunk": base64.b64encode(piece).decode()})
            # ABSOLUTE clock. target is derived from t_start, never accumulated.
            target = t_start + (i + 1) * (CHUNK_MS / 1000)
            drift = time.monotonic() - target
            max_drift = max(max_drift, drift)
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        wall = time.monotonic() - t_start
        ideal = n * CHUNK_MS / 1000
        self.mark("utterance_streamed", chunks=n, wall_s=round(wall, 3),
                  ideal_s=round(ideal, 3), drift_s=round(wall - ideal, 3),
                  max_instant_drift_s=round(max_drift, 4))
        return {"chunks": n, "wall_s": round(wall, 3), "ideal_s": round(ideal, 3),
                "drift_s": round(wall - ideal, 4),
                "max_instant_drift_s": round(max_drift, 4),
                "pcm_seconds": round(len(pcm) / 2 / SAMPLE_RATE, 2)}

    async def close(self) -> None:
        # Close the SOCKET first, then the log file. Closing the log while the
        # reader task is still draining raises ValueError('I/O operation on
        # closed file') inside the reader.
        try:
            if self.ws is not None:
                await self.ws.close()
        except Exception:
            pass

    def close_log(self) -> None:
        try:
            self.fh.close()
        except Exception:
            pass


# ── the run ──────────────────────────────────────────────────────────────────
async def run(args) -> dict:
    env = load_env()
    el_key = env["ELEVENLABS_API_KEY"]
    agent_id = env["ELEVENLABS_AGENT_ID"]
    sv_key = env["SARVAM_API_KEY"]

    result: dict = {"turns": [], "gate_passed": False}
    conv = VoiceConversation(el_key, agent_id)
    await conv.open()
    reader = asyncio.create_task(conv.reader())

    try:
        # ── Tara's OPENING turn: capture her audio AND her text ──────────────
        print("\n--- waiting for Tara's unprompted opening turn ---")
        opening = await conv.wait_for_turn(0, timeout=45)
        if opening is None:
            result["blocking_error"] = "no opening agent turn detected within 45s"
            return result
        opening_text = conv.agent_responses[0]["agent_response"] if conv.agent_responses else None
        (OUT_DIR / "tara_turn_0.pcm").write_bytes(opening["pcm"])
        (OUT_DIR / "tara_turn_0.wav").write_bytes(wrap_wav(opening["pcm"]))

        # Transcribe HER on a worker thread — the reader keeps ponging throughout.
        stt0 = await asyncio.to_thread(sarvam_stt, sv_key, opening["pcm"])
        conv.mark("tara_stt", s=stt0["stt_s"], transcript=(stt0["transcript"] or "")[:160])

        result["opening"] = {
            "agent_response_text": opening_text,
            "dynamic_vars_rendered": bool(opening_text) and "{{" not in (opening_text or ""),
            "speech_seconds": opening["seconds"],
            "peak_pct": round(opening["peak"] / 32768 * 100, 2),
            "our_stt_of_her_audio": stt0["transcript"],
            "our_stt_latency_s": stt0["stt_s"],
            "our_stt_language_code": stt0.get("language_code_used"),
            "wav_wrap_s": stt0["wrap_s"],
        }

        # ── TWO persona turns ────────────────────────────────────────────────
        for idx, line in enumerate(PERSONA_LINES[: args.turns], start=1):
            print(f"\n--- persona turn {idx} ---\n  WE SAY: {line}")
            turn: dict = {"n": idx, "persona_line": line}
            t_compose0 = time.monotonic()

            # TTS on a worker thread / a separate WS — the reader keeps ponging.
            use_ws = (args.tts == "ws") or (args.tts == "mixed" and idx == 2)
            tts = None
            if use_ws:
                try:
                    tts = await sarvam_tts_ws(sv_key, line, TTS_V3)
                    if len(tts["pcm"]) < SAMPLE_RATE:  # < 0.5 s => treat as failed
                        conv.mark("tts_ws_too_short", bytes=len(tts["pcm"]))
                        tts = None
                except Exception as e:  # noqa: BLE001
                    conv.mark("tts_ws_failed", err=repr(e))
                    tts = None
            if tts is None:
                tts = await asyncio.to_thread(sarvam_tts_rest, sv_key, line, TTS_V2)
            compose_s = time.monotonic() - t_compose0

            pcm = tts["pcm"]
            (OUT_DIR / f"persona_turn_{idx}.pcm").write_bytes(pcm)
            conv.mark("tts_ready", source=tts["source"], synth_s=tts["synth_s"],
                      pcm_bytes=len(pcm),
                      audio_s=round(len(pcm) / 2 / SAMPLE_RATE, 2))

            turn["tts"] = {k: v for k, v in tts.items() if k != "pcm"}
            turn["compose_s"] = round(compose_s, 3)
            turn["playout_s"] = round(len(pcm) / 2 / SAMPLE_RATE, 2)
            turn["chars"] = len(line)
            turn["chars_per_second_of_speech"] = round(
                len(line) / max(0.01, len(pcm) / 2 / SAMPLE_RATE), 2)

            n_tr = len(conv.user_transcripts)
            n_turn = len(conv.agent_turns)

            if args.mic == "hold":
                stream = await conv.speak_and_hold(pcm)
            else:
                stream = await conv.speak(pcm)
            turn["stream"] = stream
            stream_end_el = conv.el()

            heard = await conv.wait_for_transcript(n_tr, timeout=25)
            turn["tara_heard"] = (heard or {}).get("user_transcript")
            # how long after our LAST audio chunk her ASR produced a transcript
            turn["transcript_latency_s"] = (
                round(heard["el"] - stream_end_el, 3) if heard else None)

            reply = await conv.wait_for_turn(n_turn, timeout=60)
            reply_text = None
            for ar in conv.agent_responses:
                if heard and ar["el"] >= heard["el"]:
                    reply_text = ar.get("agent_response")
                    break
            turn["tara_reply_text"] = reply_text
            if reply:
                (OUT_DIR / f"tara_turn_{idx}.pcm").write_bytes(reply["pcm"])
                (OUT_DIR / f"tara_turn_{idx}.wav").write_bytes(wrap_wav(reply["pcm"]))
                stt = await asyncio.to_thread(sarvam_stt, sv_key, reply["pcm"])
                conv.mark("tara_stt", s=stt["stt_s"],
                          transcript=(stt["transcript"] or "")[:160])
                turn["tara_reply_speech_s"] = reply["seconds"]
                turn["tara_reply_peak_pct"] = round(reply["peak"] / 32768 * 100, 2)
                turn["our_stt_of_her_reply"] = stt["transcript"]
                turn["our_stt_latency_s"] = stt["stt_s"]
            else:
                turn["tara_reply_speech_s"] = None

            turn["complete"] = bool(turn["tara_heard"]) and bool(reply)
            result["turns"].append(turn)
            print(f"  TURN {idx} complete={turn['complete']}")
            if conv.stop.is_set():
                conv.mark("socket_gone_after_turn", n=idx)
                break

        completed = sum(1 for t in result["turns"] if t.get("complete"))
        result["turns_completed"] = completed
        result["gate_passed"] = completed >= 2

    finally:
        result["conversation_id"] = conv.conversation_id
        result["metadata"] = conv.metadata
        result["event_types"] = dict(conv.events)
        result["audio_frame_sizes"] = dict(conv.frame_sizes)
        result["pings_ponged"] = conv.pings
        result["socket_closed"] = conv.stop.is_set()
        result["close_code"] = conv.close_code
        result["close_reason"] = conv.close_reason
        result["end_call_seen"] = conv.end_call_seen
        result["agent_responses"] = conv.agent_responses
        result["user_transcripts"] = conv.user_transcripts
        result["carrier_peak_max"] = max(conv.carrier_peaks) if conv.carrier_peaks else None
        result["agent_turn_peaks"] = [t["peak"] for t in conv.agent_turns]
        result["total_wall_s"] = conv.el()
        result["timeline"] = conv.timeline
        await conv.close()
        reader.cancel()
        try:
            await reader
        except (asyncio.CancelledError, Exception):  # noqa: B014
            pass
        conv.close_log()

    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=2)
    ap.add_argument("--tts", choices=["rest", "ws", "mixed"], default="mixed",
                    help="rest = bulbul:v2 REST both turns; ws = bulbul:v3 WS both; "
                         "mixed = REST turn 1, v3 WS turn 2 (falls back to REST)")
    ap.add_argument("--mic", choices=["stop", "hold"], default="hold",
                    help="stop = stream utterance + 1.5 s tail then stop dead "
                         "(FAILS on turn 2, measured twice); hold = keep the mic "
                         "open with paced silence until scribe_realtime endpoints us")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    result = asyncio.run(run(args))
    out = OUT_DIR / (f"result_{args.tag}.json" if args.tag else "result.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 74)
    print("conversation_id :", result.get("conversation_id"))
    print("event types     :", json.dumps(result.get("event_types")))
    print("frame sizes     :", json.dumps(result.get("audio_frame_sizes")))
    print("pings ponged    :", result.get("pings_ponged"))
    print("socket closed   :", result.get("socket_closed"), result.get("close_code"))
    print("turns completed :", result.get("turns_completed"))
    print("GATE PASSED     :", result.get("gate_passed"))
    print("result ->", out)
    return 0 if result.get("gate_passed") else 1


if __name__ == "__main__":
    sys.exit(main())
