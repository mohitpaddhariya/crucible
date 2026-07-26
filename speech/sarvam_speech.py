"""speech/sarvam_speech.py — THE Sarvam speech layer. Bulbul TTS out, Saaras STT in.

Contract: docs/LEVEL1_SPEC.md §6 (file plan) and §10 step 1 (build gate). Nobody
writes a second HTTP client for Bulbul/Saaras, exactly as `agent/sarvam.py` is the
single client for chat completions. This module mirrors that module's shape: httpx,
`Authorization: Bearer`, one-shot auth-style fallback, a `status_code`/`transport`
carrying error class, and NO retry policy (the caller owns retries).

MEASURED FACTS this module is built on (`scripts/spike_sarvam_speech.py`, live):

  * TTS endpoint is `POST https://api.sarvam.ai/text-to-speech`. `/v1/text-to-speech`
    is a 404 — there is no `/v1` prefix on the speech APIs (unlike chat completions).
  * TTS response is JSON: `{"request_id": ..., "audios": ["<base64 WAV>", ...]}`.
    The audio is a full RIFF/WAVE file, base64'd, inside a LIST. Never a raw body.
  * **`speech_sample_rate` MUST be sent and MUST be 16000.** The API default is
    22050 and it returns HTTP 200 with a perfectly valid 22050 Hz WAV. Streamed to
    Tara as `pcm_16000` that is wrong-speed audio — no error, no warning, no
    exception, just a chipmunk the agent mis-transcribes. This is the single most
    dangerous default in the layer, so this module never omits the field and
    verifies the rate coming back off the wire before returning it.
  * `bulbul:v2` is the model. `bulbul:v1` is dead. `bulbul:v3` exists with a DISJOINT
    speaker roster and 6.76 s batch latency vs 1.08 s for v2 (LEVEL1_SPEC §9.7).
  * Text ceiling is 2500 characters (the API's own 400 message). Enforced here so a
    long persona reply fails loudly at the call site, not as a mystery 400.
  * STT endpoint is `POST https://api.sarvam.ai/speech-to-text`, **multipart**, form
    field name `file`. A JSON base64 body is a 400 (`body.file : Field required`).
  * `saarika:v2.5` is the ONLY live STT model. `saaras:v3` (which config.example.yaml
    currently names) does not exist and 400s.
  * Saaras **400s on headerless PCM** — "Failed to read the file, please check the
    audio format." `wrap_wav()` before every call is mandatory, not hygiene.
  * Saaras returns `{"request_id", "transcript", "language_code"}`. There is **no
    confidence field of any kind** (measured across every variant), which is why
    LEVEL1_SPEC §3.2 makes `provenance: "asr"` itself the uncertainty marker.
  * Both auth styles work on the speech APIs. Bearer is primary here; the
    `api-subscription-key` fallback is kept for parity with `agent/sarvam.py`.

STDLIB ONLY for audio. `wave`, `struct`, `base64` — no numpy, no scipy, no
soundfile. The pyproject `audio` extra is NOT needed and must NOT be added: both
sides of this pipe are already 16 kHz mono PCM16, so there is nothing to resample
and nothing to decode. If `uv sync` ever starts compiling a wheel for Level 1,
something has leaked in here.

`strip_riff()` WALKS THE CHUNK TABLE. Bulbul's header is 44 bytes today and every
spike artifact agrees, but "44" is a coincidence of the chunks it happens to emit
(`fmt ` + `data`). One `LIST`/`INFO` chunk — which encoders add for provenance
without changing anything else — shifts `data` and a hardcoded 44 would ship a few
milliseconds of header bytes as audio: a click at the head of every turn and a
permanent 1-sample phase error, with nothing anywhere to catch it.

Never logs or prints an API key. Error bodies are scrubbed before they are raised.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import struct
import time
import wave
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("voice_spar.speech")

SARVAM_SPEECH_BASE_URL = "https://api.sarvam.ai"
TTS_PATH = "/text-to-speech"        # NOT /v1/... — that is a 404 (measured)
STT_PATH = "/speech-to-text"

#: The only live models. See the module docstring — these are not preferences.
TTS_MODEL = "bulbul:v2"
STT_MODEL = "saarika:v2.5"

#: Tara's wire format, both directions: `pcm_16000` == 16 kHz mono signed 16-bit LE.
WIRE_SAMPLE_RATE = 16000
WIRE_CHANNELS = 1
WIRE_SAMPLE_WIDTH = 2               # bytes

#: The API's own documented ceiling, from its 400 message.
TTS_MAX_CHARS = 2500

#: Set the first time a speech call succeeds: "bearer" or "api-subscription-key".
SPEECH_AUTH_STYLE_USED: str | None = None

_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


# ======================================================================================
# errors
# ======================================================================================


class SpeechError(Exception):
    """Transport or HTTP failure from Bulbul/Saaras.

    Duck-types identically to `agent.sarvam.LLMError` so a caller's existing
    `_classify_exception()` (429 / 5xx / timeout retryable, everything else not)
    works on it unchanged: `.status_code` is set when the server answered,
    `.transport` is `"timeout"` or `"transport"` when it did not.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, transport: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.transport = transport


class AudioFormatError(ValueError):
    """The bytes are not the audio we require. Raised by `strip_riff`/`wrap_wav`.

    Deliberately NOT a `SpeechError`: a wrong container or a wrong sample rate is a
    bug or a silent API change, never something to retry.
    """


# ======================================================================================
# stdlib-only audio: RIFF in, RIFF out
# ======================================================================================


@dataclass(frozen=True)
class WavInfo:
    """What the chunk walk actually found. Every field is read, never assumed."""

    sample_rate: int
    channels: int
    sample_width: int                       # bytes per sample per channel
    audio_format: int                       # 1 = PCM
    data_offset: int                        # absolute byte offset of the data payload
    data_size: int                          # payload length actually available
    chunks: tuple[tuple[str, int], ...]     # (id, declared size) in file order

    @property
    def bits_per_sample(self) -> int:
        return self.sample_width * 8

    @property
    def duration_s(self) -> float:
        denom = self.sample_rate * self.channels * self.sample_width
        return (self.data_size / denom) if denom else 0.0


def parse_riff(data: bytes) -> WavInfo:
    """Walk a RIFF/WAVE chunk table and report what is really in it.

    Never assumes an offset. Handles the RF64-style padding rule (odd-sized chunks
    are followed by one pad byte), a `data` size that over-declares what the file
    actually contains (clamped to the real remainder), and any number of leading
    metadata chunks before `data`.
    """
    if len(data) < 12:
        raise AudioFormatError(f"too short to be RIFF: {len(data)} bytes")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioFormatError(
            f"not a RIFF/WAVE container: first 12 bytes = {data[:12]!r}"
        )

    chunks: list[tuple[str, int]] = []
    fmt: tuple[int, int, int, int] | None = None    # (format, channels, rate, width)
    data_offset: int | None = None
    data_size = 0

    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4].decode("ascii", "replace")
        (declared,) = struct.unpack_from("<I", data, pos + 4)
        body = pos + 8
        chunks.append((chunk_id, declared))

        if chunk_id == "fmt ":
            if declared < 16 or body + 16 > len(data):
                raise AudioFormatError(f"fmt chunk truncated (declared {declared} bytes)")
            audio_format, channels, rate, _byte_rate, _align, bits = struct.unpack_from(
                "<HHIIHH", data, body
            )
            fmt = (audio_format, channels, rate, bits // 8)
        elif chunk_id == "data":
            data_offset = body
            # A streaming writer can declare 0 or 0xFFFFFFFF. Trust the file, not the field.
            available = len(data) - body
            data_size = available if declared in (0, 0xFFFFFFFF) else min(declared, available)
            if declared > available:
                log.warning(
                    "RIFF data chunk declares %d bytes but only %d are present; clamping",
                    declared,
                    available,
                )

        pos = body + declared + (declared % 2)   # chunks are word-aligned
        if declared < 0 or pos <= body:          # defensive: a zero-size loop trap
            pos = body

    if fmt is None:
        raise AudioFormatError(
            f"no fmt chunk found; chunks = {[c[0] for c in chunks]}"
        )
    if data_offset is None:
        raise AudioFormatError(
            f"no data chunk found; chunks = {[c[0] for c in chunks]}"
        )

    audio_format, channels, rate, width = fmt
    if audio_format not in (_WAVE_FORMAT_PCM, _WAVE_FORMAT_EXTENSIBLE):
        raise AudioFormatError(
            f"not linear PCM: wFormatTag={audio_format} (only PCM is streamable to Tara)"
        )

    return WavInfo(
        sample_rate=rate,
        channels=channels,
        sample_width=width,
        audio_format=audio_format,
        data_offset=data_offset,
        data_size=data_size,
        chunks=tuple(chunks),
    )


def strip_riff(
    data: bytes,
    *,
    expect_sample_rate: int | None = WIRE_SAMPLE_RATE,
    expect_channels: int | None = WIRE_CHANNELS,
    expect_sample_width: int | None = WIRE_SAMPLE_WIDTH,
) -> bytes:
    """WAV bytes -> the raw PCM payload, by walking the chunk table.

    The output is exactly what goes into a `user_audio_chunk` frame: headerless,
    little-endian, signed 16-bit, mono, 16 kHz.

    **The header is not 44 bytes. The header is wherever the `data` chunk starts.**
    Bulbul emits `fmt `+`data` today, which lands `data` at 44 — that is an
    observation about one encoder on one day, not a property of the format. Passing
    `data[44:]` would silently prepend header bytes to the audio the moment a
    `LIST`/`INFO` chunk appears.

    The `expect_*` gates default to Tara's wire format and are the reason a 22050 Hz
    response (the API default, see the module docstring) becomes a loud
    `AudioFormatError` here instead of wrong-speed audio 40 lines later. Pass `None`
    to any of them to skip that check.
    """
    info = parse_riff(data)

    if expect_sample_rate is not None and info.sample_rate != expect_sample_rate:
        raise AudioFormatError(
            f"sample rate is {info.sample_rate} Hz, expected {expect_sample_rate} Hz — "
            "streaming this to a pcm_16000 socket plays at the wrong speed with no error "
            "(did speech_sample_rate get dropped from the TTS request?)"
        )
    if expect_channels is not None and info.channels != expect_channels:
        raise AudioFormatError(
            f"{info.channels} channels, expected {expect_channels} (mono)"
        )
    if expect_sample_width is not None and info.sample_width != expect_sample_width:
        raise AudioFormatError(
            f"{info.bits_per_sample}-bit samples, expected {expect_sample_width * 8}-bit"
        )

    pcm = data[info.data_offset : info.data_offset + info.data_size]
    frame_bytes = info.channels * info.sample_width
    if frame_bytes and len(pcm) % frame_bytes:
        # A partial frame at the tail would shift every subsequent sample by a byte.
        trimmed = len(pcm) - (len(pcm) % frame_bytes)
        log.warning("PCM payload had a partial trailing frame; trimming %d byte(s)", len(pcm) - trimmed)
        pcm = pcm[:trimmed]
    return pcm


def wrap_wav(
    pcm: bytes,
    *,
    sample_rate: int = WIRE_SAMPLE_RATE,
    channels: int = WIRE_CHANNELS,
    sample_width: int = WIRE_SAMPLE_WIDTH,
) -> bytes:
    """Raw PCM -> a RIFF/WAVE file, via stdlib `wave`. MANDATORY before any Saaras call.

    Saaras returns HTTP 400 "Failed to read the file, please check the audio format."
    for headerless PCM (measured). Every byte we capture off Tara's socket and every
    byte Bulbul hands back after `strip_riff` is headerless, so nothing reaches STT
    without passing through here.
    """
    if sample_width not in (1, 2, 3, 4):
        raise AudioFormatError(f"unsupported sample width: {sample_width} bytes")
    if channels < 1:
        raise AudioFormatError(f"unsupported channel count: {channels}")
    frame_bytes = channels * sample_width
    if len(pcm) % frame_bytes:
        raise AudioFormatError(
            f"PCM length {len(pcm)} is not a whole number of {frame_bytes}-byte frames"
        )

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def is_riff(data: bytes) -> bool:
    """Cheap container sniff, so callers can accept "WAV or PCM" without a try/except."""
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def pcm_duration_s(
    pcm: bytes,
    *,
    sample_rate: int = WIRE_SAMPLE_RATE,
    channels: int = WIRE_CHANNELS,
    sample_width: int = WIRE_SAMPLE_WIDTH,
) -> float:
    """Seconds of audio in a headerless PCM buffer. The playout budget in §1.2 uses this."""
    denom = sample_rate * channels * sample_width
    return (len(pcm) / denom) if denom else 0.0


# ======================================================================================
# config + results
# ======================================================================================


@dataclass(frozen=True)
class TTSConfig:
    model: str = TTS_MODEL
    speaker: str = "anushka"
    target_language_code: str = "hi-IN"
    #: MANDATORY 16000. Anything else is wrong-speed audio on Tara's socket (§6).
    sample_rate: int = WIRE_SAMPLE_RATE
    #: Bulbul's `pace` passthrough (personas/*.yaml `voice.pace`). UNVERIFIED live —
    #: omitted from the request entirely when None, which is the shipped default.
    pace: float | None = None
    timeout_s: float = 60.0


@dataclass(frozen=True)
class STTConfig:
    model: str = STT_MODEL
    #: "unknown" lets Saaras auto-detect, which is what Hinglish needs (measured:
    #: forcing hi-IN and auto-detect returned the same transcript on our fixtures).
    language_code: str = "unknown"
    timeout_s: float = 60.0


@dataclass(frozen=True)
class TTSResult:
    pcm: bytes                  # headerless, ready for `user_audio_chunk`
    wav: bytes                  # the container as returned, for artifact/debug writes
    sample_rate: int            # READ from the returned header, never assumed
    channels: int
    sample_width: int
    duration_s: float
    latency_ms: int
    chars: int
    model: str
    speaker: str
    request_id: str | None
    n_audio_parts: int          # len(audios[]); >1 means the API split the utterance


@dataclass(frozen=True)
class STTResult:
    text: str
    language_code: str | None   # what Saaras says it detected
    latency_ms: int
    model: str
    request_id: str | None
    raw: dict[str, Any]
    # NOTE: there is deliberately no `confidence` field. Saaras returns none, in any
    # variant (measured). LEVEL1_SPEC §3.2: provenance "asr" IS the uncertainty marker.


# ======================================================================================
# the shared HTTP base
# ======================================================================================


class _SarvamSpeechBase:
    """Auth, error classification and key scrubbing, shared by both clients.

    Same shape as `agent.sarvam.SarvamClient`: Bearer first, one fallback to
    `api-subscription-key` on 401/403, then remember what worked process-wide.
    """

    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.AsyncClient | None,
        base_url: str,
        timeout_s: float,
        label: str,
    ) -> None:
        if not api_key:
            raise ValueError("SARVAM_API_KEY is empty")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.label = label
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout_s)
        self._auth_style: str = SPEECH_AUTH_STYLE_USED or "bearer"

    async def aclose(self) -> None:
        if self._owns_http:
            try:
                await self._http.aclose()
            except Exception:      # closing must never break a run
                pass

    # ── internals ────────────────────────────────────────────────────────────

    def _auth_header(self, style: str) -> dict[str, str]:
        if style == "bearer":
            return {"Authorization": f"Bearer {self._api_key}"}
        return {"api-subscription-key": self._api_key}

    def _scrub(self, text: str) -> str:
        """No key ever reaches a log line, an exception message or an artifact."""
        return text.replace(self._api_key, "***") if self._api_key else text

    async def _post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        timeout_s: float,
    ) -> dict[str, Any]:
        """POST once per auth style. Returns the decoded JSON object.

        Does NOT retry — same division of labour as `agent/sarvam.py`: the retry
        policy differs between the persona path and the optional STT cross-check, so
        it belongs to the caller.
        """
        global SPEECH_AUTH_STYLE_USED

        styles = [self._auth_style]
        other = "api-subscription-key" if self._auth_style == "bearer" else "bearer"
        if SPEECH_AUTH_STYLE_USED is None:
            styles.append(other)

        url = f"{self.base_url}{path}"
        last_error: SpeechError | None = None

        for style in styles:
            headers = self._auth_header(style)
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            # Multipart: httpx must own Content-Type so it can set the boundary.
            try:
                response = await self._http.post(
                    url,
                    headers=headers,
                    json=json_body,
                    files=files,
                    data=data,
                    timeout=timeout_s,
                )
            except httpx.TimeoutException as exc:
                raise SpeechError(
                    f"{self.label}: request timed out: {self._scrub(str(exc))}",
                    transport="timeout",
                ) from exc
            except httpx.HTTPError as exc:
                raise SpeechError(
                    f"{self.label}: transport error: {type(exc).__name__}: "
                    f"{self._scrub(str(exc))}",
                    transport="transport",
                ) from exc

            if response.status_code in (401, 403) and style != styles[-1]:
                log.info(
                    "%s: auth style %r rejected (%d); retrying with %r",
                    self.label, style, response.status_code, other,
                )
                last_error = SpeechError(
                    f"{self.label}: auth style {style!r} rejected",
                    status_code=response.status_code,
                )
                continue

            if response.status_code >= 400:
                raise SpeechError(
                    f"{self.label}: HTTP {response.status_code} from Sarvam: "
                    f"{self._scrub(response.text[:400])}",
                    status_code=response.status_code,
                )

            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise SpeechError(
                    f"{self.label}: non-JSON response: {self._scrub(response.text[:300])!r}"
                ) from exc
            if not isinstance(payload, dict):
                raise SpeechError(f"{self.label}: response was not a JSON object")

            self._auth_style = style
            if SPEECH_AUTH_STYLE_USED is None:
                SPEECH_AUTH_STYLE_USED = style
                log.info("Sarvam speech auth style that works: %r", style)
            return payload

        assert last_error is not None
        raise last_error


# ======================================================================================
# Bulbul — text to speech
# ======================================================================================


class BulbulTTS(_SarvamSpeechBase):
    """Bulbul v2 over REST. One `synthesize()` call per persona turn.

    Measured: 0.85 s for a 49-char line, 1.29 s for a 288-char line, both at 16 kHz —
    a 0.07–0.25 realtime factor, so synthesis is never the bottleneck (§1.2).
    """

    def __init__(
        self,
        api_key: str,
        cfg: TTSConfig | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = SARVAM_SPEECH_BASE_URL,
        label: str = "bulbul",
    ) -> None:
        self.cfg = cfg or TTSConfig()
        super().__init__(
            api_key, http=http, base_url=base_url, timeout_s=self.cfg.timeout_s, label=label
        )
        if self.cfg.sample_rate != WIRE_SAMPLE_RATE:
            log.warning(
                "BulbulTTS configured at %d Hz, not %d Hz — Tara's socket is pcm_16000 and "
                "will play this at the wrong speed WITHOUT erroring (LEVEL1_SPEC §6)",
                self.cfg.sample_rate,
                WIRE_SAMPLE_RATE,
            )

    async def synthesize(
        self,
        text: str,
        *,
        speaker: str | None = None,
        sample_rate: int | None = None,
        pace: float | None = None,
        language_code: str | None = None,
    ) -> TTSResult:
        """Synthesise one utterance. Returns stripped PCM plus the WAV it came in.

        `speech_sample_rate` is ALWAYS sent. The returned header is then parsed and
        the rate compared against what we asked for, so the 22050 default can never
        reach the socket silently.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("BulbulTTS.synthesize: empty text")
        if len(text) > TTS_MAX_CHARS:
            raise ValueError(
                f"BulbulTTS.synthesize: text is {len(text)} chars; the API ceiling is "
                f"{TTS_MAX_CHARS} (persona replies are clamped to 200 by LEVEL1_SPEC §4.4)"
            )

        rate = sample_rate if sample_rate is not None else self.cfg.sample_rate
        spk = speaker or self.cfg.speaker
        body: dict[str, Any] = {
            "text": text,
            "target_language_code": language_code or self.cfg.target_language_code,
            "speaker": spk,
            "model": self.cfg.model,
            # NEVER omit. The default is 22050 and it fails silently (§6).
            "speech_sample_rate": int(rate),
        }
        effective_pace = pace if pace is not None else self.cfg.pace
        if effective_pace is not None:
            body["pace"] = float(effective_pace)

        started = time.monotonic()
        payload = await self._post(TTS_PATH, json_body=body, timeout_s=self.cfg.timeout_s)
        latency_ms = int((time.monotonic() - started) * 1000)

        audios = payload.get("audios")
        if not isinstance(audios, list) or not audios:
            raise SpeechError(
                f"{self.label}: response carried no audios[]: keys={list(payload)}"
            )

        try:
            parts = [base64.b64decode(a) for a in audios]
        except Exception as exc:      # noqa: BLE001 — malformed base64 is not retryable
            raise SpeechError(f"{self.label}: audios[] was not valid base64: {exc}") from exc

        # Every part is its own complete WAV. Strip each (chunk-table walk, validated
        # against the rate we asked for) and concatenate the PCM.
        pcm_parts: list[bytes] = []
        infos: list[WavInfo] = []
        for i, part in enumerate(parts):
            try:
                info = parse_riff(part)
                infos.append(info)
                pcm_parts.append(
                    strip_riff(
                        part,
                        expect_sample_rate=int(rate),
                        expect_channels=WIRE_CHANNELS,
                        expect_sample_width=WIRE_SAMPLE_WIDTH,
                    )
                )
            except AudioFormatError as exc:
                raise AudioFormatError(
                    f"{self.label}: audios[{i}] failed format check: {exc}"
                ) from exc

        pcm = b"".join(pcm_parts)
        head = infos[0]
        wav = parts[0] if len(parts) == 1 else wrap_wav(
            pcm,
            sample_rate=head.sample_rate,
            channels=head.channels,
            sample_width=head.sample_width,
        )

        result = TTSResult(
            pcm=pcm,
            wav=wav,
            sample_rate=head.sample_rate,
            channels=head.channels,
            sample_width=head.sample_width,
            duration_s=pcm_duration_s(
                pcm,
                sample_rate=head.sample_rate,
                channels=head.channels,
                sample_width=head.sample_width,
            ),
            latency_ms=latency_ms,
            chars=len(text),
            model=self.cfg.model,
            speaker=spk,
            request_id=payload.get("request_id"),
            n_audio_parts=len(parts),
        )
        log.debug(
            "%s %s/%s: %dc -> %.2fs of %dHz audio in %dms (%d part%s)",
            self.label, self.cfg.model, spk, len(text), result.duration_s,
            result.sample_rate, latency_ms, len(parts), "" if len(parts) == 1 else "s",
        )
        return result


# ======================================================================================
# Saaras — speech to text
# ======================================================================================


class SaarasSTT(_SarvamSpeechBase):
    """Saarika v2.5 over multipart REST. The optional agent-audio cross-check (§2.1).

    Its output is NEVER a transcript of record — `agent_response` is (LEVEL1_SPEC
    §2.1), because a mis-heard "10%" as "50%" would mint a false hallucination
    violation with the full weight of a deterministic fact.
    """

    def __init__(
        self,
        api_key: str,
        cfg: STTConfig | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = SARVAM_SPEECH_BASE_URL,
        label: str = "saaras",
    ) -> None:
        self.cfg = cfg or STTConfig()
        super().__init__(
            api_key, http=http, base_url=base_url, timeout_s=self.cfg.timeout_s, label=label
        )

    async def transcribe(
        self,
        audio: bytes,
        *,
        sample_rate: int = WIRE_SAMPLE_RATE,
        channels: int = WIRE_CHANNELS,
        sample_width: int = WIRE_SAMPLE_WIDTH,
        language_code: str | None = None,
        filename: str = "turn.wav",
    ) -> STTResult:
        """Transcribe WAV **or** headerless PCM. Headerless input is wrapped for you.

        Accepting both is the whole point: everything we capture off Tara's socket is
        headerless `pcm_16000`, and Saaras 400s on it. `wrap_wav()` is applied here so
        no call site can forget.
        """
        if not audio:
            raise ValueError("SaarasSTT.transcribe: empty audio")

        wav = audio if is_riff(audio) else wrap_wav(
            audio, sample_rate=sample_rate, channels=channels, sample_width=sample_width
        )

        form = {
            "model": self.cfg.model,
            "language_code": language_code or self.cfg.language_code,
        }
        started = time.monotonic()
        payload = await self._post(
            STT_PATH,
            files={"file": (filename, wav, "audio/wav")},   # field name is `file` (measured)
            data=form,
            timeout_s=self.cfg.timeout_s,
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        transcript = payload.get("transcript")
        if transcript is None:
            raise SpeechError(
                f"{self.label}: response carried no transcript: keys={list(payload)}"
            )

        result = STTResult(
            text=str(transcript),
            language_code=payload.get("language_code"),
            latency_ms=latency_ms,
            model=self.cfg.model,
            request_id=payload.get("request_id"),
            raw=payload,
        )
        log.debug(
            "%s %s: %.2fs audio -> %dc in %dms (lang=%s)",
            self.label, self.cfg.model,
            pcm_duration_s(wav, sample_rate=sample_rate, channels=channels, sample_width=sample_width),
            len(result.text), latency_ms, result.language_code,
        )
        return result


__all__ = [
    "SARVAM_SPEECH_BASE_URL",
    "TTS_PATH",
    "STT_PATH",
    "TTS_MODEL",
    "STT_MODEL",
    "TTS_MAX_CHARS",
    "WIRE_SAMPLE_RATE",
    "WIRE_CHANNELS",
    "WIRE_SAMPLE_WIDTH",
    "SPEECH_AUTH_STYLE_USED",
    "SpeechError",
    "AudioFormatError",
    "WavInfo",
    "parse_riff",
    "strip_riff",
    "wrap_wav",
    "is_riff",
    "pcm_duration_s",
    "TTSConfig",
    "STTConfig",
    "TTSResult",
    "STTResult",
    "BulbulTTS",
    "SaarasSTT",
]


# ======================================================================================
# selftest — `python speech/sarvam_speech.py` is OFFLINE and needs no credentials
# ======================================================================================

if __name__ == "__main__":  # pragma: no cover
    import asyncio
    import os
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent
    SPIKE = ROOT / "runs" / "_spike"

    _checks = {"pass": 0, "fail": 0}

    def check(name: str, ok: bool, detail: str = "") -> None:
        _checks["pass" if ok else "fail"] += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")

    def offline_selftest() -> int:
        """Zero network. Every assertion is READ from a file, never assumed."""
        print("=" * 78)
        print("OFFLINE SELFTEST — strip_riff / wrap_wav against the real spike artifacts")
        print("=" * 78)

        # 1. Every spike WAV: our chunk walk must agree with stdlib `wave`, byte for byte.
        wavs = sorted(SPIKE.glob("*.wav"))
        print(f"\n-- {len(wavs)} spike WAV(s) vs stdlib `wave` --")
        for path in wavs:
            raw = path.read_bytes()
            with wave.open(io.BytesIO(raw), "rb") as w:
                ref_rate, ref_ch, ref_w = w.getframerate(), w.getnchannels(), w.getsampwidth()
                ref_pcm = w.readframes(w.getnframes())
            info = parse_riff(raw)
            pcm = strip_riff(
                raw,
                expect_sample_rate=ref_rate,      # read from the file, not assumed
                expect_channels=ref_ch,
                expect_sample_width=ref_w,
            )
            chunk_ids = [c[0] for c in info.chunks]
            check(
                f"{path.name}: chunk walk == wave module",
                (info.sample_rate, info.channels, info.sample_width) == (ref_rate, ref_ch, ref_w)
                and pcm == ref_pcm,
                f"{info.sample_rate}Hz/{info.channels}ch/{info.bits_per_sample}bit "
                f"{len(pcm)}B data@{info.data_offset} chunks={chunk_ids}",
            )
            # round-trip: strip -> wrap -> strip is a fixed point
            rt = strip_riff(
                wrap_wav(pcm, sample_rate=ref_rate, channels=ref_ch, sample_width=ref_w),
                expect_sample_rate=ref_rate, expect_channels=ref_ch, expect_sample_width=ref_w,
            )
            check(f"{path.name}: strip->wrap->strip round-trip", rt == pcm)

        # 2. The 16 kHz wire artifact: the exact bytes Level 1 streams to Tara.
        print("\n-- runs/_spike/bulbul_tara_wire_16k.pcm (the wire format itself) --")
        pcm_path = SPIKE / "bulbul_tara_wire_16k.pcm"
        if not pcm_path.is_file():
            check("wire PCM artifact present", False, f"missing {pcm_path}")
        else:
            wire = pcm_path.read_bytes()
            wav = wrap_wav(wire)                       # defaults are the wire format
            # Assert the format by READING it back with stdlib `wave`. Never assume.
            with wave.open(io.BytesIO(wav), "rb") as w:
                rate, ch, width, nframes = (
                    w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
                )
                back = w.readframes(nframes)
            check("wrap_wav -> 16000 Hz (read via wave)", rate == 16000, f"got {rate}")
            check("wrap_wav -> mono (read via wave)", ch == 1, f"got {ch}")
            check("wrap_wav -> 16-bit (read via wave)", width == 2, f"got {width * 8}-bit")
            check("wrap_wav preserves every sample", back == wire, f"{len(wire)} bytes")
            check("strip_riff(wrap_wav(pcm)) == pcm", strip_riff(wav) == wire)
            check(
                "duration agrees with the byte count",
                abs(pcm_duration_s(wire) - nframes / rate) < 1e-9,
                f"{pcm_duration_s(wire):.4f}s",
            )
            info = parse_riff(wav)
            check(
                "our own header is 44 bytes (fmt + data) — and we still walked to it",
                info.data_offset == 44,
                f"chunks={[c[0] for c in info.chunks]}",
            )

        # 3. THE reason strip_riff walks: a LIST chunk in front of `data`.
        print("\n-- the 44-byte trap: a LIST/INFO chunk before `data` --")
        if pcm_path.is_file():
            wire = pcm_path.read_bytes()[:32000]        # 1 s is plenty
            plain = wrap_wav(wire)
            body = b"INFOISFT" + struct.pack("<I", 14) + b"voice-spar\x00\x00\x00\x00"
            listed = (
                plain[:12]
                + b"LIST" + struct.pack("<I", len(body)) + body
                + plain[12:]
            )
            listed = b"RIFF" + struct.pack("<I", len(listed) - 8) + listed[8:]
            info = parse_riff(listed)
            check(
                "LIST chunk shifts data off 44",
                info.data_offset != 44,
                f"data now at {info.data_offset}, chunks={[c[0] for c in info.chunks]}",
            )
            check("strip_riff still returns the exact PCM", strip_riff(listed) == wire)
            check(
                "a hardcoded [44:] would have been WRONG here",
                listed[44:] != wire,
                f"would have prepended {info.data_offset - 44} header bytes as audio",
            )
            # odd-sized chunk -> word-alignment pad byte
            odd_body = b"INFOISFT" + struct.pack("<I", 5) + b"abcde"   # 5 -> 1 pad byte
            odd = plain[:12] + b"LIST" + struct.pack("<I", len(odd_body)) + odd_body + b"\x00" + plain[12:]
            odd = b"RIFF" + struct.pack("<I", len(odd) - 8) + odd[8:]
            check("odd-sized chunk padding handled", strip_riff(odd) == wire)

        # 4. The guard rails: wrong rate / wrong container / not PCM must all raise.
        print("\n-- the guards (each of these must RAISE) --")
        silence = b"\x00\x00" * 1600
        wrong = wrap_wav(silence, sample_rate=22050)     # the dangerous API default
        try:
            strip_riff(wrong)
            check("22050 Hz WAV rejected by strip_riff", False, "it returned instead of raising")
        except AudioFormatError as exc:
            check("22050 Hz WAV rejected by strip_riff", True, str(exc)[:70] + "…")
        try:
            strip_riff(b"not a wav at all, honestly")
            check("non-RIFF rejected", False)
        except AudioFormatError:
            check("non-RIFF rejected", True)
        try:
            strip_riff(b"RIFF\x00\x00\x00\x00WAVE")      # header only, no chunks
            check("RIFF with no fmt/data rejected", False)
        except AudioFormatError:
            check("RIFF with no fmt/data rejected", True)
        try:
            wrap_wav(b"\x00\x00\x00")                    # 3 bytes = 1.5 frames
            check("partial-frame PCM rejected by wrap_wav", False)
        except AudioFormatError:
            check("partial-frame PCM rejected by wrap_wav", True)
        try:
            strip_riff(wrap_wav(silence, channels=2), expect_channels=1)
            check("stereo rejected when mono expected", False)
        except AudioFormatError:
            check("stereo rejected when mono expected", True)

        # 5. Speed — this sits on the per-turn hot path (§1.2 budgets ~70 µs).
        if pcm_path.is_file():
            big = wrap_wav(pcm_path.read_bytes())
            t0 = time.perf_counter()
            for _ in range(200):
                strip_riff(big)
            per_call_us = (time.perf_counter() - t0) / 200 * 1e6
            check("strip_riff is hot-path cheap", per_call_us < 500, f"{per_call_us:.1f} µs/call")

        print(f"\n{_checks['pass']} passed, {_checks['fail']} failed")
        return 0 if _checks["fail"] == 0 else 1

    async def live_selftest() -> int:
        """ONE Bulbul call and ONE Saaras call. The entire quota spend for this file."""
        from dotenv import dotenv_values

        key = os.environ.get("SARVAM_API_KEY") or (
            dotenv_values(ROOT / ".env", interpolate=False) or {}
        ).get("SARVAM_API_KEY") or ""
        if not key:
            print("SARVAM_API_KEY not set — skipping live half", file=sys.stderr)
            return 1
        key = key.strip()

        line = "Arre yaar, 10% off is not enough for the cricket."
        print("\n" + "=" * 78)
        print("LIVE SELFTEST — one Bulbul call, one Saaras call")
        print("=" * 78)
        print(f"  intended: {line!r} ({len(line)} chars, {len(line.split())} words)")

        tts = BulbulTTS(key)
        stt = SaarasSTT(key)
        try:
            r = await tts.synthesize(line)
            print(
                f"\n  TTS  {r.model}/{r.speaker}: {r.latency_ms} ms -> "
                f"{len(r.pcm)} B PCM = {r.duration_s:.2f} s @ {r.sample_rate} Hz "
                f"{r.channels}ch {r.sample_width * 8}-bit (parts={r.n_audio_parts})"
            )
            check("TTS returned 16000 Hz", r.sample_rate == 16000, f"got {r.sample_rate}")
            check("TTS returned mono 16-bit", r.channels == 1 and r.sample_width == 2)
            check("PCM is headerless", not is_riff(r.pcm))

            # Round-trip through the mandatory wrapper — headerless input on purpose,
            # which is exactly what a captured Tara turn looks like.
            s = await stt.transcribe(r.pcm)
            print(f"\n  STT  {s.model}: {s.latency_ms} ms, lang={s.language_code}")
            print(f"  heard: {s.text!r}")
            check("STT returned a non-empty transcript", bool(s.text.strip()))
            check("the number survived the round-trip", "10" in s.text, f"heard {s.text!r}")
            print(f"\n  auth style that worked: {SPEECH_AUTH_STYLE_USED!r}")
        finally:
            await tts.aclose()
            await stt.aclose()
        return 0

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rc = offline_selftest()
    if "--live" in sys.argv:
        rc |= asyncio.run(live_selftest())
        print(f"\n{_checks['pass']} passed, {_checks['fail']} failed (offline + live)")
    sys.exit(rc)
