#!/usr/bin/env python3
"""Offline smoke test for targets/elevenlabs_audio.py — ZERO network, ZERO quota.

The audio twin of scripts/smoke_loop_offline.py, and the gate for LEVEL1_SPEC
§10 step 2. Every byte it asserts against is a REAL capture already on disk:

  runs/_spike_audio_turn/events.jsonl      control frames of conv_1901kyekyjtpfmctw94pnefqdfyy
  runs/_spike_audio_turn/tara_turn_*.pcm   her three turns, frame-aligned raw pcm_16000
  runs/_spike_audio_turn/result_confirm.json  the HAND-VERIFIED boundaries for that run
  runs/_spike/spike_events_control-audio.jsonl  636 audio frames WITH full base64 payloads
  runs/_spike_audio/events_turn_*.jsonl    the protocol spike's per-frame peaks + arrival times

Fixtures covered (LEVEL1_SPEC §8):

  2  TURN DETECTOR REPLAY — the detector reproduces the hand-verified boundaries
     on every capture, including NOT splitting at el 28.876, and INCLUDING the
     negative control that a text-mode carrier stream yields zero turns.
  5  DEADLOCK ASSERTION — with no user_transcript ever fed, `no_user_transcript`
     fires at the 8 s mic-hold bound.

Plus the structural invariants that are cheap to check and expensive to lose:
the reader keeps ponging through a long compute block; `agent_response` is a
turn-START marker; outbound audio is a bare top-level key; nothing is streamed
between turns; teardown closes the socket before the log.

    PYTHONPATH=. uv run --python 3.12 python scripts/smoke_audio_offline.py
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import re
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from targets.base import AgentTurn                                   # noqa: E402
from targets.elevenlabs_audio import (                               # noqa: E402
    CHUNK_BYTES,
    MIC_HOLD_BOUND_S,
    SAMPLE_RATE,
    AudioAgentTurn,
    ElevenLabsAudioTarget,
    MicHoldTimeout,
    TurnDetector,
    peak_amplitude,
)

SPIKE = ROOT / "runs" / "_spike_audio_turn"
PROTO = ROOT / "runs" / "_spike_audio"
CONTROL = ROOT / "runs" / "_spike" / "spike_events_control-audio.jsonl"

FRAME_BYTES = 9600                    # every inbound audio frame, 208/208 and 636/636
FRAME_PERIOD_S = 0.3                  # ~3.3 frames/s, measured

PASSED: list[str] = []


def ok(name: str) -> None:
    PASSED.append(name)
    print(f"  ok  {name}")


# ======================================================================================
# Capture readers
# ======================================================================================

_PEAK_RE = re.compile(r"<(\d+) bytes, peak=(\d+)>")


def read_capture(path: Path):
    """Yield (el, type, payload, peak|None, nbytes) from any spike/production log.

    Handles both log dialects on disk: `el` (spike) or `t` (Level 0 target), and
    audio payloads recorded either as full base64 or as the redacted
    `"<9600 bytes, peak=NNN>"` form this target writes.
    """
    t0 = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        el = row.get("el")
        if el is None:
            if t0 is None:
                t0 = row["t"]
            el = round(row["t"] - t0, 3)
        payload = row["payload"]
        ftype = payload.get("type") or ("user_audio_chunk" if "user_audio_chunk" in payload
                                        else row.get("dir", "?"))
        if ftype == "audio":
            b64 = (payload.get("audio_event") or {}).get("audio_base_64") or ""
            m = _PEAK_RE.match(b64)
            if m:
                yield el, ftype, payload, int(m.group(2)), int(m.group(1))
            else:
                raw = base64.b64decode(b64)
                yield el, ftype, payload, peak_amplitude(raw), len(raw)
        else:
            yield el, ftype, payload, None, 0


def replay(path: Path, **kw) -> tuple[list, list[dict], TurnDetector]:
    """Push a whole capture through a real TurnDetector at its captured timestamps."""
    det = TurnDetector(keep_audio=False, **kw)
    turns, responses = [], []
    last_el = 0.0
    for el, ftype, payload, pk, nbytes in read_capture(path):
        last_el = el
        if ftype == "audio":
            done = det.feed_peak(pk, now=el, event_id=(payload.get("audio_event") or {}).get("event_id"))
            if done is not None:
                turns.append(done)
        elif ftype == "agent_response":
            ev = payload.get("agent_response_event") or {}
            responses.append({"el": el, "event_id": ev.get("event_id"),
                              "text": ev.get("agent_response") or ""})
    tail = det.flush(last_el)
    if tail is not None:
        turns.append(tail)
    return turns, responses, det


# ======================================================================================
# FIXTURE 2 — turn detector replay
# ======================================================================================

def fixture_2a_real_bytes() -> None:
    """Full-fidelity replay: real base64 PCM -> real peak_amplitude() -> detector.

    spike_events_control-audio.jsonl is the only capture that kept the audio
    payloads (636 frames, two conversations in one file). It exercises the exact
    production decode path, not a peak shortcut.
    """
    assert CONTROL.exists(), CONTROL
    turns, responses, det = replay(CONTROL)
    assert len(responses) == 2, responses
    assert len(turns) == 2, [(t.start_s, t.end_s, t.peak) for t in turns]
    assert det.frames_seen == 636, det.frames_seen
    # Every turn is loud and every carrier frame is quiet, with the margin the
    # calibration claims. carrier_peak_max 2044 vs the 3000 floor.
    assert det.carrier_peak_max == 2044, det.carrier_peak_max
    assert min(t.peak for t in turns) >= 20000, [t.peak for t in turns]
    assert [round(t.start_s, 3) for t in turns] == [0.636, 99.03], turns
    ok("fixture 2a: 636 real PCM frames -> exactly 2 turns, 2 agent_responses, carrier max 2044")


def fixture_2b_turn_count_matches_agent_responses() -> None:
    """Across every voice capture: turns detected == agent_response frames.

    Zero false splits, zero false merges, zero misses. This is the claim the
    whole half-duplex loop rests on — if it ever drifts, we start talking over her.
    """
    expected = {
        "events_turn_chunks.jsonl": 4,     # incl. "Are you still there?" and the hangup line
        "events_turn_silence.jsonl": 2,
        "events_turn_nopong.jsonl": 2,
        "events_silent.jsonl": 1,
    }
    for name, n in expected.items():
        turns, responses, det = replay(PROTO / name)
        assert len(turns) == n, f"{name}: {len(turns)} turns, expected {n}"
        assert len(responses) == n, f"{name}: {len(responses)} agent_responses, expected {n}"
        # event_id pairs the audio turn to its agent_response — 100% on disk, and
        # it is the primary key _pair_agent_response() uses.
        assert [t.event_id for t in turns] == [r["event_id"] for r in responses], name
        assert det.carrier_peak_max <= 2044, f"{name}: carrier drift {det.carrier_peak_max}"
    ok("fixture 2b: 4 captures, 9 turns — turn count and event_ids match agent_responses exactly")


def fixture_2c_no_split_at_28_876() -> None:
    """THE calibration. 0.9 s splits a real turn at el 28.876; 1.5 s does not.

    events_turn_nopong.jsonl frames:
        el 27.958  peak  6180   speech
        el 28.301  peak   663   ONE sub-threshold frame
        el 28.876  peak 12263   speech again
    The wall gap from the last speech frame is 0.918 s — the longest intra-turn
    gap ever measured. A 0.9 s backstop cuts Tara mid-sentence and we speak over
    her; the shipped 1.5 s does not. A single-frame test would be worse still.
    """
    tight, _, _ = replay(PROTO / "events_turn_nopong.jsonl", quiet_wall_s=0.9)
    assert len(tight) == 3, [(t.start_s, t.end_s) for t in tight]
    assert round(tight[1].end_s, 3) == 28.876, tight[1].end_s
    assert round(tight[2].start_s, 3) == 28.876, tight[2].start_s

    shipped, resp, _ = replay(PROTO / "events_turn_nopong.jsonl")
    assert len(shipped) == 2 == len(resp), [(t.start_s, t.end_s) for t in shipped]
    assert all(not (t.start_s < 28.876 < t.end_s and t.end_s == 28.876) for t in shipped)
    spanning = [t for t in shipped if t.start_s <= 27.958 and t.end_s >= 28.956]
    assert len(spanning) == 1, "the real turn must remain ONE turn across el 28.876"
    ok("fixture 2c: quiet_wall 0.9 splits at el 28.876 (3 turns); shipped 1.5 does not (2 turns)")


def fixture_2d_text_mode_carrier_is_never_a_turn() -> None:
    """Negative control: the office1 carrier streams in TEXT mode too, forever.

    If the floor were ever lowered to 'anything above the noise', a text-mode run
    would start hallucinating agent speech turns out of background_sound.
    """
    turns, responses, det = replay(PROTO / "events_text_turn_silence.jsonl")
    assert len(responses) == 2, responses
    assert turns == [], [(t.start_s, t.peak) for t in turns]
    assert det.frames_seen == 337, det.frames_seen
    assert det.carrier_peak_max == 2044, det.carrier_peak_max
    ok("fixture 2d: 337 text-mode carrier frames -> 0 turns (carrier max 2044 < floor 3000)")


def fixture_2e_spike_audio_turn_boundaries() -> None:
    """The named fixture: runs/_spike_audio_turn, reproduced from its real audio.

    events.jsonl for this run carries the control frames only — the spike routed
    audio into the detector without logging the payloads. The audio itself IS on
    disk, frame-aligned, in tara_turn_{0,1,2}.pcm (41/46/29 x 9600 bytes). So the
    stream is rebuilt from those real speech frames, spliced with real carrier
    frames lifted from spike_events_control-audio.jsonl, and pushed through the
    detector. The expected values are the hand-verified ones recorded live in
    result_confirm.json — peaks 25203/25665/23803, speech 12.3/13.8/8.7 s.
    """
    confirm = json.loads((SPIKE / "result_confirm.json").read_text())
    timeline = confirm["timeline"]
    starts = [r for r in timeline if r["kind"] == "SPEECH_START"]
    ends = [r for r in timeline if r["kind"] == "AGENT_TURN_END"]
    assert confirm["agent_turn_peaks"] == [25203, 25665, 23803], confirm["agent_turn_peaks"]

    carrier = [base64.b64decode((p["audio_event"] or {})["audio_base_64"])
               for _, ft, p, pk, _ in read_capture(CONTROL)
               if ft == "audio" and pk < 3000]
    assert len(carrier) > 40, len(carrier)

    det = TurnDetector()
    detected, el, ci = [], 0.0, 0
    for idx, start in enumerate(starts):
        el = start["el"]
        pcm = (SPIKE / f"tara_turn_{idx}.pcm").read_bytes()
        assert len(pcm) % FRAME_BYTES == 0, f"turn {idx} is not frame-aligned"
        for off in range(0, len(pcm), FRAME_BYTES):
            done = det.feed(pcm[off:off + FRAME_BYTES], now=el, event_id=start["event_id"])
            if done is not None:
                detected.append(done)
            el += FRAME_PERIOD_S
        for _ in range(8):                       # the quiet run that ends the turn
            done = det.feed(carrier[ci % len(carrier)], now=el)
            ci += 1
            if done is not None:
                detected.append(done)
            el += FRAME_PERIOD_S
    assert len(detected) == 3, [(d.speech_s, d.peak) for d in detected]
    assert [d.peak for d in detected] == [25203, 25665, 23803], [d.peak for d in detected]
    assert [d.speech_s for d in detected] == [12.3, 13.8, 8.7], [d.speech_s for d in detected]
    assert [d.speech_frames for d in detected] == [41, 46, 29], detected
    assert [d.event_id for d in detected] == [1, 40, 96], detected
    assert [d.event_id for d in detected] == [e["event_id"] for e in ends], ends
    # Either exit is correct and both are seen in the live captures: at the
    # measured ~0.3 s/frame cadence, 5 quiet frames and the 1.5 s wall backstop
    # land on the same frame, so which one fires is decided by network jitter.
    assert all(d.reason in ("quiet_frames", "quiet_wall") for d in detected), detected
    ok("fixture 2e: _spike_audio_turn real PCM -> 3 turns, peaks/speech_s/event_ids exact")


def fixture_2f_agent_response_is_turn_start() -> None:
    """`agent_response` is a turn-START marker. Ending a turn on it truncates ~10 s.

    §0.2 quotes 0.31-0.83 s after the first speech frame and 9-11 s before the
    last. Re-measured here over conv_1901kyekyjtpfmctw94pnefqdfyy the spread is
    WIDER on both ends — lag up to 0.973 s, truncation up to 14.1 s — so the
    spec's bounds are tight, not wrong, and the trap is worse than advertised.
    Asserted against the re-measured range; the finding itself is unchanged and
    is what makes a naive port of the Level 0 target talk over her every turn.
    """
    confirm = json.loads((SPIKE / "result_confirm.json").read_text())
    tl = confirm["timeline"]
    starts = [r["el"] for r in tl if r["kind"] == "SPEECH_START"]
    ends = [r["el"] for r in tl if r["kind"] == "AGENT_TURN_END"]
    resp = [r["el"] for r in tl if r["kind"] == "agent_response"]
    assert len(starts) == len(ends) == len(resp) == 3
    lag = [round(r - s, 3) for r, s in zip(resp, starts)]
    lost = [round(e - r, 3) for e, r in zip(ends, resp)]
    assert all(0.2 <= x <= 1.1 for x in lag), lag
    assert all(9.0 <= x <= 14.5 for x in lost), lost
    assert all(x > 0 for x in lag) and all(x > 8.0 for x in lost)
    ok(f"fixture 2f: agent_response lands +{lag}s after speech start, -{lost}s before turn end")


def fixture_2g_regression_guards() -> None:
    """The two knobs that must not be quietly loosened."""
    try:
        TurnDetector(quiet_frames=1)
    except ValueError:
        pass
    else:
        raise AssertionError("a single-frame end-of-turn test must be rejected")

    det = TurnDetector()
    assert (det.speech_peak_min, det.quiet_frames, det.quiet_wall_s) == (3000, 5, 1.5)
    # The measured worst case: carrier max 2942 vs speech min 3266. Both land on
    # the correct side of the shipped floor, and the margin is only 324 counts —
    # which is exactly why the multi-frame hold, not the floor, does the work.
    assert 2942 < det.speech_peak_min <= 3266
    ok("fixture 2g: quiet_frames>=2 enforced; defaults 3000/5/1.5 straddle the 2942..3266 margin")


# ======================================================================================
# A fake socket — the whole target, offline
# ======================================================================================

def frame_audio(pcm: bytes, event_id: int) -> str:
    return json.dumps({"type": "audio",
                       "audio_event": {"audio_base_64": base64.b64encode(pcm).decode(),
                                       "event_id": event_id, "is_final": False}})


def tone(peak: int, n_bytes: int = FRAME_BYTES) -> bytes:
    """Deterministic PCM at a chosen peak. Used only where real bytes add nothing."""
    n = n_bytes // 2
    return struct.pack(f"<{n}h", *([peak, -peak] * (n // 2)))


class FakeSocket:
    """Scripted server. `send()` may trigger further server frames (the endpointer)."""

    def __init__(self, script: list[tuple[float, str]], *, on_send=None) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.close_calls = 0
        self._q: asyncio.Queue = asyncio.Queue()
        self._on_send = on_send
        self._t0 = time.monotonic()
        self._feeder = asyncio.create_task(self._feed(script))

    async def _feed(self, script: list[tuple[float, str]]) -> None:
        for at, raw in script:
            await asyncio.sleep(max(0.0, self._t0 + at - time.monotonic()))
            await self._q.put(raw)

    def push(self, raw: str) -> None:
        self._q.put_nowait(raw)

    async def recv(self) -> str:
        return await self._q.get()

    async def send(self, raw: str) -> None:
        frame = json.loads(raw)
        self.sent.append(frame)
        if self._on_send is not None:
            self._on_send(self, frame)

    async def close(self) -> None:
        self.closed = True
        self.close_calls += 1
        self._feeder.cancel()


def fake_connect(script, *, on_send=None):
    async def _connect(url, **kw):
        assert kw["ping_interval"] is None, "ping_interval MUST be None (§1.1)"
        assert kw["additional_headers"]["xi-api-key"], "xi-api-key header is the auth path"
        return FakeSocket(script, on_send=on_send)
    return _connect


SCENARIO_VARS = {
    "subscriber_name": "Aravinth", "call_reason": "win_back",
    "call_intro": "your plan lapsed", "plan_name": "JioHotstar Super (annual)",
    "amount_inr": "1499", "expiry_date": "20 June", "content_hook": "the cricket",
    "offer_text": "10% off", "renewal_date": "", "next_retry_date": "",
    "failure_reason": "",
}

META = json.dumps({"type": "conversation_initiation_metadata",
                   "conversation_initiation_metadata_event": {
                       "conversation_id": "conv_offline_0001",
                       "agent_output_audio_format": "pcm_16000",
                       "user_input_audio_format": "pcm_16000",
                       "persistent_session_token": None}})


def opening_script(*, extra: list[tuple[float, str]] | None = None) -> list[tuple[float, str]]:
    """Metadata, a ping, then one real 12.3 s agent turn out of the capture."""
    pcm = (SPIKE / "tara_turn_0.pcm").read_bytes()
    script: list[tuple[float, str]] = [(0.0, META)]
    at = 0.02
    script.append((at, json.dumps({"type": "ping", "ping_event": {"event_id": 2, "ping_ms": None}})))
    at += 0.01
    script.append((at, json.dumps({"type": "agent_response",
                                   "agent_response_event": {
                                       "agent_response": "Hi Aravinth, this is Tara from JioHotstar.",
                                       "event_id": 1}})))
    # Real speech frames, replayed fast — the detector's clock is the frame clock.
    for off in range(0, len(pcm), FRAME_BYTES):
        at += 0.004
        script.append((at, frame_audio(pcm[off:off + FRAME_BYTES], 1)))
    for _ in range(6):                                   # the quiet run that ends it
        at += 0.004
        script.append((at, frame_audio(tone(400), 1)))
    if extra:
        script.extend((at + 0.05 + d, raw) for d, raw in extra)
    return script


async def scenario_deadlock() -> None:
    """FIXTURE 5 — no user_transcript is ever sent; `no_user_transcript` must fire.

    THE quietest failure in the system: `speak()` without the hold works for
    exactly one turn per conversation and then deadlocks forever. The socket
    stays healthy, nothing errors, the run just looks short. Two full
    conversations were burned discovering that. This assertion is the tripwire.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        target = ElevenLabsAudioTarget(
            api_key="k", agent_id="a", raw_log_path=tmp / "raw.jsonl",
            audio_dir=tmp / "audio",
            tts=_FakeTTS(seconds=0.4),
            connect=fake_connect(opening_script()),
        )
        assert target.mic_hold_bound_s == MIC_HOLD_BOUND_S == 8.0
        await target.open(SCENARIO_VARS)
        turn = await target.recv_agent_turn(20.0)
        assert turn.audio_meta["speech_s"] == 12.3, turn.audio_meta

        t0 = time.monotonic()
        try:
            await target.send_persona_turn("English please.")
        except MicHoldTimeout as e:
            held = time.monotonic() - t0
            assert e.code == "no_user_transcript", e.code
            assert "turn-2 deadlock" in str(e)
            # The bound is measured from the last real audio chunk, so the total
            # is the utterance (0.4 s + 0.3 s lead) plus the 8 s hold.
            assert 8.0 <= held <= 9.6, f"held {held:.2f}s, expected the 8.0 s bound"
            assert target.audio_chunks_sent >= 80, target.audio_chunks_sent
        else:
            raise AssertionError("no_user_transcript did not fire — the deadlock is back")
        await target.close("error")
    ok(f"fixture 5: no user_transcript -> no_user_transcript at the {MIC_HOLD_BOUND_S} s bound")


class _FakeTTSResult:
    """Duck-typed `speech.sarvam_speech.TTSResult` — only the fields the target reads."""

    def __init__(self, pcm: bytes, speaker: str | None) -> None:
        self.pcm = pcm
        self.sample_rate = SAMPLE_RATE
        self.model = "bulbul:v2"
        self.speaker = speaker or "anushka"
        self.latency_ms = 1080
        self.chars = 0


class _FakeTTS:
    def __init__(self, seconds: float = 0.4) -> None:
        self.seconds = seconds
        self.calls: list[tuple[str, str | None, int | None]] = []

    async def synthesize(self, text, *, speaker=None, sample_rate=None,
                         pace=None, language_code=None) -> _FakeTTSResult:
        self.calls.append((text, speaker, sample_rate))
        res = _FakeTTSResult(tone(12000, int(SAMPLE_RATE * 2 * self.seconds) // 2 * 2), speaker)
        res.chars = len(text)
        return res


async def scenario_full_turn() -> None:
    """The happy path end to end: open -> her turn -> our turn -> her reply.

    Also pins the wire contract the spec is emphatic about: the init frame shape,
    the bare `user_audio_chunk` key, 3200-byte chunks, and the fact that NOTHING
    is streamed between turns.
    """
    tts = _FakeTTS(seconds=1.0)
    reply_pcm = (SPIKE / "tara_turn_1.pcm").read_bytes()

    state = {"chunks": 0, "transcript_sent": False}

    def on_send(sock: FakeSocket, frame: dict) -> None:
        if "user_audio_chunk" not in frame:
            return
        assert "type" not in frame, "user_audio_chunk MUST be a BARE top-level key"
        assert len(base64.b64decode(frame["user_audio_chunk"])) == CHUNK_BYTES
        state["chunks"] += 1
        # scribe_realtime endpoints us ~2 s of silence after the last real chunk;
        # here: a few chunks into the hold.
        if state["chunks"] >= 16 and not state["transcript_sent"]:
            state["transcript_sent"] = True
            sock.push(json.dumps({"type": "user_transcript",
                                  "user_transcription_event": {
                                      "user_transcript": "English, please. 1,499 is too much here.",
                                      "event_id": 40}}))
            sock.push(json.dumps({"type": "agent_response",
                                  "agent_response_event": {
                                      "agent_response": "Got it, English it is. I can do 10% off.",
                                      "event_id": 40}}))
            for off in range(0, len(reply_pcm), FRAME_BYTES):
                sock.push(frame_audio(reply_pcm[off:off + FRAME_BYTES], 40))
            for _ in range(6):
                sock.push(frame_audio(tone(400), 40))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        target = ElevenLabsAudioTarget(
            api_key="k", agent_id="a", raw_log_path=tmp / "raw.jsonl",
            audio_dir=tmp / "audio" / "price-haggler",
            tts=tts, connect=fake_connect(opening_script(), on_send=on_send),
        )
        cid = await target.open(SCENARIO_VARS)
        assert cid == "conv_offline_0001"
        assert target.text_only_override_sent is False, "voice mode sends NO override"

        opening = await target.recv_agent_turn(20.0)
        assert isinstance(opening, (AgentTurn, AudioAgentTurn))
        assert opening.text == "Hi Aravinth, this is Tara from JioHotstar."
        assert opening.audio_meta["text_provenance"] == "agent_emitted"
        assert opening.audio_meta["peak"] == 25203, opening.audio_meta
        assert opening.audio_meta["speech_frames"] == 41
        # `audio_path` is RELATIVE to the run directory ("audio/<persona>/turn_N_who.pcm"),
        # per LEVEL1_SPEC §3.2 — an artifact must not carry one machine's absolute paths.
        # So resolve it the way any consumer has to: against the run dir, not the cwd.
        # This assertion used to open the value directly, which only worked while the
        # producer was leaking absolute paths.
        run_dir = tmp          # the target was given audio_dir = tmp/"audio"/"price-haggler"
        rel = opening.audio_meta["audio_path"]
        assert not Path(rel).is_absolute(), f"audio_path must be relative, got {rel}"
        assert (run_dir / rel).stat().st_size == 393600

        before = target.audio_chunks_sent
        result = await target.send_persona_turn("English please. Fourteen ninety nine is too much.")
        assert result.meta["text_provenance"] == "persona_intended"
        assert result.meta["endpointed_while_mic_open"] is True
        assert result.meta["tara_heard"]["provenance"] == "asr"
        assert result.meta["tara_heard"]["event_id"] == 40
        assert result.meta["chunks_sent"] == 13, result.meta   # 0.3 s lead + 1.0 s speech
        assert 0 < result.meta["hold_chunks"] < 80, result.meta
        assert result.meta["mic_hold_s"] < MIC_HOLD_BOUND_S
        assert abs(result.meta["pacing_drift_s"]) < 0.25, result.meta["pacing_drift_s"]
        # `speech_sample_rate` is ALWAYS sent: Bulbul defaults to 22050 and Tara
        # then plays us at the wrong speed with no error anywhere.
        assert tts.calls == [("English please. Fourteen ninety nine is too much.",
                              "anushka", SAMPLE_RATE)], tts.calls
        assert result.meta["tts"] == {"model": "bulbul:v2", "speaker": "anushka",
                                      "synth_ms": 1080, "chars": 49,
                                      "sample_rate": 16000}, result.meta["tts"]

        reply = await target.recv_agent_turn(20.0)
        assert reply.text == "Got it, English it is. I can do 10% off."
        assert reply.event_id == 40
        assert reply.audio_meta["peak"] == 25665, reply.audio_meta
        assert reply.audio_meta["speech_s"] == 13.8

        # NOT ONE BYTE goes out between turns. Streaming silence as a keepalive is
        # the one arm that died — empty user turns -> "Are you still there?" ->
        # end_call -> close at 59 s, faster than the problem it addressed.
        quiet_window = target.audio_chunks_sent
        await asyncio.sleep(0.4)
        assert target.audio_chunks_sent == quiet_window, "silence streamed between turns"

        sock = target._ws
        init_frames = [f for f in sock.sent if f.get("type") == "conversation_initiation_client_data"]
        assert len(init_frames) == 1
        assert "dynamic_variables" in init_frames[0], "dynamic_variables must be TOP-LEVEL"
        assert "conversation_config_override" not in init_frames[0], \
            "voice mode omits text_only entirely — not text_only:false"
        assert init_frames[0]["dynamic_variables"]["amount_inr"] == "1499"
        pongs = [f for f in sock.sent if f.get("type") == "pong"]
        assert pongs and pongs[0]["event_id"] == 2 and target.pongs_sent == target.pings_received

        assert target.audio_chunks_sent > before
        assert target.agent_turns == 2
        assert target.conversation_over is False
        await target.close("turns_over")
        assert sock.closed and target._log_fh is None
    ok("full turn: agent_response text verbatim, real peaks, bare audio key, silence only in the hold")


async def scenario_pongs_survive_compute() -> None:
    """§0.1: the reader pongs through a long compute block that owns no socket.

    This is Level 0's actual bug, reproduced as a test: `recv_agent_turn()`
    returns, the caller disappears for tens of seconds doing LLM work, and in
    Level 0 nobody is left reading the socket, so the server 1002s us with a
    message about user silence that is not what happened.
    """
    pings = [(0.05 + i * 0.05, json.dumps({"type": "ping",
                                           "ping_event": {"event_id": 100 + i, "ping_ms": 300}}))
             for i in range(12)]
    with tempfile.TemporaryDirectory() as tmp:
        target = ElevenLabsAudioTarget(
            api_key="k", agent_id="a", raw_log_path=Path(tmp) / "raw.jsonl",
            connect=fake_connect(opening_script(extra=pings)),
        )
        await target.open(SCENARIO_VARS)
        await target.recv_agent_turn(20.0)
        # Simulate persona.reply(): the event loop is free, but the caller is gone.
        await asyncio.sleep(1.0)
        assert target.pings_received >= 13, target.pings_received
        assert target.pongs_sent == target.pings_received, (target.pongs_sent,
                                                            target.pings_received)
        await target.close("done")
    ok(f"reader stays live: {target.pongs_sent} pongs sent with no caller reading the socket")


async def scenario_end_call() -> None:
    """`agent_tool_response(end_call)` is a real ending, not a disconnect.

    Level 0 had no hangup signal at all and would have recorded this as
    `target_disconnected`. schema.py's `agent_ended_call` exists for exactly this.
    """
    tool = json.dumps({"type": "agent_tool_response",
                       "agent_tool_response": {"tool_name": "end_call", "tool_type": "system",
                                               "tool_call_id": "call_x", "is_error": False,
                                               "is_blocked": False, "is_called": True,
                                               "event_id": 178}})
    with tempfile.TemporaryDirectory() as tmp:
        target = ElevenLabsAudioTarget(
            api_key="k", agent_id="a", raw_log_path=Path(tmp) / "raw.jsonl",
            connect=fake_connect(opening_script(extra=[(0.05, tool)])),
        )
        await target.open(SCENARIO_VARS)
        await target.recv_agent_turn(20.0)
        await asyncio.sleep(0.2)
        assert target.conversation_over is True
        assert target.end_call_evidence["tool_name"] == "end_call"
        assert target.end_call_evidence["is_called"] is True
        assert target.end_call_evidence["tool_type"] == "system"
        await target.close("agent_ended_call")
    ok("end_call: conversation_over set with the tool frame kept as end_reason evidence")


async def scenario_guards() -> None:
    """Ordering and configuration guards that a spike would only find in production."""
    t = ElevenLabsAudioTarget(api_key="k", agent_id="a",
                              connect=fake_connect(opening_script()))
    try:
        await t.send_persona_turn("hello")
    except Exception as e:
        assert "before open()" in str(e), e
    else:
        raise AssertionError("send before open() must fail")

    await t.open(SCENARIO_VARS)
    try:
        await t.send_persona_turn("hello")
    except Exception as e:
        assert "speaks first" in str(e), e
    else:
        raise AssertionError("the agent speaks FIRST — speaking first must fail")
    await t.close("guard")

    try:
        ElevenLabsAudioTarget(api_key="k", agent_id="a", mic_hold_bound_s=12.0)
    except ValueError as e:
        assert "8.0 s ceiling" in str(e)
    else:
        raise AssertionError("mic_hold_bound_s > 8 must be refused")

    try:
        ElevenLabsAudioTarget(api_key="k", agent_id="a", auth="signed")
    except ValueError as e:
        assert "header auth" in str(e)
    else:
        raise AssertionError("auth='signed' is unprobed in voice mode and must be refused")

    bad_meta = json.dumps({"type": "conversation_initiation_metadata",
                           "conversation_initiation_metadata_event": {
                               "conversation_id": "c", "agent_output_audio_format": "pcm_16000",
                               "user_input_audio_format": "ulaw_8000"}})
    t2 = ElevenLabsAudioTarget(api_key="k", agent_id="a",
                               connect=fake_connect([(0.0, bad_meta)]))
    try:
        await t2.open(SCENARIO_VARS)
    except Exception as e:
        assert "user_input_audio_format" in str(e), e
    else:
        raise AssertionError("a format mismatch must not be silently accepted")
    await t2.close("guard")

    t3 = ElevenLabsAudioTarget(api_key="k", agent_id="a",
                               connect=fake_connect(opening_script()))
    await t3.open(SCENARIO_VARS)
    try:
        await t3.recv_agent_turn(20.0)
        await t3.open(SCENARIO_VARS)
    except Exception as e:
        assert "twice" in str(e), e
    else:
        raise AssertionError("open() twice must fail")
    await t3.close("guard")
    ok("guards: open/send ordering, the 8 s ceiling, header-only auth, format assertion")


async def scenario_raw_log_replays() -> None:
    """The raw log this target writes must feed straight back into the detector.

    Audio payloads are logged as `<9600 bytes, peak=NNN>` rather than discarded,
    so every production run is its own regression fixture and any carrier drift
    (background_sound is server config we do not control) is visible before it
    flips a verdict.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "raw.jsonl"
        target = ElevenLabsAudioTarget(api_key="k", agent_id="a", raw_log_path=path,
                                       connect=fake_connect(opening_script()))
        await target.open(SCENARIO_VARS)
        await target.recv_agent_turn(20.0)
        await target.close("done")

        turns, responses, det = replay(path)
        assert len(responses) == 1, responses
        assert len(turns) == 1, turns
        assert turns[0].peak == 25203, turns[0].peak
        # 41 speech + the 5 quiet frames that ended the turn. The 6th never lands:
        # recv_agent_turn() returns the instant the turn is published and close()
        # cancels the reader — which is also the teardown-order check.
        assert det.frames_seen == 46, det.frames_seen
        body = path.read_text()
        assert "audio_base_64" in body and "peak=25203" in body
        assert '"peak": 25203' in body, "the per-turn peak must be logged (§9.4)"
        assert "AAAAAAAA" not in body, "raw audio payloads must never reach the log"
    ok("raw log: audio logged as peaks — replays through the detector, no payloads on disk")


def check_speech_interface() -> None:
    """The target binds to speech/sarvam_speech.py by NAME. Pin that, offline.

    Nothing here touches the network — it only asserts that the call shape the
    target compiled against is the call shape that module actually exposes, so a
    signature drift fails here instead of on a live conversation.
    """
    try:
        from speech.sarvam_speech import BulbulTTS, SaarasSTT, TTSConfig
    except ImportError as e:
        print(f"  --  speech.sarvam_speech not importable yet ({e}); "
              "the target falls back to an injected tts=/stt= and this check is skipped")
        return

    syn = inspect.signature(BulbulTTS.synthesize).parameters
    for name in ("text", "speaker", "sample_rate", "language_code"):
        assert name in syn, f"BulbulTTS.synthesize lost `{name}`: {list(syn)}"
    assert inspect.iscoroutinefunction(BulbulTTS.synthesize)

    tra = inspect.signature(SaarasSTT.transcribe).parameters
    for name in ("audio", "sample_rate"):
        assert name in tra, f"SaarasSTT.transcribe lost `{name}`: {list(tra)}"
    assert inspect.iscoroutinefunction(SaarasSTT.transcribe)

    cfg = TTSConfig(sample_rate=SAMPLE_RATE, model="bulbul:v2", speaker="anushka")
    assert cfg.sample_rate == 16000, "16 kHz is mandatory — 22050 is silent corruption"
    ok("speech interface: BulbulTTS.synthesize / SaarasSTT.transcribe match the call sites")


# ======================================================================================

async def main() -> int:
    print("smoke_audio_offline — LEVEL1_SPEC §10 step 2 (zero network, zero quota)\n")
    print("FIXTURE 2 — turn detector replay over real captures")
    fixture_2a_real_bytes()
    fixture_2b_turn_count_matches_agent_responses()
    fixture_2c_no_split_at_28_876()
    fixture_2d_text_mode_carrier_is_never_a_turn()
    fixture_2e_spike_audio_turn_boundaries()
    fixture_2f_agent_response_is_turn_start()
    fixture_2g_regression_guards()

    print("\nTARGET — reader, mic hold, teardown")
    check_speech_interface()
    await scenario_full_turn()
    await scenario_pongs_survive_compute()
    await scenario_end_call()
    await scenario_guards()
    await scenario_raw_log_replays()

    print(f"\nFIXTURE 5 — the deadlock assertion (takes {MIC_HOLD_BOUND_S:.0f}s: it is a real bound)")
    await scenario_deadlock()

    print(f"\n{len(PASSED)} checks passed. Fixtures 2 and 5 hold.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
