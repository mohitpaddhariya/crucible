#!/usr/bin/env python3
"""audit_level1_probe — INDEPENDENT re-run of the Level 1 gate's load-bearing claims.

Written from scratch (not a copy of scripts/spike_audio_turn.py) so that a claim is
only "reproduced" if it survives a second implementation.

Arms:
  --arm silent   voice; opening turn -> one real spoken utterance -> her reply ->
                 optionally a second (long-answer-provoking) utterance -> then
                 SEND NOTHING for --hold seconds while ponging.
                 TESTS: "the 60 s kill is a pong rule, not a user-message rule".
  --arm chunks   same up to her reply, then stream paced SILENCE user_audio_chunk
                 frames continuously for --hold seconds.
                 TESTS: "streaming chunks does not keep you alive; it gets you hung up on".
  --arm nopong   same up to her reply, then stop reading the socket entirely for
                 --hold seconds (this is what the gate called "nopong" — note it is
                 ALSO "no reading", which is the confound this arm exists to expose).

Every inbound audio frame is logged with its decoded byte length, peak amplitude and
event_id so the turn-end detector can be re-derived OFFLINE from the capture.

Read-only against ElevenLabs: one wss conversation, no REST writes, ever.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import struct
import time
import wave
from collections import Counter
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "runs" / "_audit_level1"
OUT.mkdir(parents=True, exist_ok=True)

WS_URL = "wss://api.elevenlabs.io/v1/convai/conversation"
SARVAM_TTS = "https://api.sarvam.ai/text-to-speech"

SAMPLE_RATE = 16000
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # 3200

SPEECH_PEAK = 3000
QUIET_FRAMES = 5
QUIET_SECONDS = 1.5

DYNAMIC_VARIABLES = {
    "subscriber_name": "Aravinth",
    "call_reason": "win_back",
    "call_intro": ("I'm calling because your NovaPlay Super annual plan lapsed on "
                   "20 June and I'd love to get you back before the cricket ends."),
    "plan_name": "NovaPlay Super (annual)",
    "amount_inr": "1499",
    "expiry_date": "20 June",
    "content_hook": "the ICC Women's T20 World Cup, live through 5 July",
    "offer_text": "10% off if you reactivate before 20 June",
    "renewal_date": "",
    "next_retry_date": "",
    "failure_reason": "",
}

# Reuse the already-synthesised utterance so this probe costs zero TTS quota.
CANNED_PCM = ROOT / "runs" / "_spike_audio_turn" / "persona_turn_1.pcm"
CANNED_TEXT = "English please. Fourteen ninety nine is too much yaar, kuch discount milega kya?"
# Provokes a LONG agent turn — the case the gate never measured.
LONG_LINE = ("Ok listen, before I decide, please explain everything the plan includes, "
             "all the channels, all the sports, and how the discount works, in full detail.")


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def peak(raw: bytes) -> int:
    n = len(raw) // 2
    if not n:
        return 0
    return max(abs(s) for s in struct.unpack(f"<{n}h", raw[:n * 2]))


def strip_riff(wav: bytes) -> bytes:
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    pos = 12
    while pos + 8 <= len(wav):
        cid = wav[pos:pos + 4]
        size = int.from_bytes(wav[pos + 4:pos + 8], "little")
        if cid == b"data":
            return wav[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    raise AssertionError("no data chunk")


def wrap_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def tts(key: str, text: str) -> bytes:
    r = httpx.post(SARVAM_TTS,
                   headers={"api-subscription-key": key, "Content-Type": "application/json"},
                   json={"text": text, "target_language_code": "en-IN",
                         "speaker": "anushka", "model": "bulbul:v2",
                         "speech_sample_rate": SAMPLE_RATE},
                   timeout=120.0)
    r.raise_for_status()
    wav = base64.b64decode(r.json()["audios"][0])
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SAMPLE_RATE), \
            f"bulbul returned {w.getnchannels()}ch/{w.getsampwidth()}B/{w.getframerate()}Hz"
    return strip_riff(wav)


class Probe:
    def __init__(self, key: str, agent: str, tag: str):
        self.key, self.agent, self.tag = key, agent, tag
        self.t0 = 0.0
        self.ws = None
        self.log = (OUT / f"events_{tag}.jsonl").open("w", encoding="utf-8")
        self.counts: Counter[str] = Counter()
        self.frames: list[dict] = []          # every inbound audio frame
        self.agent_responses: list[dict] = []
        self.user_transcripts: list[dict] = []
        self.tool_responses: list[dict] = []
        self.timeline: list[dict] = []
        self.pings = self.pongs = 0
        self.stop = asyncio.Event()
        self.reader_paused = asyncio.Event()   # set => reader stops reading (nopong arm)
        self.suppress_pong = False             # readnopong arm: drain, but never pong
        self.close_code = None
        self.close_reason = None
        self.closed_at = None
        self.end_call = False
        self.meta: dict = {}
        self.sizes: Counter[int] = Counter()
        # live detector state
        self.in_turn = False
        self.quiet = 0
        self.last_speech = None
        self._buf: list[bytes] = []
        self._start_el = None
        self._eid = None
        self._peak = 0
        self.turns: list[dict] = []
        self.carrier_max = 0

    def el(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def mark(self, kind: str, **kw):
        row = {"el": self.el(), "kind": kind, **kw}
        self.timeline.append(row)
        print(f"  [{row['el']:>8.3f}] {kind} " + (json.dumps(kw, ensure_ascii=False) if kw else ""))

    def rec(self, d: str, payload: dict):
        self.log.write(json.dumps({"el": self.el(), "dir": d, "payload": payload},
                                  ensure_ascii=False) + "\n")
        self.log.flush()

    async def send(self, frame: dict):
        await self.ws.send(json.dumps(frame, ensure_ascii=False))
        if "user_audio_chunk" in frame:
            self.rec("out", {"user_audio_chunk": f"<{CHUNK_BYTES}B>"})
        else:
            self.rec("out", frame)

    async def open(self):
        self.t0 = time.monotonic()
        self.ws = await websockets.connect(
            f"{WS_URL}?agent_id={self.agent}",
            additional_headers={"xi-api-key": self.key},
            ping_interval=None, max_size=16 * 1024 * 1024, open_timeout=30)
        self.mark("socket_open")
        await self.send({"type": "conversation_initiation_client_data",
                         "dynamic_variables": DYNAMIC_VARIABLES})

    async def reader(self):
        try:
            while True:
                while self.reader_paused.is_set():
                    await asyncio.sleep(0.05)
                raw = await self.ws.recv()
                if isinstance(raw, (bytes, bytearray)):
                    self.counts["<binary>"] += 1
                    continue
                await self.on_frame(json.loads(raw))
        except websockets.exceptions.ConnectionClosed as e:
            r = getattr(e, "rcvd", None)
            self.close_code = getattr(r, "code", None)
            self.close_reason = getattr(r, "reason", None)
            self.closed_at = self.el()
            self.mark("SOCKET_CLOSED", code=self.close_code, reason=self.close_reason)
        except Exception as e:  # noqa: BLE001
            self.closed_at = self.el()
            self.mark("READER_ERROR", err=repr(e))
        finally:
            self.stop.set()

    async def on_frame(self, msg: dict):
        t = msg.get("type") or "<none>"
        self.counts[t] += 1
        if t == "audio":
            ev = msg.get("audio_event") or {}
            raw = base64.b64decode(ev.get("audio_base_64") or "")
            self.sizes[len(raw)] += 1
            pk = peak(raw)
            self.frames.append({"el": self.el(), "bytes": len(raw), "peak": pk,
                                "event_id": ev.get("event_id"),
                                "is_final": ev.get("is_final")})
            self.rec("in", {"type": "audio", "audio_event": {
                "audio_base_64": f"<{len(raw)}B peak={pk}>",
                "event_id": ev.get("event_id"), "is_final": ev.get("is_final")}})
            self._detect(raw, pk, ev.get("event_id"))
            return
        if t == "ping":
            ev = msg.get("ping_event") or {}
            self.pings += 1
            self.rec("in", msg)
            if self.suppress_pong:
                return
            await self.send({"type": "pong", "event_id": ev.get("event_id")})
            self.pongs += 1
            return
        self.rec("in", msg)
        if t == "conversation_initiation_metadata":
            self.meta = msg.get("conversation_initiation_metadata_event") or {}
            self.mark("metadata", **self.meta)
        elif t == "agent_response":
            ev = msg.get("agent_response_event") or {}
            self.agent_responses.append({"el": self.el(), **ev})
            self.mark("agent_response", event_id=ev.get("event_id"),
                      chars=len(ev.get("agent_response") or ""))
        elif t == "user_transcript":
            ev = msg.get("user_transcription_event") or {}
            self.user_transcripts.append({"el": self.el(), **ev})
            self.mark("USER_TRANSCRIPT", **ev)
        elif t == "agent_tool_response":
            ev = msg.get("agent_tool_response") or {}
            self.tool_responses.append({"el": self.el(), **ev})
            self.mark("agent_tool_response", **ev)
            if ev.get("tool_name") == "end_call":
                self.end_call = True
        else:
            self.mark(t, keys=sorted(msg.keys()))

    def _detect(self, raw: bytes, pk: int, eid):
        now = time.monotonic()
        if pk >= SPEECH_PEAK:
            if not self.in_turn:
                self.in_turn, self._buf, self._start_el = True, [], self.el()
                self._eid, self._peak = eid, 0
                self.mark("SPEECH_START", event_id=eid, peak=pk)
            self.quiet = 0
            self.last_speech = now
            self._peak = max(self._peak, pk)
            self._buf.append(raw)
        elif self.in_turn:
            self.quiet += 1
            self._buf.append(raw)
            wall = now - (self.last_speech or now)
            if self.quiet >= QUIET_FRAMES or wall >= QUIET_SECONDS:
                sp = b"".join(self._buf[:len(self._buf) - self.quiet])
                self.turns.append({"pcm": sp, "start_el": self._start_el, "end_el": self.el(),
                                   "peak": self._peak, "event_id": self._eid,
                                   "seconds": round(len(sp) / 2 / SAMPLE_RATE, 2),
                                   "ended_by": "frames" if self.quiet >= QUIET_FRAMES else "wall"})
                self.mark("AGENT_TURN_END", event_id=self._eid,
                          speech_s=self.turns[-1]["seconds"],
                          ended_by=self.turns[-1]["ended_by"])
                self.in_turn, self._buf, self.quiet = False, [], 0
        else:
            self.carrier_max = max(self.carrier_max, pk)

    async def wait_turn(self, n: int, timeout: float):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if len(self.turns) > n:
                return self.turns[-1]
            if self.stop.is_set():
                return None
            await asyncio.sleep(0.05)
        self.mark("TURN_NOT_DETECTED", waited=timeout)
        return None

    async def speak_and_hold(self, pcm: bytes, max_hold_s: float = 8.0) -> dict:
        n_tr = len(self.user_transcripts)
        stream = b"\x00" * (CHUNK_BYTES * 3) + pcm
        n = (len(stream) + CHUNK_BYTES - 1) // CHUNK_BYTES
        t_start = time.monotonic()
        drift = 0.0
        for i in range(n):
            if self.stop.is_set():
                break
            await self.send({"user_audio_chunk": base64.b64encode(
                stream[i * CHUNK_BYTES:(i + 1) * CHUNK_BYTES]).decode()})
            target = t_start + (i + 1) * (CHUNK_MS / 1000)
            drift = max(drift, time.monotonic() - target)
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        utter_wall = time.monotonic() - t_start
        quiet_b64 = base64.b64encode(b"\x00" * CHUNK_BYTES).decode()
        hold0 = time.monotonic()
        i = n
        held = 0
        while (time.monotonic() - hold0) < max_hold_s and not self.stop.is_set():
            if len(self.user_transcripts) > n_tr:
                break
            await self.send({"user_audio_chunk": quiet_b64})
            held += 1
            i += 1
            target = t_start + (i + 1) * (CHUNK_MS / 1000)
            await asyncio.sleep(max(0.0, target - time.monotonic()))
        got = len(self.user_transcripts) > n_tr
        res = {"chunks": n, "hold_chunks": held, "utterance_wall_s": round(utter_wall, 3),
               "ideal_s": round(n * CHUNK_MS / 1000, 3),
               "max_instant_drift_s": round(drift, 4),
               "hold_s": round(time.monotonic() - hold0, 3),
               "endpointed_while_open": got}
        self.mark("mic_closed", **res)
        return res

    async def close(self):
        try:
            if self.ws:
                await self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.2)
        self.log.close()


async def run(arm: str, hold: float, long_turn: bool):
    env = load_env()
    p = Probe(env["ELEVENLABS_API_KEY"], env["ELEVENLABS_AGENT_ID"], arm)
    res: dict = {"arm": arm, "hold_requested_s": hold}
    await p.open()
    task = asyncio.create_task(p.reader())
    try:
        opening = await p.wait_turn(0, 30)
        res["opening_detected"] = bool(opening)
        res["opening_speech_s"] = opening["seconds"] if opening else None
        res["opening_agent_response"] = (p.agent_responses[0]["agent_response"]
                                         if p.agent_responses else None)
        res["opening_agent_response_el"] = (p.agent_responses[0]["el"]
                                            if p.agent_responses else None)

        pcm = CANNED_PCM.read_bytes()
        res["utterance_1"] = await p.speak_and_hold(pcm)
        res["heard_1"] = (p.user_transcripts[-1].get("user_transcript")
                          if p.user_transcripts else None)
        reply = await p.wait_turn(1, 60)
        res["reply_1_detected"] = bool(reply)
        res["reply_1_speech_s"] = reply["seconds"] if reply else None

        if long_turn:
            pcm2 = await asyncio.to_thread(tts, env["SARVAM_API_KEY"], LONG_LINE)
            n_before = len(p.turns)
            res["utterance_2"] = await p.speak_and_hold(pcm2)
            res["heard_2"] = (p.user_transcripts[-1].get("user_transcript")
                              if p.user_transcripts else None)
            reply2 = await p.wait_turn(n_before, 90)
            res["reply_2_detected"] = bool(reply2)
            res["reply_2_speech_s"] = reply2["seconds"] if reply2 else None

        last_user_frame_el = p.el()
        p.mark("HOLD_BEGINS", arm=arm, seconds=hold)
        t_hold = time.monotonic()
        if arm == "silent":
            while (time.monotonic() - t_hold) < hold and not p.stop.is_set():
                await asyncio.sleep(0.2)
        elif arm == "chunks":
            q = base64.b64encode(b"\x00" * CHUNK_BYTES).decode()
            i = 0
            n_sent = 0
            while (time.monotonic() - t_hold) < hold and not p.stop.is_set():
                try:
                    await p.send({"user_audio_chunk": q})
                except Exception as e:  # noqa: BLE001
                    p.mark("SEND_FAILED", err=repr(e))
                    break
                n_sent += 1
                i += 1
                target = t_hold + i * (CHUNK_MS / 1000)
                await asyncio.sleep(max(0.0, target - time.monotonic()))
            res["silence_chunks_streamed"] = n_sent
        elif arm == "readnopong":
            # THE DISAMBIGUATION the gate never ran: keep DRAINING the socket
            # (so no backpressure, no queue overflow) but send no pongs at all.
            # If this survives, "the 60 s kill is a pong rule" is wrong and the
            # real rule is "drain the socket".
            p.suppress_pong = True
            while (time.monotonic() - t_hold) < hold and not p.stop.is_set():
                await asyncio.sleep(0.2)
            res["pings_ignored"] = p.pings - p.pongs
            p.suppress_pong = False
            try:
                await p.send({"type": "pong", "event_id": 999999})
                res["post_hold_send_ok"] = True
            except Exception as e:  # noqa: BLE001
                res["post_hold_send_ok"] = False
                res["post_hold_send_err"] = repr(e)[:200]
        elif arm == "nopong":
            p.reader_paused.set()
            await asyncio.sleep(hold)
            p.reader_paused.clear()
            await asyncio.sleep(1.0)
            try:
                await p.send({"type": "pong", "event_id": 999999})
                res["post_pause_send_ok"] = True
            except Exception as e:  # noqa: BLE001
                res["post_pause_send_ok"] = False
                res["post_pause_send_err"] = repr(e)[:200]
        res["hold_actual_s"] = round(time.monotonic() - t_hold, 2)
        res["last_user_frame_el"] = last_user_frame_el
        res["alive_after_hold"] = not p.stop.is_set()
    finally:
        res.update({
            "conversation_id": p.meta.get("conversation_id"),
            "metadata": p.meta,
            "event_counts": dict(p.counts),
            "frame_sizes": {str(k): v for k, v in p.sizes.items()},
            "pings": p.pings, "pongs": p.pongs,
            "closed_at_s": p.closed_at, "close_code": p.close_code,
            "close_reason": p.close_reason, "end_call_seen": p.end_call,
            "agent_responses": [{k: v for k, v in a.items()} for a in p.agent_responses],
            "user_transcripts": p.user_transcripts,
            "tool_responses": p.tool_responses,
            "detected_turns": [{k: v for k, v in t.items() if k != "pcm"} for t in p.turns],
            "carrier_max_peak": p.carrier_max,
            "total_el_s": p.el(),
            "timeline": p.timeline,
        })
        for i, t in enumerate(p.turns):
            (OUT / f"{arm}_agent_turn_{i}.wav").write_bytes(wrap_wav(t["pcm"]))
        await p.close()
        task.cancel()
        (OUT / f"result_{arm}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items()
                      if k not in ("timeline", "agent_responses", "metadata")},
                     ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["silent", "chunks", "nopong", "readnopong"],
                    required=True)
    ap.add_argument("--hold", type=float, default=95.0)
    ap.add_argument("--long-turn", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.arm, a.hold, a.long_turn))
