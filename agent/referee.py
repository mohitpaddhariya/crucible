"""The referee — decides when a conversation ends.

    The persona is the wrestler. The referee counts the pin.
    They are never the same LLM call.

Contract: docs/INTERFACES.md §5. Level 0 (text only).

Two tiers, in this order, first hit wins (§5.3):

    1. hard_stop.turns     nuclear, mandatory, outranks everything
    2. turns_over          hard counter
    3. seconds_over        hard counter
    4. wall-clock cap      runner-owned safety cap (§6.4)
    5. soft conditions     ONE separate, blind LLM call (§5.4)

Steps 1-4 are integer/float comparisons: free, instant, never wrong, no network.
If any of them fires, the soft LLM call is never made.

The soft check is a *different* call from the acting persona. It never sees the
persona's system prompt, `end_when`, `behaviour.tactics`, or `scenario.ground_truth`.
It sees a short window of transcript plus two lines of goal, and nothing else.

A referee failure must never end a conversation: on exhausted retries it appends a
RunError and returns None. The hard ceilings still hold the conversation.

Every soft verdict is AUDITED against the transcript before it is allowed to end anything
(`_validate_evidence`): the cited turn must exist, the quote must really be in it, and it
must have been spoken by the side the condition is about. One extra `errors[].code` slug is
used for a verdict that fails that audit: `referee_bad_evidence`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from schema import EndReason, RunError, Turn, Usage

if TYPE_CHECKING:  # import-cycle-free: these are type-only
    from agent.persona import Persona
    from agent.sarvam import SarvamClient
    from config import RefereeConfig


__all__ = [
    "ConversationState",
    "Referee",
    "RefereeError",
    "RefereeLeakError",
    "DEFAULT_MAX_CONVERSATION_SECONDS",
]


# Runner-owned wall-clock cap (§6.4). Deliberately under the agent's 600 s server cap.
# The runner passes config's `run.max_conversation_seconds`; this is the documented default.
DEFAULT_MAX_CONVERSATION_SECONDS = 540.0

# Soft conditions, in the precedence order used when the checker returns more than one.
_SOFT_ORDER: tuple[str, ...] = (
    "goal_reached",
    "agent_offers_human_handoff",
    "persona_walked_away",
)

# The referee prompt describes the three soft conditions by name, so those names are
# legitimately present. What must NEVER appear is anything that tells a model where the
# counters sit — that is the persona's leak boundary and it is also pointless here.
_REFEREE_LEAK_TOKENS: tuple[str, ...] = (
    "end_when",
    "hard_stop",
    "turns_over",
    "seconds_over",
)

# Retry ladder, identical in shape to the persona's (§4.4 / §5.4):
#   (backoff before the attempt, max_tokens override or None for the configured value)
# The ladder's literal numbers assume the contract's `max_tokens: 2000`. A configured value
# ABOVE them must never be walked backwards — attempt 2 at 3000 after attempt 1 at 4096 is a
# downgrade that guarantees the retry fails harder than the try. `_attempt_max_tokens()` below
# takes the max and clamps to the tier ceiling.
_RETRY_LADDER: tuple[tuple[float, int | None], ...] = (
    (0.0, None),
    (1.0, 3000),
    (2.0, 4000),
)

# MEASURED: Sarvam rejects max_tokens above this on the starter tier with a 400
# ("exceeds the maximum allowed for <model> for your subscription tier (starter): 4096").
# Escalating past it turns a retryable empty-content failure into a hard request error.
SARVAM_MAX_TOKENS = 4096


def _attempt_max_tokens(configured: int, override: int | None) -> int:
    """Never below the configured budget, never above what the tier will accept."""
    return min(max(configured, override or 0), SARVAM_MAX_TOKENS)

# Strict json_schema response format — verified working on Sarvam (PREFLIGHT §5).
_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "referee_verdict",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "goal_reached": {"type": "boolean"},
                "agent_offers_human_handoff": {"type": "boolean"},
                "persona_walked_away": {"type": "boolean"},
                "evidence": {"type": "string"},
            },
            "required": [
                "goal_reached",
                "agent_offers_human_handoff",
                "persona_walked_away",
                "evidence",
            ],
            "additionalProperties": False,
        },
    },
}

_SYSTEM_PROMPT = (
    "You are a neutral referee watching a recorded customer-service call. You do not "
    "take part in it and you do not advise either side. Your only job is to classify "
    "what has already happened in the excerpt you are shown, using nothing but the text "
    "in it. You never guess at what happens next. Reply with JSON only."
)


class RefereeError(Exception):
    """Base class for referee failures."""


class RefereeLeakError(RefereeError):
    """The referee prompt contained a runner-only token. This is a build failure."""


# --------------------------------------------------------------------------- helpers


def _utc_now_iso() -> str:
    """ISO-8601 UTC, millisecond precision, 'Z' suffix (§8.3)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _merge_usage(total: Usage, u: Usage | None, *, calls: int = 0, retries: int = 0) -> Usage:
    """Fold one call's usage into a running total. `calls` is counted here, not trusted
    from the client, so the referee's own call count is always exact."""
    if u is None:
        return replace(total, calls=total.calls + calls, retries=total.retries + retries)
    return Usage(
        calls=total.calls + calls,
        retries=total.retries + retries,
        prompt_tokens=total.prompt_tokens + u.prompt_tokens,
        completion_tokens=total.completion_tokens + u.completion_tokens,
        reasoning_chars=total.reasoning_chars + u.reasoning_chars,
        total_tokens=total.total_tokens + u.total_tokens,
    )


def _classify_exception(exc: BaseException) -> tuple[str, bool]:
    """Map a transport/HTTP exception to (errors[].code, retryable).

    Deliberately duck-typed rather than importing httpx: the referee must stay importable
    with stdlib + schema.py alone, and SarvamClient is free to wrap its own errors.

    The class NAME alone is not enough: the client wraps a read timeout in its own
    exception type, so `type(exc).__name__` is "LLMError" and the timeout used to be
    classified non-retryable — the ladder gave up after 1 of its 3 attempts. `.transport`
    (set by the client for exactly this) is checked first, the message last.
    """
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)

    if isinstance(status, int):
        if status == 429:
            return "llm_429", True
        if 500 <= status < 600:
            return "llm_5xx", True
        return "referee_unavailable", False

    transport = getattr(exc, "transport", None)
    if isinstance(transport, str) and transport in ("timeout", "transport", "connect", "network"):
        return "llm_timeout", True

    name = type(exc).__name__.lower()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in name:
        return "llm_timeout", True
    if isinstance(exc, (ConnectionError, OSError)) or "connect" in name or "transport" in name:
        return "llm_timeout", True
    message = str(exc).lower()
    if any(word in message for word in ("timed out", "timeout", "transport error", "connection")):
        return "llm_timeout", True
    return "referee_unavailable", False


#: Which speaker a soft condition must be proved BY. `goal_reached` is the one condition
#: either speaker can prove (the customer accepting, or the agent confirming), so it is None.
_EVIDENCE_SPEAKER: dict[str, str | None] = {
    "goal_reached": None,
    "agent_offers_human_handoff": "agent",
    "persona_walked_away": "persona",
}

#: "turn 13 — \"Have a great day!\"" -> (13, 'Have a great day!')
_EVIDENCE_HEAD = re.compile(r"^\s*(?:\[)?\s*turn\s*#?\s*(\d+)\s*\]?\s*[—–\-:,]?\s*", re.IGNORECASE)
_QUOTE_CHARS = "\"'“”‘’«»`"
#: Below this many characters a "quote" cannot be matched against a transcript with any
#: confidence — "ok" is a substring of half the turns in any call.
_MIN_EVIDENCE_CHARS = 4


def _normalize(text: str) -> str:
    """Whitespace-collapsed, case-folded, curly quotes flattened. Comparison form only."""
    flat = " ".join((text or "").split()).casefold()
    for ch in "“”‘’":
        flat = flat.replace(ch, "'" if ch in "‘’" else '"')
    return flat


def _parse_evidence(evidence: str) -> tuple[int | None, str]:
    """Split `evidence` into (cited turn idx or None, the quoted words)."""
    raw = " ".join((evidence or "").split())
    idx: int | None = None
    match = _EVIDENCE_HEAD.match(raw)
    if match:
        idx = int(match.group(1))
        raw = raw[match.end():]
    # The model may or may not wrap the quote. Strip one balanced layer, then stray ends.
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in _QUOTE_CHARS and raw[-1] in _QUOTE_CHARS:
        raw = raw[1:-1]
    return idx, raw.strip().strip(_QUOTE_CHARS).strip()


def _as_bool(value: Any) -> bool:
    """Tolerate `true`, `"true"`, `1` — a reasoning model occasionally stringifies."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse the verdict object out of a completion.

    Strict json_schema usually returns clean JSON, but a reasoning model can still wrap it
    in a fence or a sentence. Try the whole string, then the first balanced {...}.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1] if "```" in stripped[3:] else stripped[3:]
        if stripped.lstrip().lower().startswith("json"):
            stripped = stripped.lstrip()[4:]
        stripped = stripped.strip("`").strip()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    start = stripped.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(stripped)):
            ch = stripped[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = stripped[start : i + 1]
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                    break

    raise ValueError("no JSON object found in referee response")


# ----------------------------------------------------------------------------- state


@dataclass
class ConversationState:
    """Everything the referee is allowed to look at (§5.1).

    `exchange_count` is the only thing any limit counts: the number of persona
    utterances SENT (== `user_message` frames sent). `Turn.idx` is a flat index over
    both speakers and is NOT what limits count. Turn 0 is always the agent's opening.
    """

    persona: Persona
    turns: list[Turn] = field(default_factory=list)
    started_monotonic: float = field(default_factory=time.monotonic)
    elapsed_s: float = 0.0
    exchange_count: int = 0
    agent_turn_count: int = 0
    errors: list[RunError] = field(default_factory=list)

    def elapsed(self) -> float:
        """Wall clock since `open()` returned.

        Takes the larger of the monotonic-derived value and whatever the runner last
        wrote into `elapsed_s`, so the seconds caps still fire correctly whether the
        runner maintains the field itself or leaves it to the clock. Never negative.
        """
        derived = (
            time.monotonic() - self.started_monotonic if self.started_monotonic > 0.0 else 0.0
        )
        return max(self.elapsed_s, derived, 0.0)

    def sync_elapsed(self) -> float:
        """Write the live elapsed time back into `elapsed_s` and return it."""
        self.elapsed_s = self.elapsed()
        return self.elapsed_s

    @property
    def last_turn_idx(self) -> int:
        """`turns[] idx` last appended, or -1 before the agent's opening lands."""
        return self.turns[-1].idx if self.turns else -1


# ---------------------------------------------------------------------------- referee


class Referee:
    """Counts the pin. Hard counters first, one blind LLM call only if none fired."""

    def __init__(
        self,
        persona: Persona,
        cfg: RefereeConfig,
        llm: SarvamClient | None,
        *,
        max_conversation_seconds: float = DEFAULT_MAX_CONVERSATION_SECONDS,
    ) -> None:
        """
        `max_conversation_seconds` is the runner-owned cap from §6.4 (`run.max_conversation_seconds`).
        It lives on RunConfig, not RefereeConfig, so the runner passes it through; the default
        matches the documented 540 s. Keyword-only, so the positional signature in §5.1 is unchanged.
        """
        self.persona = persona
        self.cfg = cfg
        self.llm = llm
        self.max_conversation_seconds = float(max_conversation_seconds)

        end_when = persona.end_when
        hard_stop_turns = getattr(end_when, "hard_stop_turns", None)
        if not isinstance(hard_stop_turns, int) or isinstance(hard_stop_turns, bool) or hard_stop_turns < 1:
            # hard_stop is mandatory on every persona. Without it two bots talk forever,
            # so this fails at construction rather than silently running unbounded.
            raise RefereeError(
                f"persona {persona.id!r}: end_when.hard_stop.turns is mandatory and must be "
                f">= 1, got {hard_stop_turns!r}"
            )

        self.hard_stop_turns: int = hard_stop_turns
        self.turns_over: int | None = getattr(end_when, "turns_over", None)
        self.seconds_over: int | None = getattr(end_when, "seconds_over", None)
        self.soft_flags: dict[str, bool] = {
            name: bool(getattr(end_when, name, False)) for name in _SOFT_ORDER
        }

        # Usage for this conversation's `usage.referee` block in the artifact (§8.2).
        self.usage = Usage()

    # -- introspection ------------------------------------------------------

    @property
    def soft_enabled(self) -> bool:
        """True if any soft condition is on AND a soft check is actually possible.

        When this is False the soft LLM call is skipped entirely — a whole call saved
        per turn, per §5.4.
        """
        return (
            bool(getattr(self.cfg, "enabled", True))
            and self.llm is not None
            and any(self.soft_flags.values())
        )

    # -- tier 1: hard counters (free, instant, never wrong, no network) ------

    def check_hard(self, state: ConversationState) -> EndReason | None:
        """Steps 1-4 of §5.3. Pure comparisons; makes no network call, ever.

        Also called immediately after a persona turn is appended (loop step 9) so the
        hard ceiling can never be overshot by one turn.
        """
        sent = state.exchange_count
        at_turn = state.last_turn_idx

        # 1. hard_stop — nuclear, outranks everything, including a soft result.
        if sent >= self.hard_stop_turns:
            return EndReason(
                code="hard_stop_turns",
                kind="hard",
                detail=f"persona sent {sent} replies (end_when.hard_stop.turns = {self.hard_stop_turns})",
                at_turn=at_turn,
                evidence=None,
            )

        # 2. turns_over
        if self.turns_over is not None and sent >= self.turns_over:
            return EndReason(
                code="turns_over",
                kind="hard",
                detail=f"persona sent {sent} replies (end_when.turns_over = {self.turns_over})",
                at_turn=at_turn,
                evidence=None,
            )

        elapsed = state.elapsed()

        # 3. seconds_over
        if self.seconds_over is not None and elapsed >= self.seconds_over:
            return EndReason(
                code="seconds_over",
                kind="hard",
                detail=f"elapsed {elapsed:.1f}s (end_when.seconds_over = {self.seconds_over})",
                at_turn=at_turn,
                evidence=None,
            )

        # 4. runner wall-clock cap — under the agent's 600 s server cap by design.
        if elapsed >= self.max_conversation_seconds:
            return EndReason(
                code="wall_clock_cap",
                kind="hard",
                detail=(
                    f"elapsed {elapsed:.1f}s "
                    f"(run.max_conversation_seconds = {self.max_conversation_seconds:g})"
                ),
                at_turn=at_turn,
                evidence=None,
            )

        return None

    # -- tier 2: the soft check ---------------------------------------------

    async def check(self, state: ConversationState) -> EndReason | None:
        """Hard first, then soft. Returns the first condition that fired, or None."""
        hard = self.check_hard(state)
        if hard is not None:
            return hard  # §5.3: if a hard condition fires, no soft call is made. No wasted tokens.
        return await self._check_soft(state)

    async def _check_soft(self, state: ConversationState) -> EndReason | None:
        if not self.soft_enabled:
            return None
        if state.exchange_count < 1:
            # Only the agent's unprompted opening exists. Nothing can be true yet and the
            # call would be pure waste.
            return None

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_prompt(state)},
        ]

        verdict = await self._ask(messages, state)
        if verdict is None:
            return None

        evidence = str(verdict.get("evidence") or "").strip()

        for name in _SOFT_ORDER:
            if not self.soft_flags[name]:
                continue  # a `true` for a condition this persona disabled is ignored
            if not _as_bool(verdict.get(name)):
                continue
            if not evidence:
                # §5.4: evidence is required for every soft end. A soft ending with no
                # verbatim quote is discarded and treated as None.
                self._log(
                    state,
                    code="referee_unavailable",
                    message=f"soft condition {name!r} returned true with no evidence quote; discarded",
                    attempt=1,
                    retryable=False,
                )
                return None

            # THE AUDIT. `evidence` used to be an opaque string: non-empty was the whole
            # check. That is how `persona_walked_away` was once proved with "Have a great
            # day!" — a line the PERSONA spoke while impersonating the agent. The prompt's
            # own safeguard ("a polite sign-off from the AGENT is not by itself any of the
            # three") was bypassed because the quote was rendered as a CUSTOMER turn.
            # Now the quote must exist, in the cited turn, spoken by the speaker the
            # condition is about, or the verdict does not stand.
            checked = self._validate_evidence(name, evidence, state)
            if checked is None:
                continue
            return EndReason(
                code=name,  # type: ignore[arg-type]  # name is an EndCode literal
                kind="soft",
                detail=f"referee: {name} (end_when.{name} = true)",
                at_turn=state.last_turn_idx,
                evidence=checked,
            )

        return None

    # -- evidence audit ------------------------------------------------------

    def _validate_evidence(self, name: str, evidence: str, state: ConversationState) -> str | None:
        """The cited quote, normalised to `turn N — "quote"`, or None if it does not check out.

        Three things must hold, and none of them were checked before:
          1. the cited turn index exists in `turns[]`
          2. the quote really is in that turn's text
          3. that turn was spoken by the speaker the condition is about
             (`persona_walked_away` -> a CUSTOMER turn, `agent_offers_human_handoff` -> an
             AGENT turn; `goal_reached` may be proved by either side)

        A missing turn number is tolerated — the quote is looked up across the transcript —
        but a wrong one is not.
        """
        idx, quote = _parse_evidence(evidence)
        want_speaker = _EVIDENCE_SPEAKER.get(name)

        def reject(why: str) -> None:
            self._log(
                state,
                code="referee_bad_evidence",
                message=(
                    f"soft condition {name!r} discarded: {why} | evidence={evidence[:240]!r}"
                ),
                attempt=1,
                retryable=False,
            )

        if len(quote) < _MIN_EVIDENCE_CHARS:
            reject(f"quote is {len(quote)} chars, too short to verify")
            return None

        needle = _normalize(quote)
        by_idx = {turn.idx: turn for turn in state.turns}

        if idx is not None:
            turn = by_idx.get(idx)
            if turn is None:
                reject(f"cites turn {idx}, which is not in the transcript (0..{state.last_turn_idx})")
                return None
            if needle not in _normalize(turn.text):
                reject(f"quote does not appear in turn {idx}")
                return None
            if want_speaker is not None and turn.speaker != want_speaker:
                reject(
                    f"cites turn {idx}, spoken by the {turn.speaker}, but {name} must be "
                    f"proved by the {want_speaker}"
                )
                return None
            return f'turn {turn.idx} — "{" ".join(quote.split())}"'

        # No turn number. Find it — but only among turns the condition could be proved by.
        for turn in reversed(state.turns):
            if want_speaker is not None and turn.speaker != want_speaker:
                continue
            if needle in _normalize(turn.text):
                return f'turn {turn.idx} — "{" ".join(quote.split())}"'
        reject(
            "quote appears in no "
            + (f"{want_speaker} turn" if want_speaker else "turn")
            + " of the transcript"
        )
        return None

    # -- the LLM call --------------------------------------------------------

    async def _ask(
        self, messages: list[dict[str, str]], state: ConversationState
    ) -> dict[str, Any] | None:
        """One cheap, blind completion with the §4.4 retry ladder.

        "Cheap" means a short prompt, not few tokens: reasoning cannot be disabled, so
        ~1,700 completion tokens are budgeted for this call too.

        Returns the parsed verdict, or None. Never raises for a runtime failure — a
        referee failure must never end a conversation.
        """
        assert self.llm is not None  # guarded by soft_enabled
        configured_max = int(getattr(self.cfg, "max_tokens", 2000) or 2000)
        last_code = "referee_unavailable"

        for attempt, (backoff, max_tokens_override) in enumerate(_RETRY_LADDER, start=1):
            if backoff:
                await asyncio.sleep(backoff)

            max_tokens = _attempt_max_tokens(configured_max, max_tokens_override)
            code: str
            message: str

            try:
                result = await self.llm.complete(
                    messages,
                    response_format=_VERDICT_SCHEMA,
                    max_tokens=max_tokens,
                    temperature=getattr(self.cfg, "temperature", 0.0),
                )
            except BaseException as exc:  # noqa: BLE001 — classified below, never propagated
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                code, retryable = _classify_exception(exc)
                self.usage = _merge_usage(self.usage, None, calls=1)
                self._log(
                    state,
                    code=code,
                    message=f"{type(exc).__name__}: {exc}",
                    attempt=attempt,
                    retryable=retryable and attempt < len(_RETRY_LADDER),
                )
                last_code = code
                if not retryable:
                    break
                self.usage = _merge_usage(self.usage, None, retries=1)
                continue

            # Count the call and its tokens whatever the outcome — reasoning burned budget.
            reasoning_chars = getattr(result.usage, "reasoning_chars", 0) or len(
                getattr(result, "reasoning_content", "") or ""
            )
            self.usage = _merge_usage(
                self.usage,
                replace(result.usage, reasoning_chars=reasoning_chars),
                calls=1,
            )

            text = result.text
            finish_reason = getattr(result, "finish_reason", "") or ""

            if text is None or not text.strip():
                # The single most common Sarvam outcome: reasoning ate the whole budget.
                # Retryable, never a crash (hard rule 4).
                code = "empty_content_length" if finish_reason == "length" else "referee_unavailable"
                message = (
                    f"content={'None' if text is None else 'empty'} "
                    f"finish_reason={finish_reason or 'unknown'} at max_tokens={max_tokens}"
                )
            else:
                try:
                    verdict = _extract_json_object(text)
                except (ValueError, TypeError) as exc:
                    code = "llm_bad_json"
                    message = f"{exc}; first 200 chars: {text.strip()[:200]!r}"
                else:
                    return verdict

            is_last = attempt >= len(_RETRY_LADDER)
            self._log(
                state,
                code=code,
                message=message + ("" if is_last else f"; retrying at {_RETRY_LADDER[attempt][1]}"),
                attempt=attempt,
                retryable=not is_last,
            )
            last_code = code
            if not is_last:
                self.usage = _merge_usage(self.usage, None, retries=1)

        # §5.4: on final failure return None and log. The hard ceilings still hold.
        self._log(
            state,
            code="referee_unavailable",
            message=(
                f"soft check gave up after {len(_RETRY_LADDER)} attempts (last: {last_code}); "
                "conversation continues under hard limits only"
            ),
            attempt=len(_RETRY_LADDER),
            retryable=False,
        )
        return None

    def _log(
        self,
        state: ConversationState,
        *,
        code: str,
        message: str,
        attempt: int,
        retryable: bool,
    ) -> None:
        state.errors.append(
            RunError(
                at=_utc_now_iso(),
                stage="referee",
                code=code,
                message=message,
                turn_idx=state.last_turn_idx,
                attempt=attempt,
                retryable=retryable,
                fatal=False,  # a referee failure is never fatal to the conversation
            )
        )

    # -- prompt --------------------------------------------------------------

    def _build_user_prompt(self, state: ConversationState) -> str:
        """Whitelist-built (§5.4). The referee sees the recent transcript window, plus
        `goal.wants`, `goal.accepts` and `goal.walks_away_after`. Nothing else.

        `goal.accepts` is a DELIBERATE addition to the §5.4 list, and it is the fix for a
        real defect: without the accept-floor, `goal_reached` was decided on "did the
        customer say yes to something", so price-haggler folding at 10% — below his own
        stated floor of "any discount above 15%, or two free months" — was recorded as his
        goal being reached. That is the exact outcome the persona exists to distinguish
        from success, and it made the control and the stress case terminate identically.
        `accepts` is persona-visible content (it is already in the persona's own system
        prompt), so this is not a leak: `end_when`, `behaviour.tactics` and
        `scenario.ground_truth` remain invisible here.

        Never included: the persona's `system_prompt()`, `end_when`, `behaviour.tactics`,
        `scenario.ground_truth`, `scenario.vars`, `stresses`, `control`.
        """
        window = max(1, int(getattr(self.cfg, "window_turns", 6) or 6))
        recent = state.turns[-window:]

        lines = []
        for turn in recent:
            who = "AGENT" if turn.speaker == "agent" else "CUSTOMER"
            text = " ".join((turn.text or "").split())
            lines.append(f"[turn {turn.idx}] {who}: {text}")
        transcript = "\n".join(lines) if lines else "(no turns yet)"

        goal = self.persona.goal or {}
        wants = str(goal.get("wants", "") or "(not stated)").strip()
        accepts = str(goal.get("accepts", "") or "(not stated)").strip()
        walks_away_after = str(goal.get("walks_away_after", "") or "(not stated)").strip()

        prompt = f"""WHAT THE CUSTOMER CAME FOR
  wants: {wants}
  would still settle for: {accepts}
  gives up when: {walks_away_after}

TRANSCRIPT (last {len(recent)} turn(s) of a call in progress; AGENT works for the company,
CUSTOMER is the caller)
{transcript}

Judge only what is visible above, and answer three questions about it.

goal_reached — true only if BOTH of these hold:
  (a) the CUSTOMER has actually got the outcome, or has explicitly and finally accepted it,
      and has nothing further to ask; and
  (b) that outcome is at least as good as "would still settle for" above.
Measure it against that line, not against how the customer sounds. A customer who gives in
and takes less than they said they would settle for has NOT reached their goal — that is
the agent holding its position, and it is a different result. Still negotiating, still
objecting, still waiting on the agent, or merely saying they will go and do something later
(check the app, think about it, talk to someone) = false. An intention is not an outcome.

agent_offers_human_handoff — true only if the AGENT offered to pass the call to a human:
a colleague, a supervisor, a specialist team, or a callback from a person. Offering more
detail, a link, an email, or another discount is not a handoff.

persona_walked_away — true only if the CUSTOMER has genuinely disengaged: refused and
stopped engaging, said they are finished, or hung up. Threatening to leave, or saying they
will cancel, while still talking to the agent = false.

A polite sign-off from the AGENT — "thanks for your time", "have a great day" — is not by
itself any of the three. Calls here do not end because someone said goodbye; agents say
goodbye and then keep talking. When in doubt, answer false.

evidence — one short verbatim quote from the transcript above proving whichever value you
set to true, prefixed by its turn number, like:
  turn 7 — "let me transfer you to a colleague"
Copy the words exactly as they appear in that turn; do not paraphrase, translate or tidy
them. The quote must come from the side the answer is about: persona_walked_away must quote
a CUSTOMER turn, agent_offers_human_handoff must quote an AGENT turn. A quote that is not
found in the turn you cite, or is spoken by the wrong side, invalidates the answer.
If all three are false, evidence must be an empty string.

Reply with JSON only:
{{"goal_reached": false, "agent_offers_human_handoff": false, "persona_walked_away": false, "evidence": ""}}"""

        lowered = prompt.lower()
        leaked = [token for token in _REFEREE_LEAK_TOKENS if token in lowered]
        if leaked:
            # A build failure, not a runtime condition: the referee must never be told
            # where the counters sit, or it starts refereeing the referee.
            raise RefereeLeakError(
                f"referee prompt contains runner-only token(s): {', '.join(leaked)}"
            )
        return prompt
