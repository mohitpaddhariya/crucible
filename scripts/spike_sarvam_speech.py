#!/usr/bin/env python3
"""
spike_sarvam_speech.py — LEVEL 1 HARD GATE (audio I/O).

Prove Sarvam's speech APIs work and pin down EVERY format detail that
half-duplex audio (Level 1) depends on. Nothing here is assumed from docs;
everything is called against the live API and the response is introspected.

Probes, in order:
  A. ElevenLabs agent audio config      (read-only GET; what Tara accepts/emits)
  B. Bulbul TTS  — endpoint, auth style, model id, response envelope
  C. Bulbul TTS  — speaker roster (enumerated from the API's own 400 message)
  D. Bulbul TTS  — one short Hinglish line, saved to runs/_spike/, WAV introspected
  E. Bulbul TTS  — sample-rate matrix (which rates are actually accepted)
  F. Bulbul TTS  — streaming (WebSocket) availability
  G. Saaras STT  — endpoint, model id, round-trip the Bulbul output
  H. Saaras STT  — timestamps / confidence surface
  I. Saaras STT  — input format + sample-rate acceptance
  J. Saaras STT  — streaming availability

COST DISCIPLINE: exactly ONE full-length synthesis of the canonical line is
billed at normal length (probe D). Every other TTS call uses a 3-word string.
Probe C's calls are deliberate 400s — they synthesise nothing.

NEVER touches the ElevenLabs agent with anything but a GET.
NEVER prints a key value.

Run:
    cd /Users/mohitpaddhariya/flagship-projects/voice-spar
    uv run --python 3.12 --with httpx --with websockets python scripts/spike_sarvam_speech.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import struct
import sys
import time
import wave
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "runs" / "_spike"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = OUT_DIR / "sarvam_speech_result.json"

SARVAM_HOST = "https://api.sarvam.ai"
EL_HOST = "https://api.elevenlabs.io"

# The canonical probe line. 11 words / 52 chars — a realistic persona turn.
HINGLISH_LINE = "Arre yaar, 10% off is not enough for the cricket."
TINY_LINE = "Theek hai."  # for cheap format probes

REPORT: dict[str, Any] = {"probes": {}}


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k in env or k.endswith("_KEY") or k.endswith("_ID")})
    return env


ENV = load_env()
SARVAM_KEY = ENV.get("SARVAM_API_KEY", "")
EL_KEY = ENV.get("ELEVENLABS_API_KEY", "")
EL_AGENT = ENV.get("ELEVENLABS_AGENT_ID", "")

if not SARVAM_KEY:
    print("FATAL: SARVAM_API_KEY missing from .env / environment", file=sys.stderr)
    sys.exit(2)


def say(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def brief(obj: Any, limit: int = 400) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"… (+{len(s) - limit} chars)"


def describe_audio(raw: bytes) -> dict[str, Any]:
    """Introspect the returned bytes. Never trust the docs about the container."""
    info: dict[str, Any] = {"n_bytes": len(raw), "first_16_hex": raw[:16].hex()}
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        info["container"] = "WAV (RIFF)"
        try:
            with wave.open(io.BytesIO(raw), "rb") as w:
                info["channels"] = w.getnchannels()
                info["sample_width_bytes"] = w.getsampwidth()
                info["bits_per_sample"] = w.getsampwidth() * 8
                info["sample_rate_hz"] = w.getframerate()
                info["n_frames"] = w.getnframes()
                info["duration_s"] = round(w.getnframes() / w.getframerate(), 4)
                frames = w.readframes(w.getnframes())
            if info.get("sample_width_bytes") == 2:
                n = len(frames) // 2
                vals = struct.unpack(f"<{n}h", frames[: n * 2])
                info["peak_abs_amplitude"] = max(abs(v) for v in vals) if vals else 0
                info["peak_pct_full_scale"] = round(
                    100 * (info["peak_abs_amplitude"] / 32768.0), 2
                )
            # sub-chunk walk: does the header carry anything unusual?
            chunks, pos = [], 12
            while pos + 8 <= len(raw):
                cid = raw[pos : pos + 4].decode("ascii", "replace")
                csz = int.from_bytes(raw[pos + 4 : pos + 8], "little")
                chunks.append({"id": cid, "size": csz})
                pos += 8 + csz + (csz % 2)
            info["riff_chunks"] = chunks
            info["header_bytes_before_data"] = next(
                (
                    sum(8 + c["size"] + (c["size"] % 2) for c in chunks[:i]) + 12 + 8
                    for i, c in enumerate(chunks)
                    if c["id"] == "data"
                ),
                None,
            )
        except Exception as e:  # noqa: BLE001
            info["wave_parse_error"] = repr(e)
    elif raw[:3] == b"ID3" or raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        info["container"] = "MP3"
    elif raw[:4] == b"OggS":
        info["container"] = "OGG"
    elif raw[:4] == b"fLaC":
        info["container"] = "FLAC"
    else:
        info["container"] = "UNKNOWN / headerless (raw PCM?)"
    return info


def unwrap_audio(payload: Any) -> tuple[bytes | None, str]:
    """Find the audio inside whatever envelope came back. Report the shape."""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload), "raw HTTP body (not JSON)"
    if isinstance(payload, dict):
        for key in ("audios", "audio", "audio_base64", "audio_base_64", "data"):
            if key in payload and payload[key]:
                v = payload[key]
                if isinstance(v, list):
                    return base64.b64decode(v[0]), f"JSON, base64 in list `{key}[0]`"
                if isinstance(v, str):
                    return base64.b64decode(v), f"JSON, base64 in string `{key}`"
    return None, f"could not locate audio; keys={list(payload) if isinstance(payload, dict) else type(payload)}"


# ─────────────────────────────────────────────────────────────────────────────
# A. ElevenLabs agent audio config  (READ-ONLY GET — never modify)
# ─────────────────────────────────────────────────────────────────────────────
def probe_a_elevenlabs_audio_config(client: httpx.Client) -> None:
    say("A. ElevenLabs agent audio config (GET, read-only)")
    out: dict[str, Any] = {}
    if not (EL_KEY and EL_AGENT):
        out["skipped"] = "ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID missing"
        REPORT["probes"]["A_elevenlabs_audio_config"] = out
        print(out["skipped"])
        return
    try:
        r = client.get(
            f"{EL_HOST}/v1/convai/agents/{EL_AGENT}",
            headers={"xi-api-key": EL_KEY},
            timeout=30,
        )
        out["status"] = r.status_code
        if r.status_code == 200:
            cfg = r.json().get("conversation_config", {})
            asr = cfg.get("asr", {}) or {}
            tts = cfg.get("tts", {}) or {}
            conv = cfg.get("conversation", {}) or {}
            out["asr"] = {
                k: asr.get(k)
                for k in ("quality", "provider", "user_input_audio_format", "keywords")
            }
            out["tts"] = {
                k: tts.get(k)
                for k in (
                    "model_id",
                    "voice_id",
                    "agent_output_audio_format",
                    "optimize_streaming_latency",
                    "stability",
                    "speed",
                )
            }
            out["conversation"] = {
                k: conv.get(k) for k in ("text_only", "max_duration_seconds", "client_events")
            }
            # what may be overridden at runtime
            ov = (
                r.json()
                .get("platform_settings", {})
                .get("overrides", {})
                .get("conversation_config_override", {})
            )
            out["overrides_allowed"] = ov
            print(json.dumps({k: out[k] for k in ("asr", "tts", "conversation")}, indent=2))
        else:
            out["body"] = brief(r.text)
            print(f"  HTTP {r.status_code}: {brief(r.text, 200)}")
    except Exception as e:  # noqa: BLE001
        out["error"] = repr(e)
        print(f"  ERROR {e!r}")
    REPORT["probes"]["A_elevenlabs_audio_config"] = out


# ─────────────────────────────────────────────────────────────────────────────
# B. Bulbul TTS — endpoint / auth / model discovery
# ─────────────────────────────────────────────────────────────────────────────
TTS_PATHS = ["/text-to-speech", "/v1/text-to-speech"]
AUTH_STYLES = {
    "api-subscription-key": lambda k: {"api-subscription-key": k},
    "Authorization: Bearer": lambda k: {"Authorization": f"Bearer {k}"},
}


def tts_call(
    client: httpx.Client,
    *,
    path: str,
    auth: str,
    body: dict[str, Any],
    timeout: float = 90.0,
) -> tuple[int, Any, float, dict[str, str]]:
    headers = AUTH_STYLES[auth](SARVAM_KEY)
    headers["Content-Type"] = "application/json"
    t0 = time.perf_counter()
    r = client.post(f"{SARVAM_HOST}{path}", headers=headers, json=body, timeout=timeout)
    dt = time.perf_counter() - t0
    ct = r.headers.get("content-type", "")
    if "application/json" in ct:
        try:
            payload = r.json()
        except Exception:  # noqa: BLE001
            payload = r.text
    elif ct.startswith("audio/") or ct == "application/octet-stream":
        payload = r.content
    else:
        payload = r.text
    return r.status_code, payload, dt, dict(r.headers)


def probe_b_tts_endpoint(client: httpx.Client) -> dict[str, Any]:
    say("B. Bulbul TTS — endpoint, auth style, model id")
    out: dict[str, Any] = {"attempts": []}
    working: dict[str, Any] | None = None

    for path in TTS_PATHS:
        for auth in AUTH_STYLES:
            for model in ("bulbul:v2", "bulbul:v1"):
                body = {
                    "text": TINY_LINE,
                    "target_language_code": "hi-IN",
                    "speaker": "anushka" if model == "bulbul:v2" else "meera",
                    "model": model,
                }
                try:
                    code, payload, dt, hdrs = tts_call(client, path=path, auth=auth, body=body)
                except Exception as e:  # noqa: BLE001
                    out["attempts"].append(
                        {"path": path, "auth": auth, "model": model, "error": repr(e)}
                    )
                    print(f"  {path:22s} {auth:22s} {model:10s} -> EXC {e!r}")
                    continue
                rec = {
                    "path": path,
                    "auth": auth,
                    "model": model,
                    "status": code,
                    "latency_s": round(dt, 3),
                    "content_type": hdrs.get("content-type"),
                    "body_preview": brief(payload if not isinstance(payload, bytes) else f"<{len(payload)} bytes>", 220),
                }
                out["attempts"].append(rec)
                print(f"  {path:22s} {auth:22s} {model:10s} -> {code} ({dt:.2f}s) {rec['body_preview'][:120]}")
                if code == 200 and working is None:
                    audio, shape = unwrap_audio(payload)
                    working = {
                        "path": path,
                        "auth": auth,
                        "model": model,
                        "envelope": shape,
                        "response_keys": list(payload) if isinstance(payload, dict) else None,
                    }
                    if audio:
                        working["tiny_audio"] = describe_audio(audio)
                if code == 200:
                    # don't burn quota re-proving a working combo across models
                    break
    out["working"] = working
    REPORT["probes"]["B_tts_endpoint"] = out
    print(f"\n  WORKING COMBO: {json.dumps(working, indent=2) if working else 'NONE'}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# C. Bulbul TTS — speaker roster, enumerated from the API's own error
# ─────────────────────────────────────────────────────────────────────────────
def probe_c_speakers(client: httpx.Client, combo: dict[str, Any]) -> None:
    say("C. Bulbul TTS — speaker roster (from the API's 400 message; synthesises nothing)")
    out: dict[str, Any] = {}
    for model in ("bulbul:v2", "bulbul:v1"):
        body = {
            "text": TINY_LINE,
            "target_language_code": "hi-IN",
            "speaker": "__nope__",
            "model": model,
        }
        try:
            code, payload, dt, _ = tts_call(
                client, path=combo["path"], auth=combo["auth"], body=body, timeout=30
            )
            out[model] = {"status": code, "message": payload}
            print(f"  {model}: HTTP {code}\n    {brief(payload, 900)}")
        except Exception as e:  # noqa: BLE001
            out[model] = {"error": repr(e)}
            print(f"  {model}: EXC {e!r}")
    # also: does an unsupported language enumerate the language list?
    try:
        code, payload, _, _ = tts_call(
            client,
            path=combo["path"],
            auth=combo["auth"],
            body={
                "text": TINY_LINE,
                "target_language_code": "xx-XX",
                "speaker": "anushka",
                "model": "bulbul:v2",
            },
            timeout=30,
        )
        out["bad_language"] = {"status": code, "message": payload}
        print(f"  bad language: HTTP {code}\n    {brief(payload, 700)}")
    except Exception as e:  # noqa: BLE001
        out["bad_language"] = {"error": repr(e)}
    REPORT["probes"]["C_speakers"] = out


# ─────────────────────────────────────────────────────────────────────────────
# D. Bulbul TTS — the real Hinglish line, saved and measured
# ─────────────────────────────────────────────────────────────────────────────
def probe_d_synthesise(client: httpx.Client, combo: dict[str, Any], speaker: str) -> dict[str, Any]:
    say(f"D. Bulbul TTS — synthesise the canonical Hinglish line (speaker={speaker})")
    out: dict[str, Any] = {
        "text": HINGLISH_LINE,
        "n_chars": len(HINGLISH_LINE),
        "n_words": len(HINGLISH_LINE.split()),
        "speaker": speaker,
    }
    body = {
        "text": HINGLISH_LINE,
        "target_language_code": "hi-IN",
        "speaker": speaker,
        "model": combo["model"],
    }
    try:
        code, payload, dt, hdrs = tts_call(client, path=combo["path"], auth=combo["auth"], body=body)
    except Exception as e:  # noqa: BLE001
        out["error"] = repr(e)
        REPORT["probes"]["D_synthesis"] = out
        print(f"  EXC {e!r}")
        return out

    out["status"] = code
    out["latency_s"] = round(dt, 3)
    out["content_type"] = hdrs.get("content-type")
    out["response_keys"] = list(payload) if isinstance(payload, dict) else None
    if code != 200:
        out["body"] = brief(payload, 600)
        print(f"  HTTP {code}: {out['body']}")
        REPORT["probes"]["D_synthesis"] = out
        return out

    audio, shape = unwrap_audio(payload)
    out["envelope"] = shape
    if audio is None:
        out["error"] = "no audio located in response"
        REPORT["probes"]["D_synthesis"] = out
        return out

    wav_path = OUT_DIR / f"bulbul_{speaker}_hinglish.wav"
    wav_path.write_bytes(audio)
    out["saved_to"] = str(wav_path)
    out["audio"] = describe_audio(audio)
    dur = out["audio"].get("duration_s")
    if dur:
        out["chars_per_second"] = round(len(HINGLISH_LINE) / dur, 2)
        out["words_per_minute"] = round(len(HINGLISH_LINE.split()) / dur * 60, 1)
        out["realtime_factor"] = round(dt / dur, 3)  # <1.0 = faster than realtime
    print(f"  HTTP 200 in {dt:.2f}s -> {wav_path}")
    print(json.dumps({k: v for k, v in out.items() if k != "text"}, indent=2, default=str))
    REPORT["probes"]["D_synthesis"] = out
    return out


# ─────────────────────────────────────────────────────────────────────────────
# E. Bulbul TTS — which sample rates are actually accepted
# ─────────────────────────────────────────────────────────────────────────────
def probe_e_sample_rates(client: httpx.Client, combo: dict[str, Any], speaker: str) -> None:
    say("E. Bulbul TTS — speech_sample_rate matrix (tiny text; cheap)")
    out: dict[str, Any] = {}
    for sr in (8000, 16000, 22050, 24000, 44100, 48000):
        body = {
            "text": TINY_LINE,
            "target_language_code": "hi-IN",
            "speaker": speaker,
            "model": combo["model"],
            "speech_sample_rate": sr,
        }
        try:
            code, payload, dt, _ = tts_call(
                client, path=combo["path"], auth=combo["auth"], body=body, timeout=60
            )
        except Exception as e:  # noqa: BLE001
            out[str(sr)] = {"error": repr(e)}
            print(f"  {sr:6d} -> EXC {e!r}")
            continue
        rec: dict[str, Any] = {"status": code, "latency_s": round(dt, 3)}
        if code == 200:
            audio, _ = unwrap_audio(payload)
            if audio:
                d = describe_audio(audio)
                rec["actual_sample_rate_hz"] = d.get("sample_rate_hz")
                rec["container"] = d.get("container")
                rec["channels"] = d.get("channels")
                rec["bits"] = d.get("bits_per_sample")
                rec["duration_s"] = d.get("duration_s")
        else:
            rec["body"] = brief(payload, 250)
        out[str(sr)] = rec
        print(f"  {sr:6d} -> {code}  {brief(rec, 200)}")
    REPORT["probes"]["E_sample_rates"] = out


# ─────────────────────────────────────────────────────────────────────────────
# F. Bulbul TTS — streaming
# ─────────────────────────────────────────────────────────────────────────────
async def probe_f_tts_streaming(combo: dict[str, Any], speaker: str) -> None:
    say("F. Bulbul TTS — streaming availability")
    out: dict[str, Any] = {}

    # F1: HTTP streaming variants
    import httpx as _httpx

    for path in ("/text-to-speech/streaming", "/text-to-speech/stream"):
        try:
            async with _httpx.AsyncClient() as ac:
                t0 = time.perf_counter()
                r = await ac.post(
                    f"{SARVAM_HOST}{path}",
                    headers={
                        **AUTH_STYLES[combo["auth"]](SARVAM_KEY),
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": TINY_LINE,
                        "target_language_code": "hi-IN",
                        "speaker": speaker,
                        "model": combo["model"],
                    },
                    timeout=45,
                )
                out[f"http {path}"] = {
                    "status": r.status_code,
                    "content_type": r.headers.get("content-type"),
                    "n_bytes": len(r.content),
                    "latency_s": round(time.perf_counter() - t0, 3),
                    "body": brief(r.text if not r.headers.get("content-type", "").startswith("audio") else "<audio>", 250),
                }
        except Exception as e:  # noqa: BLE001
            out[f"http {path}"] = {"error": repr(e)}
        print(f"  http {path} -> {brief(out[f'http {path}'], 220)}")

    # F2: WebSocket streaming
    try:
        import websockets
    except ImportError:
        out["ws"] = {"error": "websockets not importable"}
        REPORT["probes"]["F_tts_streaming"] = out
        return

    ws_urls = [
        f"wss://api.sarvam.ai/text-to-speech/ws?model={combo['model']}",
        f"wss://api.sarvam.ai/v1/text-to-speech/ws?model={combo['model']}",
    ]
    for url in ws_urls:
        rec: dict[str, Any] = {"url": url.split("?")[0]}
        try:
            hdrs = AUTH_STYLES[combo["auth"]](SARVAM_KEY)
            kw = {"ping_interval": None, "open_timeout": 20, "max_size": 16 * 1024 * 1024}
            try:
                ws = await websockets.connect(url, additional_headers=hdrs, **kw)
            except TypeError:
                ws = await websockets.connect(url, extra_headers=hdrs, **kw)
            async with ws:
                rec["connected"] = True
                t0 = time.perf_counter()
                await ws.send(
                    json.dumps(
                        {
                            "type": "config",
                            "data": {
                                "speaker": speaker,
                                "target_language_code": "hi-IN",
                                "output_audio_codec": "wav",
                            },
                        }
                    )
                )
                await ws.send(json.dumps({"type": "text", "data": {"text": HINGLISH_LINE}}))
                await ws.send(json.dumps({"type": "flush"}))
                frames, first_audio_s, total_audio_bytes = [], None, 0
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=20)
                        if isinstance(msg, bytes):
                            total_audio_bytes += len(msg)
                            if first_audio_s is None:
                                first_audio_s = time.perf_counter() - t0
                            frames.append({"binary": len(msg)})
                            continue
                        ev = json.loads(msg)
                        et = ev.get("type")
                        b64 = (ev.get("data") or {}).get("audio") if isinstance(ev.get("data"), dict) else None
                        if b64:
                            if first_audio_s is None:
                                first_audio_s = time.perf_counter() - t0
                            total_audio_bytes += len(base64.b64decode(b64))
                            frames.append({"type": et, "audio_bytes": len(base64.b64decode(b64))})
                        else:
                            frames.append({"type": et, "keys": list(ev)})
                        if len(frames) > 60:
                            break
                except asyncio.TimeoutError:
                    rec["ended"] = "recv timeout (no more frames)"
                except Exception as e:  # noqa: BLE001
                    rec["ended"] = repr(e)
                rec["n_frames"] = len(frames)
                rec["first_frames"] = frames[:6]
                rec["time_to_first_audio_s"] = round(first_audio_s, 3) if first_audio_s else None
                rec["total_audio_bytes"] = total_audio_bytes
        except Exception as e:  # noqa: BLE001
            rec["error"] = repr(e)
        out[f"ws {url.split('?')[0]}"] = rec
        print(f"  {url.split('?')[0]} -> {brief(rec, 400)}")
        if rec.get("connected"):
            break
    REPORT["probes"]["F_tts_streaming"] = out


# ─────────────────────────────────────────────────────────────────────────────
# G/H/I. Saaras STT
# ─────────────────────────────────────────────────────────────────────────────
STT_PATHS = ["/speech-to-text", "/v1/speech-to-text"]


def stt_call(
    client: httpx.Client,
    *,
    path: str,
    auth: str,
    wav_bytes: bytes,
    filename: str = "probe.wav",
    mime: str = "audio/wav",
    data: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> tuple[int, Any, float]:
    headers = AUTH_STYLES[auth](SARVAM_KEY)
    files = {"file": (filename, wav_bytes, mime)}
    t0 = time.perf_counter()
    r = client.post(
        f"{SARVAM_HOST}{path}", headers=headers, files=files, data=data or {}, timeout=timeout
    )
    dt = time.perf_counter() - t0
    try:
        payload = r.json()
    except Exception:  # noqa: BLE001
        payload = r.text
    return r.status_code, payload, dt


def probe_g_stt(client: httpx.Client, wav_bytes: bytes) -> dict[str, Any]:
    say("G. Saaras STT — endpoint, auth, model id; round-trip the Bulbul output")
    out: dict[str, Any] = {"attempts": [], "source_text": HINGLISH_LINE}
    working: dict[str, Any] | None = None
    models = ["saarika:v2.5", "saarika:v2", "saarika:v1", "saarika:flash"]
    for path in STT_PATHS:
        for auth in AUTH_STYLES:
            for model in models:
                try:
                    code, payload, dt = stt_call(
                        client,
                        path=path,
                        auth=auth,
                        wav_bytes=wav_bytes,
                        data={"model": model, "language_code": "hi-IN"},
                    )
                except Exception as e:  # noqa: BLE001
                    out["attempts"].append({"path": path, "auth": auth, "model": model, "error": repr(e)})
                    print(f"  {path:22s} {auth:22s} {model:14s} -> EXC {e!r}")
                    continue
                rec = {
                    "path": path,
                    "auth": auth,
                    "model": model,
                    "status": code,
                    "latency_s": round(dt, 3),
                    "response": payload if code == 200 else brief(payload, 300),
                }
                out["attempts"].append(rec)
                print(f"  {path:22s} {auth:22s} {model:14s} -> {code} ({dt:.2f}s) {brief(payload, 200)}")
                if code == 200 and working is None:
                    working = {
                        "path": path,
                        "auth": auth,
                        "model": model,
                        "response_keys": list(payload) if isinstance(payload, dict) else None,
                        "transcript": payload.get("transcript") if isinstance(payload, dict) else None,
                        "latency_s": round(dt, 3),
                    }
                if code == 200:
                    break  # a working model found for this path/auth; don't burn more
            if working and working["auth"] == auth and working["path"] == path:
                break
        if working:
            break
    out["working"] = working
    REPORT["probes"]["G_stt"] = out
    print(f"\n  WORKING STT: {json.dumps(working, indent=2, ensure_ascii=False) if working else 'NONE'}")
    return out


def probe_h_stt_confidence(client: httpx.Client, combo: dict[str, Any], wav_bytes: bytes) -> None:
    say("H. Saaras STT — does it return confidence / per-word timings?")
    out: dict[str, Any] = {}
    variants = [
        ("with_timestamps=true", {"model": combo["model"], "language_code": "hi-IN", "with_timestamps": "true"}),
        ("with_diarization=true", {"model": combo["model"], "language_code": "hi-IN", "with_diarization": "true"}),
        ("language_code=unknown", {"model": combo["model"], "language_code": "unknown"}),
    ]
    for label, data in variants:
        try:
            code, payload, dt = stt_call(
                client, path=combo["path"], auth=combo["auth"], wav_bytes=wav_bytes, data=data
            )
            out[label] = {"status": code, "latency_s": round(dt, 3), "response": payload if code == 200 else brief(payload, 300)}
            print(f"  {label:26s} -> {code} ({dt:.2f}s)\n    {brief(payload, 700)}")
        except Exception as e:  # noqa: BLE001
            out[label] = {"error": repr(e)}
            print(f"  {label:26s} -> EXC {e!r}")
    # also the translate sibling, which is what Hinglish may actually need
    try:
        code, payload, dt = stt_call(
            client,
            path="/speech-to-text-translate",
            auth=combo["auth"],
            wav_bytes=wav_bytes,
            data={"model": "saaras:v2.5"},
        )
        out["speech-to-text-translate (saaras:v2.5)"] = {
            "status": code,
            "latency_s": round(dt, 3),
            "response": payload if code == 200 else brief(payload, 400),
        }
        print(f"  speech-to-text-translate    -> {code} ({dt:.2f}s)\n    {brief(payload, 500)}")
    except Exception as e:  # noqa: BLE001
        out["speech-to-text-translate (saaras:v2.5)"] = {"error": repr(e)}
    REPORT["probes"]["H_stt_confidence"] = out


def resample_pcm16(raw: bytes, src_hz: int, dst_hz: int) -> bytes:
    """Nearest-neighbour PCM16 mono resample — stdlib only, good enough to prove acceptance."""
    n = len(raw) // 2
    src = struct.unpack(f"<{n}h", raw[: n * 2])
    ratio = src_hz / dst_hz
    out_n = int(n / ratio)
    dst = [src[min(int(i * ratio), n - 1)] for i in range(out_n)]
    return struct.pack(f"<{len(dst)}h", *dst)


def wrap_wav(pcm: bytes, hz: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(hz)
        w.writeframes(pcm)
    return buf.getvalue()


def probe_i_stt_formats(client: httpx.Client, combo: dict[str, Any], wav_bytes: bytes) -> None:
    say("I. Saaras STT — accepted input formats and sample rates")
    out: dict[str, Any] = {}
    # unpack the source wav
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        src_hz = w.getframerate()
        pcm = w.readframes(w.getnframes())
    out["source_sample_rate_hz"] = src_hz

    # I1: resampled WAVs — do the ElevenLabs-relevant rates go through?
    for hz in (8000, 16000, 24000, 44100):
        body = wrap_wav(resample_pcm16(pcm, src_hz, hz) if hz != src_hz else pcm, hz)
        try:
            code, payload, dt = stt_call(
                client,
                path=combo["path"],
                auth=combo["auth"],
                wav_bytes=body,
                filename=f"probe_{hz}.wav",
                data={"model": combo["model"], "language_code": "hi-IN"},
            )
            out[f"wav@{hz}"] = {
                "status": code,
                "latency_s": round(dt, 3),
                "transcript": payload.get("transcript") if isinstance(payload, dict) else brief(payload, 200),
            }
            print(f"  wav @ {hz:6d} Hz -> {code} ({dt:.2f}s) {brief(out[f'wav@{hz}'].get('transcript'), 160)}")
        except Exception as e:  # noqa: BLE001
            out[f"wav@{hz}"] = {"error": repr(e)}

    # I2: headerless raw PCM — does it need a container?
    try:
        code, payload, dt = stt_call(
            client,
            path=combo["path"],
            auth=combo["auth"],
            wav_bytes=pcm,
            filename="probe.pcm",
            mime="application/octet-stream",
            data={"model": combo["model"], "language_code": "hi-IN"},
        )
        out["headerless_pcm16"] = {
            "status": code,
            "latency_s": round(dt, 3),
            "response": payload if code == 200 else brief(payload, 300),
        }
        print(f"  headerless PCM16 -> {code} {brief(payload, 250)}")
    except Exception as e:  # noqa: BLE001
        out["headerless_pcm16"] = {"error": repr(e)}

    # I3: base64-in-JSON instead of multipart?
    try:
        t0 = time.perf_counter()
        r = client.post(
            f"{SARVAM_HOST}{combo['path']}",
            headers={**AUTH_STYLES[combo["auth"]](SARVAM_KEY), "Content-Type": "application/json"},
            json={"audio": base64.b64encode(wav_bytes).decode(), "model": combo["model"], "language_code": "hi-IN"},
            timeout=60,
        )
        out["json_base64_body"] = {
            "status": r.status_code,
            "latency_s": round(time.perf_counter() - t0, 3),
            "body": brief(r.text, 300),
        }
        print(f"  JSON base64 body -> {r.status_code} {brief(r.text, 200)}")
    except Exception as e:  # noqa: BLE001
        out["json_base64_body"] = {"error": repr(e)}

    REPORT["probes"]["I_stt_formats"] = out


async def probe_j_stt_streaming(combo: dict[str, Any]) -> None:
    say("J. Saaras STT — streaming availability")
    out: dict[str, Any] = {}
    try:
        import websockets
    except ImportError:
        out["error"] = "websockets not importable"
        REPORT["probes"]["J_stt_streaming"] = out
        return
    urls = [
        "wss://api.sarvam.ai/speech-to-text/ws?language-code=hi-IN&model=saarika:v2.5",
        "wss://api.sarvam.ai/speech-to-text/ws?language-code=hi-IN",
        "wss://api.sarvam.ai/speech-to-text-translate/ws?model=saaras:v2.5",
    ]
    for url in urls:
        rec: dict[str, Any] = {}
        try:
            hdrs = AUTH_STYLES[combo["auth"]](SARVAM_KEY)
            kw = {"ping_interval": None, "open_timeout": 15, "max_size": 16 * 1024 * 1024}
            try:
                ws = await websockets.connect(url, additional_headers=hdrs, **kw)
            except TypeError:
                ws = await websockets.connect(url, extra_headers=hdrs, **kw)
            async with ws:
                rec["connected"] = True
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=8)
                    rec["first_frame"] = brief(msg if isinstance(msg, str) else f"<{len(msg)} bytes>", 300)
                except asyncio.TimeoutError:
                    rec["first_frame"] = "(none within 8s — server waits for us)"
        except Exception as e:  # noqa: BLE001
            rec["error"] = repr(e)
        out[url.split("?")[0]] = rec
        print(f"  {url.split('?')[0]} -> {brief(rec, 300)}")
        if rec.get("connected"):
            break
    REPORT["probes"]["J_stt_streaming"] = out


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — casting, pacing, and the streaming format question
# ─────────────────────────────────────────────────────────────────────────────
def estimate_f0_hz(wav_bytes: bytes) -> dict[str, Any]:
    """Median fundamental frequency by autocorrelation. Stdlib only.

    Voice casting must be a MEASUREMENT, not a guess from a name. Adult male
    speech sits ~85-155 Hz, adult female ~165-255 Hz.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        hz = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    samples = struct.unpack(f"<{len(raw) // 2}h", raw[: (len(raw) // 2) * 2])
    win = int(0.040 * hz)  # 40 ms
    hop = int(0.020 * hz)
    lo_lag, hi_lag = int(hz / 400), int(hz / 60)  # 60-400 Hz search band
    f0s: list[float] = []
    for start in range(0, max(0, len(samples) - win), hop):
        seg = samples[start : start + win]
        energy = sum(s * s for s in seg) / win
        if energy < 2_000_000:  # skip silence / unvoiced
            continue
        mean = sum(seg) / win
        seg = [s - mean for s in seg]
        r0 = sum(s * s for s in seg)
        if r0 <= 0:
            continue
        best_lag, best_val = 0, 0.0
        for lag in range(lo_lag, min(hi_lag, win - 1)):
            acc = 0.0
            for i in range(0, win - lag, 2):  # stride 2 for speed
                acc += seg[i] * seg[i + lag]
            val = acc / r0
            if val > best_val:
                best_val, best_lag = val, lag
        if best_lag and best_val > 0.30:  # voiced enough to trust
            f0s.append(hz / best_lag)
    if not f0s:
        return {"f0_median_hz": None, "n_voiced_windows": 0}
    f0s.sort()
    med = f0s[len(f0s) // 2]
    return {
        "f0_median_hz": round(med, 1),
        "f0_p10_hz": round(f0s[int(0.10 * len(f0s))], 1),
        "f0_p90_hz": round(f0s[int(0.90 * len(f0s))], 1),
        "n_voiced_windows": len(f0s),
        "inferred_register": "male" if med < 160 else ("female" if med > 175 else "ambiguous"),
    }


CASTING_LINE = "Nahi yaar, ye theek nahi hai."  # 6 words, enough voiced audio to pitch-track

# Candidates spanning the roster. bulbul:v2's documented set plus a sample of
# the extended names the 400 message revealed.
CASTING_CANDIDATES = [
    "anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh",
    "aditya", "rahul", "rohan", "amit", "dev", "varun", "kabir", "ashutosh",
    "anand", "tarun", "sunny", "vijay", "mohit", "rehan", "soham",
]


def probe_k_casting(client: httpx.Client, combo: dict[str, Any]) -> None:
    say("K. Bulbul TTS — voice casting by MEASURED pitch (short line per speaker)")
    out: dict[str, Any] = {"line": CASTING_LINE, "speakers": {}}
    for sp in CASTING_CANDIDATES:
        body = {
            "text": CASTING_LINE,
            "target_language_code": "hi-IN",
            "speaker": sp,
            "model": combo["model"],
            "speech_sample_rate": 16000,
        }
        try:
            code, payload, dt, _ = tts_call(
                client, path=combo["path"], auth=combo["auth"], body=body, timeout=60
            )
            if code != 200:
                out["speakers"][sp] = {"status": code, "body": brief(payload, 200)}
                print(f"  {sp:12s} -> {code}")
                continue
            audio, _ = unwrap_audio(payload)
            path = OUT_DIR / "casting" / f"{sp}.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
            d = describe_audio(audio)
            f0 = estimate_f0_hz(audio)
            rec = {
                "status": 200,
                "latency_s": round(dt, 3),
                "duration_s": d.get("duration_s"),
                "sample_rate_hz": d.get("sample_rate_hz"),
                "saved_to": str(path),
                **f0,
            }
            out["speakers"][sp] = rec
            print(
                f"  {sp:12s} -> F0 {str(rec.get('f0_median_hz')):>6s} Hz  "
                f"({rec.get('inferred_register')})  dur {rec.get('duration_s')}s  "
                f"lat {rec['latency_s']}s"
            )
        except Exception as e:  # noqa: BLE001
            out["speakers"][sp] = {"error": repr(e)}
            print(f"  {sp:12s} -> EXC {e!r}")
    males = sorted(
        (
            (v["f0_median_hz"], k)
            for k, v in out["speakers"].items()
            if isinstance(v.get("f0_median_hz"), (int, float)) and v["f0_median_hz"] < 160
        )
    )
    out["male_voices_by_pitch_low_to_high"] = [k for _, k in males]
    print(f"\n  MALE VOICES (low -> high pitch): {out['male_voices_by_pitch_low_to_high']}")
    REPORT["probes"]["K_casting"] = out


LONG_TURN = (
    "Dekho bhai, main aapko seedhi baat bata deta hoon. Pichle saal maine ye plan "
    "pandrah sau rupaye mein liya tha, aur ab aap mujhe sirf das percent discount "
    "de rahe ho. Ye toh bilkul bhi theek nahi hai yaar, mere dost ko usi plan pe "
    "tees percent off mila tha, toh main kyun zyada paisa doon?"
)


def probe_l_latency_vs_length(client: httpx.Client, combo: dict[str, Any], speaker: str) -> None:
    say("L. Bulbul TTS — latency at REALISTIC persona-turn length (the 60s budget)")
    out: dict[str, Any] = {}
    for label, text in (("short_10w", HINGLISH_LINE), ("long_52w", LONG_TURN)):
        body = {
            "text": text,
            "target_language_code": "hi-IN",
            "speaker": speaker,
            "model": combo["model"],
            "speech_sample_rate": 16000,
        }
        try:
            code, payload, dt, _ = tts_call(
                client, path=combo["path"], auth=combo["auth"], body=body, timeout=120
            )
            rec: dict[str, Any] = {
                "status": code,
                "n_chars": len(text),
                "n_words": len(text.split()),
                "latency_s": round(dt, 3),
            }
            if code == 200:
                audio, _ = unwrap_audio(payload)
                d = describe_audio(audio)
                rec["audio_duration_s"] = d.get("duration_s")
                rec["sample_rate_hz"] = d.get("sample_rate_hz")
                rec["n_audio_chunks_in_list"] = (
                    len(payload["audios"]) if isinstance(payload, dict) and "audios" in payload else None
                )
                rec["chars_per_second_of_speech"] = round(len(text) / d["duration_s"], 2)
                rec["realtime_factor"] = round(dt / d["duration_s"], 3)
                (OUT_DIR / f"bulbul_{label}_16k.wav").write_bytes(audio)
                rec["saved_to"] = str(OUT_DIR / f"bulbul_{label}_16k.wav")
            else:
                rec["body"] = brief(payload, 300)
            out[label] = rec
            print(f"  {label:10s} -> {json.dumps(rec, default=str)}")
        except Exception as e:  # noqa: BLE001
            out[label] = {"error": repr(e)}
            print(f"  {label:10s} -> EXC {e!r}")
    # text length ceiling
    try:
        code, payload, _, _ = tts_call(
            client,
            path=combo["path"],
            auth=combo["auth"],
            body={
                "text": "a" * 5000,
                "target_language_code": "hi-IN",
                "speaker": speaker,
                "model": combo["model"],
            },
            timeout=30,
        )
        out["text_length_ceiling_probe"] = {"status": code, "body": brief(payload, 300)}
        print(f"  5000-char text -> {code}: {brief(payload, 250)}")
    except Exception as e:  # noqa: BLE001
        out["text_length_ceiling_probe"] = {"error": repr(e)}
    REPORT["probes"]["L_latency_vs_length"] = out


async def probe_m_tts_ws_format(combo: dict[str, Any], speaker: str) -> None:
    say("M. Bulbul TTS WebSocket — can it emit 16 kHz PCM (Tara's format) directly?")
    out: dict[str, Any] = {}
    try:
        import websockets
    except ImportError:
        out["error"] = "websockets not importable"
        REPORT["probes"]["M_tts_ws_format"] = out
        return

    configs = [
        ("wav@16000", {"speaker": speaker, "target_language_code": "hi-IN",
                       "output_audio_codec": "wav", "speech_sample_rate": 16000}),
        ("linear16@16000", {"speaker": speaker, "target_language_code": "hi-IN",
                            "output_audio_codec": "linear16", "speech_sample_rate": 16000}),
        ("bad_codec", {"speaker": speaker, "target_language_code": "hi-IN",
                       "output_audio_codec": "__nope__"}),
    ]
    url = f"wss://api.sarvam.ai/text-to-speech/ws?model={combo['model']}"
    for label, cfg in configs:
        rec: dict[str, Any] = {"config_sent": cfg}
        try:
            hdrs = AUTH_STYLES[combo["auth"]](SARVAM_KEY)
            kw = {"ping_interval": None, "open_timeout": 20, "max_size": 16 * 1024 * 1024}
            try:
                ws = await websockets.connect(url, additional_headers=hdrs, **kw)
            except TypeError:
                ws = await websockets.connect(url, extra_headers=hdrs, **kw)
            async with ws:
                t0 = time.perf_counter()
                await ws.send(json.dumps({"type": "config", "data": cfg}))
                await ws.send(json.dumps({"type": "text", "data": {"text": HINGLISH_LINE}}))
                await ws.send(json.dumps({"type": "flush"}))
                chunks: list[bytes] = []
                events: list[dict] = []
                first_s = None
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=12)
                        if isinstance(msg, bytes):
                            if first_s is None:
                                first_s = time.perf_counter() - t0
                            chunks.append(msg)
                            continue
                        ev = json.loads(msg)
                        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                        b64 = (data or {}).get("audio")
                        if b64:
                            if first_s is None:
                                first_s = time.perf_counter() - t0
                            chunks.append(base64.b64decode(b64))
                            events.append({"type": ev.get("type"), "audio_bytes": len(chunks[-1])})
                        else:
                            events.append({"type": ev.get("type"), "payload": brief(ev, 250)})
                        if len(events) > 40:
                            break
                except asyncio.TimeoutError:
                    rec["ended"] = "recv timeout"
                except Exception as e:  # noqa: BLE001
                    rec["ended"] = repr(e)
                rec["n_audio_chunks"] = len(chunks)
                rec["events"] = events[:8]
                rec["time_to_first_audio_s"] = round(first_s, 3) if first_s else None
                if chunks:
                    rec["chunk_sizes"] = [len(c) for c in chunks[:8]]
                    rec["first_chunk_header_hex"] = chunks[0][:16].hex()
                    rec["first_chunk_described"] = describe_audio(chunks[0])
                    joined = b"".join(chunks)
                    rec["total_bytes"] = len(joined)
                    p = OUT_DIR / f"bulbul_ws_{label.replace('@', '_')}.bin"
                    p.write_bytes(joined)
                    rec["saved_to"] = str(p)
        except Exception as e:  # noqa: BLE001
            rec["error"] = repr(e)
        out[label] = rec
        print(f"  {label:16s} -> {brief(rec, 600)}")
    REPORT["probes"]["M_tts_ws_format"] = out


def probe_o_v3_roster_and_casting(client: httpx.Client, combo: dict[str, Any]) -> None:
    """bulbul:v1 is DEAD. bulbul:v2 and bulbul:v3 have DISJOINT speaker rosters."""
    say("O. Bulbul model/roster reality: v1 dead, v2 vs v3 rosters are disjoint")
    out: dict[str, Any] = {}
    for m in ("bulbul:v1", "bulbul:v2", "bulbul:v3", "bulbul:v3-beta"):
        code, payload, _, _ = tts_call(
            client,
            path=combo["path"],
            auth=combo["auth"],
            body={"text": TINY_LINE, "target_language_code": "hi-IN", "speaker": "__nope__", "model": m},
            timeout=30,
        )
        msg = payload.get("error", {}).get("message", "") if isinstance(payload, dict) else str(payload)
        out[m] = {"status": code, "message": msg}
        print(f"  {m:14s} -> {code}: {msg[:150]}")
    # roster per model, asked of the model itself
    for m in ("bulbul:v2", "bulbul:v3"):
        code, payload, _, _ = tts_call(
            client,
            path=combo["path"],
            auth=combo["auth"],
            body={"text": TINY_LINE, "target_language_code": "hi-IN", "speaker": "aditya" if m == "bulbul:v2" else "abhilash", "model": m},
            timeout=30,
        )
        msg = payload.get("error", {}).get("message", "") if isinstance(payload, dict) else ""
        if "are: " in msg:
            out[f"{m}_roster"] = msg.split("are: ")[1].rstrip(".").split(", ")
            print(f"  {m} roster ({len(out[f'{m}_roster'])}): {out[f'{m}_roster']}")

    # measured pitch for the v3 roster — casting must be a measurement
    casting: dict[str, Any] = {}
    for sp in out.get("bulbul:v3_roster", []):
        code, payload, dt, _ = tts_call(
            client,
            path=combo["path"],
            auth=combo["auth"],
            body={"text": CASTING_LINE, "target_language_code": "hi-IN", "speaker": sp,
                  "model": "bulbul:v3", "speech_sample_rate": 16000},
            timeout=120,
        )
        if code != 200:
            casting[sp] = {"status": code}
            continue
        audio, _ = unwrap_audio(payload)
        p = OUT_DIR / "casting_v3" / f"{sp}.wav"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(audio)
        d = describe_audio(audio)
        casting[sp] = {"latency_s": round(dt, 3), "duration_s": d.get("duration_s"),
                       "sample_rate_hz": d.get("sample_rate_hz"), "saved_to": str(p),
                       **estimate_f0_hz(audio)}
        print(f"  v3 {sp:10s} F0={str(casting[sp].get('f0_median_hz')):>6s}Hz "
              f"{casting[sp].get('inferred_register')} dur={casting[sp].get('duration_s')}s")
    out["v3_casting"] = casting
    males = sorted((v["f0_median_hz"], k) for k, v in casting.items()
                   if isinstance(v.get("f0_median_hz"), (int, float)) and v["f0_median_hz"] < 160)
    out["v3_male_by_pitch_low_to_high"] = [k for _, k in males]
    print(f"\n  v3 MALE (low -> high): {out['v3_male_by_pitch_low_to_high']}")
    REPORT["probes"]["O_v3_roster_casting"] = out


def probe_p_roundtrip_fidelity(client: httpx.Client, tts: dict[str, Any], stt: dict[str, Any],
                               *, model: str, speaker: str) -> None:
    """Does the NUMBER survive TTS->STT? A mis-heard 10% would fake a ceiling violation."""
    say(f"P. Hinglish round-trip fidelity ({model}/{speaker}) — do the numbers survive?")
    lines = [
        "Arre yaar, 10% off is not enough for the cricket.",
        "Mere dost ko toh 30 percent off mila tha, aap 10 percent bol rahe ho.",
        "1499 rupaye bahut zyada hai bhai, kuch kam karo.",
    ]
    out: dict[str, Any] = {"model": model, "speaker": speaker, "lines": []}
    for line in lines:
        code, payload, _, _ = tts_call(
            client, path=tts["path"], auth=tts["auth"],
            body={"text": line, "target_language_code": "hi-IN", "speaker": speaker,
                  "model": model, "speech_sample_rate": 16000},
        )
        if code != 200:
            out["lines"].append({"src": line, "tts_status": code})
            continue
        audio, _ = unwrap_audio(payload)
        rec: dict[str, Any] = {"src": line}
        for lang in ("hi-IN", "unknown"):
            c2, p2, dt2 = stt_call(client, path=stt["path"], auth=stt["auth"], wav_bytes=audio,
                                   data={"model": stt["model"], "language_code": lang})
            rec[f"stt[{lang}]"] = {
                "detected_language": p2.get("language_code") if isinstance(p2, dict) else None,
                "transcript": p2.get("transcript") if isinstance(p2, dict) else brief(p2, 200),
                "latency_s": round(dt2, 3),
            }
            print(f"  [{lang:7s}] {rec[f'stt[{lang}]']['detected_language']}: {rec[f'stt[{lang}]']['transcript']}")
        print(f"  SRC          : {line}\n")
        out["lines"].append(rec)
    REPORT["probes"]["P_roundtrip_fidelity"] = out


async def probe_q_v3_streaming(combo: dict[str, Any], speaker: str) -> None:
    """Streaming is what makes v3's slow batch latency irrelevant."""
    say("Q. Bulbul v3 over the TTS WebSocket — chunked linear16, time-to-first-audio")
    out: dict[str, Any] = {}
    try:
        import websockets
    except ImportError:
        REPORT["probes"]["Q_v3_streaming"] = {"error": "websockets missing"}
        return
    for model in ("bulbul:v2", "bulbul:v3"):
        sp = speaker if model == "bulbul:v3" else "abhilash"
        rec: dict[str, Any] = {"speaker": sp}
        url = f"wss://api.sarvam.ai/text-to-speech/ws?model={model}"
        try:
            hdrs = AUTH_STYLES[combo["auth"]](SARVAM_KEY)
            async with websockets.connect(url, additional_headers=hdrs, ping_interval=None,
                                          open_timeout=20, max_size=16 * 1024 * 1024) as ws:
                t0 = time.perf_counter()
                await ws.send(json.dumps({"type": "config", "data": {
                    "speaker": sp, "target_language_code": "hi-IN",
                    "output_audio_codec": "linear16", "speech_sample_rate": 16000}}))
                await ws.send(json.dumps({"type": "text", "data": {"text": LONG_TURN}}))
                await ws.send(json.dumps({"type": "flush"}))
                sizes: list[int] = []
                first = None
                try:
                    while True:
                        m = await asyncio.wait_for(ws.recv(), timeout=15)
                        if isinstance(m, bytes):
                            b = m
                        else:
                            ev = json.loads(m)
                            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                            if not (data or {}).get("audio"):
                                continue
                            b = base64.b64decode(data["audio"])
                        if first is None:
                            first = time.perf_counter() - t0
                        sizes.append(len(b))
                except asyncio.TimeoutError:
                    pass
                total = sum(sizes)
                rec.update({
                    "n_chunks": len(sizes), "chunk_sizes_head": sizes[:10],
                    "total_pcm_bytes": total,
                    "audio_seconds_at_16k_pcm16": round(total / 32000, 2),
                    "time_to_first_audio_s": round(first, 3) if first else None,
                })
        except Exception as e:  # noqa: BLE001
            rec["error"] = repr(e)
        out[model] = rec
        print(f"  {model}: {json.dumps(rec, default=str)}")
    REPORT["probes"]["Q_v3_streaming"] = out


def probe_n_roundtrip_16k(client: httpx.Client, tts: dict[str, Any], stt: dict[str, Any], speaker: str) -> None:
    """THE Level 1 question: synth at 16k -> strip header -> that IS Tara's wire format."""
    say("N. End-to-end format proof: Bulbul @16k -> headerless PCM16 -> Tara's pcm_16000")
    out: dict[str, Any] = {}
    body = {
        "text": HINGLISH_LINE,
        "target_language_code": "hi-IN",
        "speaker": speaker,
        "model": tts["model"],
        "speech_sample_rate": 16000,
    }
    code, payload, dt, _ = tts_call(client, path=tts["path"], auth=tts["auth"], body=body)
    out["tts_status"] = code
    out["tts_latency_s"] = round(dt, 3)
    if code != 200:
        out["body"] = brief(payload, 300)
        REPORT["probes"]["N_roundtrip"] = out
        return
    audio, _ = unwrap_audio(payload)
    d = describe_audio(audio)
    out["wav"] = d
    with wave.open(io.BytesIO(audio), "rb") as w:
        pcm = w.readframes(w.getnframes())
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    out["stripped_pcm"] = {
        "n_bytes": len(pcm),
        "sample_rate_hz": rate,
        "channels": ch,
        "bits_per_sample": width * 8,
        "header_bytes_stripped": len(audio) - len(pcm),
        "duration_s": round(len(pcm) / (rate * ch * width), 4),
        "matches_tara_pcm_16000": rate == 16000 and ch == 1 and width == 2,
    }
    out["base64_len_for_user_audio_chunk"] = len(base64.b64encode(pcm))
    # how many 250ms chunks would we stream?
    chunk_bytes = int(0.25 * rate) * width
    out["stream_plan_250ms"] = {
        "chunk_bytes": chunk_bytes,
        "n_chunks": -(-len(pcm) // chunk_bytes),
    }
    (OUT_DIR / "bulbul_tara_wire_16k.pcm").write_bytes(pcm)
    out["saved_pcm"] = str(OUT_DIR / "bulbul_tara_wire_16k.pcm")
    print(json.dumps(out, indent=2, default=str))

    # and STT the same 16k file, since that is what we'd also feed Saaras
    code2, payload2, dt2 = stt_call(
        client, path=stt["path"], auth=stt["auth"], wav_bytes=audio,
        data={"model": stt["model"], "language_code": "unknown"},
    )
    out["stt_of_16k"] = {"status": code2, "latency_s": round(dt2, 3), "response": payload2}
    print(f"  STT of the 16k wav -> {code2} ({dt2:.2f}s): {brief(payload2, 300)}")
    REPORT["probes"]["N_roundtrip"] = out


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    t_start = time.time()

    # ── PHASE 2: casting + pacing + streaming format. Reuses the combos that
    #    phase 1 proved, so it can be re-run on its own without re-probing.
    # ── PHASE 3: bulbul model reality (v1 dead / v2 vs v3 rosters), v3 casting
    #    by measured pitch, round-trip number fidelity, and v3 streaming.
    if "--phase3" in sys.argv:
        globals()["RESULT_JSON"] = OUT_DIR / "sarvam_speech_result_phase3.json"
        tts_combo = {"path": "/text-to-speech", "auth": "api-subscription-key", "model": "bulbul:v2"}
        stt_combo = {"path": "/speech-to-text", "auth": "api-subscription-key", "model": "saarika:v2.5"}
        with httpx.Client(follow_redirects=True) as client:
            probe_o_v3_roster_and_casting(client, tts_combo)
            probe_l_latency_vs_length(client, {**tts_combo, "model": "bulbul:v3"}, "varun")
            probe_p_roundtrip_fidelity(client, tts_combo, stt_combo, model="bulbul:v3", speaker="varun")
        asyncio.run(probe_q_v3_streaming(tts_combo, "varun"))
        REPORT["elapsed_s"] = round(time.time() - t_start, 2)
        RESULT_JSON.write_text(json.dumps(REPORT, indent=2, default=str, ensure_ascii=False))
        say(f"WROTE {RESULT_JSON}")
        return 0

    if "--phase2" in sys.argv:
        globals()["RESULT_JSON"] = OUT_DIR / "sarvam_speech_result_phase2.json"
        tts_combo = {"path": "/text-to-speech", "auth": "api-subscription-key", "model": "bulbul:v2"}
        stt_combo = {"path": "/speech-to-text", "auth": "api-subscription-key", "model": "saarika:v2.5"}
        with httpx.Client(follow_redirects=True) as client:
            probe_k_casting(client, tts_combo)
            probe_l_latency_vs_length(client, tts_combo, "abhilash")
            probe_n_roundtrip_16k(client, tts_combo, stt_combo, "abhilash")
        asyncio.run(probe_m_tts_ws_format(tts_combo, "abhilash"))
        REPORT["elapsed_s"] = round(time.time() - t_start, 2)
        RESULT_JSON.write_text(json.dumps(REPORT, indent=2, default=str, ensure_ascii=False))
        say(f"WROTE {RESULT_JSON}")
        return 0

    with httpx.Client(follow_redirects=True) as client:
        probe_a_elevenlabs_audio_config(client)

        b = probe_b_tts_endpoint(client)
        combo = b.get("working")
        if not combo:
            print("\nFATAL: no working Bulbul TTS combination. Stopping.")
            RESULT_JSON.write_text(json.dumps(REPORT, indent=2, default=str))
            return 1

        probe_c_speakers(client, combo)

        # pick a male-sounding speaker if the roster gives one; default anushka
        speaker = os.environ.get("SPIKE_SPEAKER", "anushka" if combo["model"] == "bulbul:v2" else "meera")
        d = probe_d_synthesise(client, combo, speaker)

        # a second speaker so persona casting has a real comparison
        alt = os.environ.get("SPIKE_SPEAKER_ALT", "abhilash" if combo["model"] == "bulbul:v2" else "amol")
        d_alt = probe_d_synthesise(client, combo, alt)
        REPORT["probes"]["D_synthesis_alt"] = REPORT["probes"].pop("D_synthesis")
        REPORT["probes"]["D_synthesis"] = d

        probe_e_sample_rates(client, combo, speaker)

        wav_bytes = None
        for cand in (d, d_alt):
            if cand.get("saved_to"):
                wav_bytes = Path(cand["saved_to"]).read_bytes()
                break
        if wav_bytes:
            g = probe_g_stt(client, wav_bytes)
            stt_combo = g.get("working")
            if stt_combo:
                probe_h_stt_confidence(client, stt_combo, wav_bytes)
                probe_i_stt_formats(client, stt_combo, wav_bytes)
                asyncio.run(probe_j_stt_streaming(stt_combo))
            else:
                print("  no working STT combo; skipping H/I/J")
        else:
            print("  no wav produced; skipping STT probes")

        asyncio.run(probe_f_tts_streaming(combo, speaker))

    REPORT["elapsed_s"] = round(time.time() - t_start, 2)
    RESULT_JSON.write_text(json.dumps(REPORT, indent=2, default=str, ensure_ascii=False))
    say(f"WROTE {RESULT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
