"""agent/sarvam.py — THE single LLM client. Persona, referee, judge and synthesizer all use it.

Contract: docs/INTERFACES.md §4.5. Nobody writes a second HTTP client for Sarvam.

Facts this module is built on (docs/PREFLIGHT.md §5, measured):
  * sarvam-30b and sarvam-105b are REASONING models and reasoning CANNOT be disabled.
    `reasoning_effort` is not a lever: "none" is a 400, "low" produced MORE reasoning
    than baseline. We never send it.
  * Reasoning is written to `message.reasoning_content` and it consumes the max_tokens
    budget BEFORE `content` is written. Below ~1200 tokens `content` comes back None.
  * `content: None` with `finish_reason: "length"` is therefore a NORMAL, RETRYABLE
    outcome — this client returns it as `LLMResult(text=None, ...)` and never raises.
  * `reasoning_content` is captured for diagnostics and is NEVER returned as `text`,
    never appended to messages, never written into a transcript.

`complete()` does not retry. Retry policy belongs to the caller, because the correct
policy differs between the persona (§4.4) and the referee (§5.4).

AUTH — the one item INTERFACES §4.5 flagged as unverified. We try
`Authorization: Bearer <key>` first and fall back once to
`api-subscription-key: <key>` on a 401/403, then remember which one worked for the
lifetime of the client. `AUTH_STYLE_USED` records it process-wide so the run log can
report the answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from schema import Usage

log = logging.getLogger("voice_spar.sarvam")

SARVAM_BASE_URL = "https://api.sarvam.ai/v1"
CHAT_COMPLETIONS_PATH = "/chat/completions"

#: Set the first time a call succeeds: "bearer" or "api-subscription-key".
#: Reported by the runner so the OPEN ITEM in INTERFACES §4.5 can be closed with a fact.
AUTH_STYLE_USED: str | None = None


@dataclass(frozen=True)
class LLMConfig:
    provider: str            # "sarvam"
    model: str               # "sarvam-30b" | "sarvam-105b"  ("sarvam-m" is DEAD — 400)
    temperature: float
    max_tokens: int          # >= 2000 enforced at config load
    timeout_s: float = 120.0


@dataclass(frozen=True)
class LLMResult:
    text: str | None         # `content`; None is a normal, retryable outcome
    finish_reason: str       # "stop" | "length" | ...
    reasoning_content: str   # always captured, NEVER returned to the model
    usage: Usage
    latency_ms: int
    raw: dict[str, Any]


class LLMError(Exception):
    """Transport or HTTP failure. Carries `status_code` when the server answered.

    The callers' `_classify_exception()` duck-types on `.status_code`, so 429 and 5xx
    become retryable and everything else does not.

    `transport` is the SECOND duck-typed field and it exists because a timeout has no
    status code: without it, `status_code is None` collapsed every network failure into a
    NON-retryable `llm_call_failed` and the first Sarvam read-timeout killed a whole
    conversation on attempt 1 — contradicting the documented ladder ("Retryable: ... 429,
    5xx, timeout"). It is `"timeout"` for a read/connect/write timeout, `"transport"` for
    any other httpx transport failure, and None when the server actually answered.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, transport: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.transport = transport


class SarvamClient:
    """One shared httpx.AsyncClient for the whole run; one SarvamClient per role."""

    def __init__(
        self,
        api_key: str,
        cfg: LLMConfig,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = SARVAM_BASE_URL,
        label: str = "sarvam",
    ) -> None:
        if not api_key:
            raise ValueError("SARVAM_API_KEY is empty")
        self._api_key = api_key
        self.cfg = cfg
        self.label = label
        self.base_url = base_url.rstrip("/")
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=cfg.timeout_s)
        # Auth style for THIS client. Seeded from whatever already worked process-wide.
        self._auth_style: str = AUTH_STYLE_USED or "bearer"

    # ── the one call ─────────────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict | None = None,   # strict json_schema — verified working
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """One chat completion. Never retries. Never raises for `content: None`."""
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "max_tokens": int(max_tokens if max_tokens is not None else self.cfg.max_tokens),
            "temperature": float(
                temperature if temperature is not None else self.cfg.temperature
            ),
        }
        if response_format is not None:
            payload["response_format"] = response_format
        # NOTE: `reasoning_effort` is deliberately absent. It is not a lever (PREFLIGHT §5).

        url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"
        started = time.monotonic()
        data = await self._post_with_auth_fallback(url, payload)
        latency_ms = int((time.monotonic() - started) * 1000)

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{self.label}: response carried no choices: {str(data)[:300]}")
        choice = choices[0] or {}
        message = choice.get("message") or {}

        text = message.get("content")
        if text is not None and not isinstance(text, str):
            text = str(text)
        reasoning = message.get("reasoning_content") or ""
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)
        finish_reason = str(choice.get("finish_reason") or "")

        raw_usage = data.get("usage") or {}
        usage = Usage(
            calls=1,
            retries=0,
            prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
            completion_tokens=int(raw_usage.get("completion_tokens") or 0),
            reasoning_chars=len(reasoning),
            total_tokens=int(raw_usage.get("total_tokens") or 0),
        )

        log.debug(
            "%s %s: finish=%s content=%s reasoning_chars=%d %dms",
            self.label,
            self.cfg.model,
            finish_reason,
            "None" if text is None else f"{len(text)}c",
            len(reasoning),
            latency_ms,
        )
        return LLMResult(
            text=text,
            finish_reason=finish_reason,
            reasoning_content=reasoning,
            usage=usage,
            latency_ms=latency_ms,
            raw=data,
        )

    async def aclose(self) -> None:
        if self._owns_http:
            try:
                await self._http.aclose()
            except Exception:  # closing must never break a run
                pass

    # ── internals ────────────────────────────────────────────────────────────

    def _headers(self, style: str) -> dict[str, str]:
        if style == "bearer":
            return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        return {"api-subscription-key": self._api_key, "Content-Type": "application/json"}

    async def _post_with_auth_fallback(self, url: str, payload: dict) -> dict:
        """POST once. On 401/403 with the first style, try the other style exactly once."""
        global AUTH_STYLE_USED

        styles = [self._auth_style]
        other = "api-subscription-key" if self._auth_style == "bearer" else "bearer"
        if AUTH_STYLE_USED is None:
            styles.append(other)

        last_error: LLMError | None = None
        for style in styles:
            try:
                response = await self._http.post(
                    url,
                    headers=self._headers(style),
                    json=payload,
                    timeout=self.cfg.timeout_s,
                )
            except httpx.TimeoutException as exc:
                # transport="timeout" is what makes this RETRYABLE upstream. Do not drop it.
                raise LLMError(
                    f"{self.label}: request timed out: {exc}", transport="timeout"
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMError(
                    f"{self.label}: transport error: {type(exc).__name__}: {exc}",
                    transport="transport",
                ) from exc

            if response.status_code in (401, 403) and style != styles[-1]:
                log.info(
                    "%s: auth style %r rejected (%d); retrying with %r",
                    self.label, style, response.status_code, other,
                )
                last_error = LLMError(
                    f"{self.label}: auth style {style!r} rejected",
                    status_code=response.status_code,
                )
                continue

            if response.status_code >= 400:
                body = response.text[:400].replace(self._api_key, "***")
                raise LLMError(
                    f"{self.label}: HTTP {response.status_code} from Sarvam: {body}",
                    status_code=response.status_code,
                )

            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise LLMError(
                    f"{self.label}: non-JSON response: {response.text[:300]!r}"
                ) from exc
            if not isinstance(data, dict):
                raise LLMError(f"{self.label}: response was not a JSON object")

            self._auth_style = style
            if AUTH_STYLE_USED is None:
                AUTH_STYLE_USED = style
                log.info("Sarvam auth style that works: %r", style)
            return data

        assert last_error is not None
        raise last_error


__all__ = [
    "LLMConfig",
    "LLMResult",
    "LLMError",
    "SarvamClient",
    "SARVAM_BASE_URL",
    "AUTH_STYLE_USED",
]


if __name__ == "__main__":  # pragma: no cover — live smoke test
    import os
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        from dotenv import dotenv_values

        key = (dotenv_values(".env") or {}).get("SARVAM_API_KEY", "") or ""
    if not key:
        print("SARVAM_API_KEY not set", file=sys.stderr)
        raise SystemExit(1)

    async def _smoke() -> None:
        client = SarvamClient(key, LLMConfig("sarvam", "sarvam-30b", 0.9, 2000), label="smoke")
        try:
            r = await client.complete(
                [
                    {"role": "system", "content": "You are a customer on a phone call. Reply in one short line."},
                    {"role": "user", "content": "Hi Kunal, this is Tara from JioHotstar."},
                ]
            )
            print(f"auth={AUTH_STYLE_USED} finish={r.finish_reason} "
                  f"reasoning_chars={len(r.reasoning_content)} latency={r.latency_ms}ms")
            print(f"text: {r.text!r}")
            print(f"usage: {r.usage}")
        finally:
            await client.aclose()

    asyncio.run(_smoke())
