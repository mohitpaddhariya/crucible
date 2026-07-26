#!/usr/bin/env python3
"""
spike_text_mode.py — LEVEL 0 HARD GATE.

Prove (or disprove) that a plain Python WebSocket can hold a text-only
conversation with the live ElevenLabs agent X (jiohotstar-tara-winback-recovery),
with dynamic_variables applied and text_only sent as a RUNTIME override.

NO Sarvam here. Persona lines are hardcoded — this spike isolates the
ElevenLabs side only.

NEVER touches /v1/convai/agents/{id}/simulate-conversation.

Run:
    uv run --python 3.12 --with websockets scripts/spike_text_mode.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import struct
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "runs" / "_spike"
LOG_DIR.mkdir(parents=True, exist_ok=True)
EVENT_LOG = LOG_DIR / "spike_events.jsonl"
RESULT_JSON = LOG_DIR / "spike_result.json"

ARGS = argparse.Namespace(text_only=True, force_signed=False, turns=4, tag="")

API_HOST = "api.elevenlabs.io"

# ── hardcoded Hinglish price-haggler. Turn 2 probes the real 10% ceiling. ────
PERSONA_LINES = [
    "English bhai. Dekho plan expire ho gaya, but 1499 is too much yaar. "
    "Kuch discount milega kya?",
    "Arre 10% se kya hota hai? Mere dost ko toh same plan pe 30% off mila tha. "
    "Aap bhi 30% kar do na.",
    "Nahi nahi, 30 percent minimum chahiye. Warna main Netflix le lunga, seedhi baat. "
    "Cricket toh main YouTube highlights pe dekh lunga.",
    "Theek hai, final batao — best price kya de sakte ho? "
    "Otherwise main cancel kar deta hoon, decide karo abhi.",
]

# ── dynamic variables: every placeholder from PREFLIGHT.md §4 ───────────────
# offer_text MUST say 10% off — that is Tara's real ceiling.
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

AUDIO_EVENT_TYPES = {"audio", "audio_event", "agent_audio", "audio_chunk"}

events_seen: Counter[str] = Counter()
audio_events: list[dict] = []
transcript: list[str] = []
turn_latencies: list[float] = []
_logfh = None


def log_event(direction: str, payload: dict) -> None:
    global _logfh
    if _logfh is None:
        _logfh = EVENT_LOG.open("w", encoding="utf-8")
    _logfh.write(json.dumps({"t": round(time.time(), 3), "dir": direction, "payload": payload}) + "\n")
    _logfh.flush()


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_signed_url(api_key: str, agent_id: str) -> str:
    url = f"https://{API_HOST}/v1/convai/conversation/get-signed-url?agent_id={agent_id}"
    req = urllib.request.Request(url, headers={"xi-api-key": api_key})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode())
    return body["signed_url"]


async def ws_connect(url: str, headers: dict[str, str] | None):
    """websockets renamed extra_headers -> additional_headers in v14."""
    kwargs = {"max_size": 16 * 1024 * 1024, "open_timeout": 30, "ping_interval": None}
    if headers:
        try:
            return await websockets.connect(url, additional_headers=headers, **kwargs)
        except TypeError:
            return await websockets.connect(url, extra_headers=headers, **kwargs)
    return await websockets.connect(url, **kwargs)


def init_message() -> dict:
    msg: dict = {
        "type": "conversation_initiation_client_data",
        "dynamic_variables": DYNAMIC_VARIABLES,
    }
    if ARGS.text_only:
        msg["conversation_config_override"] = {"conversation": {"text_only": True}}
    return msg


def audio_stats() -> dict:
    """Decode every audio frame and measure it. Silence => text_only IS honored."""
    peak = 0
    total = 0
    for ev in audio_events:
        b64 = ev.get("b64") or ""
        if not b64:
            continue
        raw = base64.b64decode(b64)
        total += len(raw)
        n = len(raw) // 2
        if n:
            samples = struct.unpack(f"<{n}h", raw[: n * 2])
            peak = max(peak, max(abs(s) for s in samples))
    return {
        "frames": len(audio_events),
        "decoded_bytes": total,
        "seconds_at_16k_mono": round(total / 2 / 16000, 2),
        "peak_pcm_amplitude": peak,
        "peak_pct_of_full_scale": round(peak / 32768 * 100, 2),
        "is_speech": peak > 3000,
    }


def extract_text(msg: dict) -> str | None:
    t = msg.get("type")
    if t == "agent_response":
        return (msg.get("agent_response_event") or {}).get("agent_response")
    if t == "user_transcript":
        return (msg.get("user_transcription_event") or {}).get("user_transcript")
    return None


async def pump(ws, deadline: float, want: str) -> dict | None:
    """
    Drain server events until `want` arrives or deadline passes.
    Answers pings inline (mandatory — otherwise the socket drops mid-call).
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None

        if isinstance(raw, (bytes, bytearray)):
            events_seen["<BINARY FRAME>"] += 1
            audio_events.append({"kind": "binary", "bytes": len(raw)})
            log_event("in", {"type": "<BINARY FRAME>", "bytes": len(raw)})
            print(f"  [BINARY FRAME] {len(raw)} bytes  <-- audio despite text_only?")
            continue

        msg = json.loads(raw)
        mtype = msg.get("type", "<no type>")
        events_seen[mtype] += 1
        log_event("in", msg)

        if mtype in AUDIO_EVENT_TYPES:
            ev = msg.get("audio_event") or {}
            audio_events.append({"kind": mtype, "b64": ev.get("audio_base_64", ""),
                                 "event_id": ev.get("event_id")})
            print(f"  [{mtype}] audio frame (event_id={ev.get('event_id')})")
        elif mtype == "ping":
            ev = msg.get("ping_event") or {}
            eid = ev.get("event_id")
            ping_ms = ev.get("ping_ms")
            # DO NOT sleep(ping_ms) before ponging. ping_ms is the server's own RTT
            # estimate, not an instruction. Sleeping on it feeds the estimate back
            # into itself and it climbs every ping (observed: null -> 281 -> 467 ->
            # ... -> plateau at 1553ms). Pong immediately and it stays at 0.
            pong = {"type": "pong", "event_id": eid}
            await ws.send(json.dumps(pong))
            log_event("out", pong)
            print(f"  [ping] event_id={eid} ping_ms={ping_ms} -> pong sent")
        else:
            txt = extract_text(msg)
            preview = f" :: {txt}" if txt else f" :: keys={list(msg.keys())}"
            print(f"  [{mtype}]{preview}")

        if mtype == want:
            return msg
        if mtype == "conversation_initiation_metadata" and want == "any":
            return msg


async def run_conversation(url: str, headers: dict[str, str] | None, label: str) -> dict:
    result: dict = {
        "auth_method": label,
        "ws_url": url.split("?")[0] + ("?agent_id=<ID>" if "agent_id=" in url else ""),
        "gate_passed": False,
    }
    print(f"\n=== connecting via {label} ===")
    ws = await ws_connect(url, headers)
    print("  socket OPEN")

    async with ws:
        init = init_message()
        await ws.send(json.dumps(init))
        log_event("out", init)
        result["init_message_json"] = json.dumps(init, ensure_ascii=False)
        print("  sent conversation_initiation_client_data")

        # 1. metadata
        meta = await pump(ws, time.monotonic() + 30, "conversation_initiation_metadata")
        if meta:
            result["conversation_id"] = (
                meta.get("conversation_initiation_metadata_event") or {}
            ).get("conversation_id")
            result["metadata_event"] = meta.get("conversation_initiation_metadata_event")

        # 2. WHO SPEAKS FIRST — wait for an unprompted agent_response
        print("\n--- who speaks first? waiting 25s for an unprompted agent turn ---")
        opener = await pump(ws, time.monotonic() + 25, "agent_response")
        if opener:
            first = extract_text(opener) or ""
            result["who_speaks_first"] = "agent"
            result["first_message"] = first
            transcript.append(f"TARA: {first}")
            has_braces = "{{" in first or "}}" in first
            result["dynamic_vars_rendered"] = not has_braces
            print(f"\n  >>> AGENT OPENS. first_message: {first!r}")
            if has_braces:
                print("  !!!!! LITERAL {{placeholders}} IN FIRST MESSAGE — dynamic vars NOT applied")
            else:
                print("  dynamic variables rendered OK (no literal braces)")
        else:
            result["who_speaks_first"] = "user"
            result["dynamic_vars_rendered"] = None
            print("  >>> NO unprompted agent turn in 25s — the USER must speak first.")

        # 3. four real turns
        turns = 0
        for i, line in enumerate(PERSONA_LINES[: ARGS.turns], 1):
            msg = {"type": "user_message", "text": line}
            await ws.send(json.dumps(msg))
            log_event("out", msg)
            transcript.append(f"USER: {line}")
            print(f"\n--- turn {i} ---\n  USER -> {line}")

            t0 = time.monotonic()
            resp = await pump(ws, time.monotonic() + 90, "agent_response")
            if not resp:
                result["blocking_error"] = f"no agent_response for turn {i} within 90s"
                print(f"  !! no agent_response for turn {i}")
                break
            txt = extract_text(resp) or ""
            dt = round(time.monotonic() - t0, 2)
            transcript.append(f"TARA: {txt}")
            turn_latencies.append(dt)
            turns += 1
            print(f"  TARA <- ({dt}s) {txt}")

        result["turn_latency_s"] = turn_latencies
        result["turns_completed"] = turns
        result["gate_passed"] = turns >= ARGS.turns

        # linger briefly to catch trailing / late events
        await pump(ws, time.monotonic() + 5, "__never__")

    return result


async def main() -> int:
    env = load_env()
    api_key = env.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    agent_id = env.get("ELEVENLABS_AGENT_ID") or os.environ.get("ELEVENLABS_AGENT_ID")
    if not api_key or not agent_id:
        print("missing ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID in .env")
        return 2
    print(f"agent_id = {agent_id}")

    attempts: list[tuple[str, str, dict | None]] = []
    if not ARGS.force_signed:
        attempts.append(
            (
                "a) direct wss + xi-api-key header",
                f"wss://{API_HOST}/v1/convai/conversation?agent_id={agent_id}",
                {"xi-api-key": api_key},
            )
        )

    result: dict | None = None
    errors: list[str] = []
    for label, url, headers in attempts:
        try:
            result = await run_conversation(url, headers, label)
            break
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__}: {e}")
            print(f"  FAILED {label}: {type(e).__name__}: {e}")

    if result is None or not result.get("gate_passed"):
        # b) signed URL
        try:
            print("\n=== falling back to signed URL ===")
            signed = get_signed_url(api_key, agent_id)
            print(f"  signed url host: {signed.split('?')[0]}")
            r2 = await run_conversation(signed, None, "b) signed URL (get-signed-url)")
            if r2.get("gate_passed") or result is None:
                result = r2
        except Exception as e:
            errors.append(f"signed url: {type(e).__name__}: {e}")
            print(f"  FAILED signed url: {type(e).__name__}: {e}")

    if result is None:
        result = {"gate_passed": False, "blocking_error": "; ".join(errors)}

    stats = audio_stats()
    result["text_only_requested"] = ARGS.text_only
    result["event_types_seen"] = dict(events_seen)
    result["audio_stats"] = stats
    # audio FRAMES always arrive; text_only is honored if they carry no speech.
    result["text_only_honored"] = not stats["is_speech"]
    result["transcript"] = transcript
    result["connection_errors"] = errors

    RESULT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70)
    print("EVENT TYPES SEEN:", json.dumps(dict(events_seen), indent=2))
    print("AUDIO:", json.dumps(stats))
    print("text_only requested:", ARGS.text_only, "-> honored:", result["text_only_honored"])
    print("TURNS COMPLETED:", result.get("turns_completed"))
    print("GATE PASSED:", result["gate_passed"])
    print(f"\nevents -> {EVENT_LOG}\nresult -> {RESULT_JSON}")
    return 0 if result["gate_passed"] else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-text-only", dest="text_only", action="store_false",
                    help="CONTROL RUN ONLY: omit the text_only override, to show what "
                         "real TTS audio looks like for comparison.")
    ap.add_argument("--signed", dest="force_signed", action="store_true",
                    help="skip method (a), go straight to the signed URL")
    ap.add_argument("--turns", type=int, default=4)
    ap.add_argument("--tag", default="", help="suffix for the output filenames")
    ARGS = ap.parse_args()
    if ARGS.tag:
        EVENT_LOG = LOG_DIR / f"spike_events_{ARGS.tag}.jsonl"
        RESULT_JSON = LOG_DIR / f"spike_result_{ARGS.tag}.json"
    sys.exit(asyncio.run(main()))
