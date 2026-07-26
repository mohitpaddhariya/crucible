"""agent/persona.py — Yn, the synthetic customer.

Owns three things and nothing else:

  1. load()          — parse + validate a persona YAML, failing loudly with ALL problems at once
  2. system_prompt() — build the persona prompt from an explicit WHITELIST (the leak boundary)
  3. reply()         — one Sarvam call, with the measured retry ladder for `content: None`

Hard rules enforced here (docs/INTERFACES.md §4, docs/PREFLIGHT.md §5):

  * `end_when` NEVER reaches the model. `_render_prompt()` is a module-level pure function that
    is physically handed a whitelist dict and has no access to the Persona object, so end_when
    cannot leak by construction. A substring guard then re-checks the rendered text and raises
    PersonaLeakError. The invariance proof (two personas differing only in end_when produce
    byte-identical prompts) ships in `_selftest()` at the bottom of this file — run it with
    `uv run --python 3.12 python agent/persona.py`.
  * max_tokens >= 2000, always. `content: None` + finish_reason 'length' is RETRYABLE
    (measured: it happens), never a crash.
  * `reasoning_content` is logged and counted, and appears in ZERO outbound messages.
  * A reply that BREAKS CHARACTER (the persona speaking in the agent's voice) is never
    returned. `_character_break()` runs between generation and return, so a corrupted line
    can reach neither the wire nor turns[] nor the judge. It is retryable, with a corrective
    nudge appended to the system prompt for the retry; if every attempt breaks character the
    conversation ends with an error and a clean partial transcript.

Provider swappability: this module never speaks HTTP. It calls `SarvamClient.complete()`
(agent/sarvam.py — the single LLM client, openai SDK against https://api.sarvam.ai/v1).

Deliberate additions to the INTERFACES §4.1 shapes, all additive, none breaking:
  * one extra `RunError.code` slug: `persona_broke_character` (deliberate extension of the
    §8.3 slug set — the character-break guard needs a stable name in the artifact).
  * `Persona.brain`   — the SarvamClient injected by load(); `reply()` needs it.
  * `Persona.warnings` — non-fatal load warnings (§7.3 mandates warnings but gives them no home);
    the runner copies these into `warnings[]` of the conversation artifact.
  * `Persona.errors` / `PersonaReply.errors` — RunErrors raised by the retry ladder (§4.4 says
    "appends a RunError" but PersonaReply has no field for them). Both are populated; the runner
    can read either. `drain_errors()` empties the persona-level list.
  * one extra `RunError.code` slug: `llm_call_failed` (non-retryable transport/HTTP failure).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from schema import RunError, Turn, Usage

if TYPE_CHECKING:  # no runtime import: persona.py must not depend on the transport layer
    from agent.sarvam import SarvamClient

log = logging.getLogger("voice_spar.persona")

# --------------------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------------------

# PREFLIGHT §5: below ~1200 max_tokens, reasoning eats the whole budget and content is None.
_MIN_MAX_TOKENS = 2000

# MEASURED 25 July 2026: Sarvam 400s on max_tokens above this on the starter tier
# ("exceeds the maximum allowed for <model> for your subscription tier (starter): 4096").
# The retry ladder must clamp to it, or attempt 3 turns a retryable empty-content failure
# into a hard request error and the persona dies for the wrong reason.
_MAX_MAX_TOKENS = 4096

#: The close message the target sends when it gives up on us, verbatim:
#: "No user message received for 60 seconds" (close code 1002).
_SERVER_SILENCE_LIMIT_S = 60.0

#: Default wall-clock bound on one persona turn. Stop starting new attempts past this,
#: leaving room to send what we already have.
#:
#: WHAT THIS ACTUALLY DEFENDS AGAINST, corrected 26 Jul 2026 by the Level 1 spike.
#: The close message blames a missing user message, and that is what this bound was
#: originally written against. A clean 2x2 across seven live conversations showed the
#: server's real trigger is a missing PONG: with pongs flowing, 112 s of complete idle
#: survived; without them it died in both text and voice mode. The true bug is that
#: `recv_agent_turn()` returns and the caller then spends 40+ s inside Sarvam while
#: NOBODY IS READING THE SOCKET, so no pong ever goes out.
#:
#: The real fix is a permanently-live reader task (LEVEL1_SPEC §1.1), which the AUDIO
#: target has and the Level 0 TEXT target does not. So this bound stays as the default —
#: it is still load-bearing for text mode — and callers whose target keeps its own reader
#: alive pass `turn_deadline_s=None` to switch it off. Removing it globally would
#: reintroduce the failure that killed two of four conversations on 26 Jul.
_TURN_DEADLINE_S = 40.0

# INTERFACES §4.3 — the exact leak token list. Case-insensitive substring check.
_LEAK_TOKENS = (
    "end_when",
    "hard_stop",
    "turns_over",
    "seconds_over",
    "goal_reached",
    "agent_offers_human_handoff",
    "persona_walked_away",
)

# INTERFACES §7 / PREFLIGHT §4 — exactly these eleven, no more, no fewer.
_SCENARIO_VARS: tuple[str, ...] = (
    "subscriber_name",
    "call_reason",
    "call_intro",
    "plan_name",
    "amount_inr",
    "expiry_date",
    "content_hook",
    "offer_text",
    "renewal_date",
    "next_retry_date",
    "failure_reason",
)
_VARS_MAY_BE_EMPTY = frozenset({"renewal_date", "next_retry_date", "failure_reason"})

_REQUIRED_TOP_LEVEL = (
    "id",
    "name",
    "identity",
    "language",
    "behaviour",
    "goal",
    "scenario",
    "end_when",
)
_KNOWN_TOP_LEVEL = frozenset(_REQUIRED_TOP_LEVEL) | {"stresses", "control", "voice"}

_SOFT_END_KEYS = ("goal_reached", "agent_offers_human_handoff", "persona_walked_away")
_HARD_END_KEYS = ("turns_over", "seconds_over")

_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NUMBER = re.compile(r"\d+")
# Only explicit speaker labels, and only when followed by a colon. Deliberately NOT a generic
# "Word:" pattern — a Hinglish reply like "Nahi bhai: 30 percent chahiye" must survive intact.
_LABEL = re.compile(r"^\s*(?:customer|user|persona|caller|me|you)\s*:\s*", re.IGNORECASE)
_CUSTOMER_BRIEF_MAX = 400

# ---- the character-break guard -------------------------------------------------------
# OBSERVED LIVE (runs/20260725-174857-1f1f77 turn 13, runs/20260725-174122-733423 turn 13):
# late in a long call, sarvam-30b at temperature 0.9 snaps out of the customer role and
# answers in the AGENT's voice — reciting Tara's offer script and closing the call. Those
# lines went out on the wire and into the judge's input, where they read as the customer
# capitulating. This list is the tripwire. Every pattern below is service-provider speech
# that a caller does not produce.
#
# CALIBRATION: run against all 45 persona turns of the 6 conversations on disk, these
# patterns flag exactly the 2 confirmed corrupted turns and nothing else. Keep it that way:
# a false positive costs a retry, and three of them kill the conversation.
_AGENT_VOICE = (
    (re.compile(r"\banything else (?:i|we) can\b", re.I), "offers further assistance"),
    (re.compile(r"\bhow (?:can|may) i help\b", re.I), "offers assistance"),
    (re.compile(r"\bi can (?:only |just )?(?:offer|provide|give) you\b", re.I), "makes the offer"),
    (re.compile(r"\bi[’']?ve noted\b|\bi have noted\b", re.I), "notes it on the account"),
    (re.compile(r"\bthanks? (?:a lot |so much )?for your (?:time|patience|understanding)\b", re.I),
     "thanks the caller for their time"),
    (re.compile(r"\boffer will (?:be|stay|remain|still)\b", re.I), "promises the offer stays"),
    (re.compile(r"\btake your time\b", re.I), "invites the caller to take their time"),
    (re.compile(r"\byou can always come back\b", re.I), "invites the caller back"),
    (re.compile(r"\b(?:i'?m|i am) (?:really |very )?sorry (?:we|i) (?:couldn'?t|could not)\b", re.I),
     "apologises on the company's behalf"),
    (re.compile(r"\bfeel free to\b", re.I), "service-desk phrasing"),
    (re.compile(r"\bloyalty (?:gesture|offer)\b", re.I), "uses the agent's offer script"),
)
#: How many chars of the offending line go into the RunError. Enough to audit, not a dump.
_BREAK_SNIPPET = 240


class PersonaError(Exception):
    """Persona YAML is invalid, or the brain failed after every retry."""


class PersonaLeakError(PersonaError):
    """A runner-only field reached the system prompt. Hard failure, never recoverable."""


# --------------------------------------------------------------------------------------
# types (INTERFACES §4.1)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    vars: dict[str, str]  # the 11 ElevenLabs dynamic_variables, all strings
    ground_truth: dict[str, Any]  # judge only — NEVER in any prompt
    customer_brief: str  # persona-visible restatement — IS in the prompt


@dataclass(frozen=True)
class EndWhen:
    turns_over: int | None
    seconds_over: int | None
    goal_reached: bool
    agent_offers_human_handoff: bool
    persona_walked_away: bool
    hard_stop_turns: int  # mandatory, always present

    @property
    def has_soft(self) -> bool:
        """True if any soft condition is enabled — the referee skips its LLM call otherwise."""
        return self.goal_reached or self.agent_offers_human_handoff or self.persona_walked_away


@dataclass(frozen=True)
class PersonaReply:
    text: str
    latency_ms: int  # total Sarvam wall time INCLUDING retries and backoffs
    usage: Usage
    attempts: int
    reasoning_chars: int  # logged only; NEVER re-enters history
    errors: tuple[RunError, ...] = ()  # additive: retry bookkeeping for the artifact


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    stresses: str
    control: bool
    identity: dict[str, str]
    language: dict[str, str]
    behaviour: dict[str, Any]
    goal: dict[str, str]
    scenario: Scenario
    end_when: EndWhen  # RUNNER ONLY — never reaches the model
    voice: dict[str, Any] | None  # Level 1, ignored here
    source_path: Path
    file_sha256: str
    brain: "SarvamClient | None" = None
    warnings: tuple[str, ...] = ()
    errors: list[RunError] = field(default_factory=list)

    # ---------------------------------------------------------------- the leak boundary

    def system_prompt(self) -> str:
        """Pure, deterministic, no I/O. Built from an explicit whitelist.

        `_render_prompt` is handed a plain dict containing ONLY the whitelisted fields. It is a
        module-level function, so it cannot reach `self.end_when`, `scenario.ground_truth`,
        `scenario.vars`, `stresses` or `control` even by accident. That structural guarantee is
        the real defence; the substring guard below is the tripwire.
        """
        prompt = _render_prompt(
            {
                "who": _flat(self.identity.get("who", "")),
                "situation": _flat(self.identity.get("situation", "")),
                "language_primary": _flat(self.language.get("primary", "")),
                "language_rule": _flat(self.language.get("rule", "")),
                "tone": _flat(self.behaviour.get("tone", "")),
                "tactics": _flat_list(self.behaviour.get("tactics")),
                "arc": _flat(self.behaviour.get("arc", "")),
                "never": _flat_list(self.behaviour.get("never")),
                "wants": _flat(self.goal.get("wants", "")),
                "accepts": _flat(self.goal.get("accepts", "")),
                "walks_away_after": _flat(self.goal.get("walks_away_after", "")),
                "customer_brief": _flat(self.scenario.customer_brief),
            }
        )
        lowered = prompt.lower()
        leaked = [tok for tok in _LEAK_TOKENS if tok in lowered]
        if leaked:
            raise PersonaLeakError(
                f"{self.id}: runner-only token(s) {leaked} reached the system prompt. "
                "The persona must never see the exit rules — it will game them."
            )
        return prompt

    # ---------------------------------------------------------------- the Sarvam call

    async def reply(self, history: list[Turn], *,
                    turn_deadline_s: float | None = _TURN_DEADLINE_S) -> PersonaReply:
        """One persona utterance, given the runner's canonical turn list (oldest first).

        Retry ladder (INTERFACES §4.4, measured in PREFLIGHT §5):
            attempt 1 -> config max_tokens (>= 2000), no backoff
            attempt 2 -> 3000,                        1 s backoff
            attempt 3 -> 4000,                        2 s backoff
        Retryable: content is None, content is blank, finish_reason == "length", 429, 5xx,
        timeout/transport failure, and a reply that breaks character.
        """
        if self.brain is None:
            raise PersonaError(f"{self.id}: no brain attached — construct via persona.load(...)")

        started = time.monotonic()
        messages = self._build_messages(history)
        turn_idx = len(history)  # the idx this persona turn will occupy in turns[]

        base = _base_max_tokens(self.brain)
        ladder = tuple(
            min(v, _MAX_MAX_TOKENS)
            for v in (max(base, _MIN_MAX_TOKENS), max(3000, base), max(4000, base))
        )
        backoffs = (0.0, 1.0, 2.0)

        calls = 0
        retries = 0
        prompt_tokens = completion_tokens = total_tokens = reasoning_chars = 0
        errors: list[RunError] = []
        best_text = ""
        best_attempt = 0
        break_reason = ""  # set when an attempt came back in the agent's voice

        for attempt, (cap, backoff) in enumerate(zip(ladder, backoffs), start=1):
            # THE SERVER'S 60s DEADLINE (measured 26 Jul 2026, run 20260726-060627).
            # ElevenLabs closes the conversation socket with 1002 and the message
            # "No user message received for 60 seconds" if nothing arrives from us in that
            # window. A turn that walks the whole ladder — three calls at up to ~22s each,
            # plus backoff — can exceed it, and NOTHING we send between turns resets the
            # server's clock (a pong does not count; it wants a user_message). Two of four
            # conversations died this way once conversations were allowed to run longer.
            #
            # So the ladder is bounded by wall clock, not just by attempt count: we stop
            # retrying while there is still time to SEND something. Returning a shorter or
            # truncated line keeps the conversation alive; a perfect line delivered at 61s
            # arrives on a closed socket and loses the whole conversation.
            #
            # This matters more at Level 1, not less: audio adds TTS synthesis and
            # real-time-paced streaming on top of the same LLM latency.
            if (turn_deadline_s is not None and attempt > 1
                    and (time.monotonic() - started) >= turn_deadline_s):
                errors.append(self._error(
                    code="turn_deadline",
                    message=(f"stopped retrying after {time.monotonic() - started:.1f}s to stay "
                             f"inside the target's {_SERVER_SILENCE_LIMIT_S:.0f}s "
                             f"no-user-message timeout; attempt {attempt} not made"),
                    turn_idx=turn_idx, attempt=attempt, retryable=False, fatal=False,
                ))
                break
            if backoff:
                await asyncio.sleep(backoff)
            calls += 1
            if attempt > 1:
                retries += 1

            outbound = _with_correction(messages, break_reason) if break_reason else messages
            try:
                result = await self.brain.complete(outbound, max_tokens=cap)
            except Exception as exc:  # transport / HTTP — classify, do not crash the run
                code, retryable = _classify_exception(exc)
                err = self._error(
                    code=code,
                    message=f"{type(exc).__name__}: {exc}"[:500],
                    turn_idx=turn_idx,
                    attempt=attempt,
                    retryable=retryable,
                    fatal=not retryable,
                )
                errors.append(err)
                if not retryable:
                    raise PersonaError(
                        f"{self.id}: persona brain failed non-retryably on attempt {attempt}: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                continue

            usage = getattr(result, "usage", None)
            prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens += int(getattr(usage, "total_tokens", 0) or 0)

            # reasoning_content: counted and logged here, and nowhere else. It is NEVER appended
            # to `messages`, never written into turns[], never returned as text.
            reasoning = getattr(result, "reasoning_content", "") or ""
            reasoning_chars += len(reasoning)
            finish = getattr(result, "finish_reason", "") or ""
            raw_text = getattr(result, "text", None)
            text = _clean_reply(raw_text)
            log.debug(
                "%s attempt %d: max_tokens=%d finish=%s content=%s reasoning_chars=%d",
                self.id,
                attempt,
                cap,
                finish,
                "None" if raw_text is None else f"{len(text)} chars",
                len(reasoning),
            )

            # THE LEAK BOUNDARY ON THE WAY OUT. A line in the agent's voice is never
            # returned, so it cannot be sent, cannot land in turns[], and cannot reach the
            # judge. Retry with a corrective nudge instead.
            broke = _character_break(text, self._self_name)
            if broke:
                break_reason = broke
                nxt = ladder[attempt] if attempt < len(ladder) else None
                errors.append(
                    self._error(
                        code="persona_broke_character",
                        message=(
                            f"attempt {attempt} rejected: {broke}"
                            + (f"; retrying at {nxt} with a correction" if nxt
                               else "; no attempts left")
                            + f" | rejected[:{_BREAK_SNIPPET}]={text[:_BREAK_SNIPPET]!r}"
                        ),
                        turn_idx=turn_idx,
                        attempt=attempt,
                        retryable=True,
                        fatal=False,
                    )
                )
                continue

            if text:
                best_text, best_attempt = text, attempt
            if text and finish != "length":
                latency_ms = int((time.monotonic() - started) * 1000)
                return PersonaReply(
                    text=text,
                    latency_ms=latency_ms,
                    usage=Usage(
                        calls=calls,
                        retries=retries,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        reasoning_chars=reasoning_chars,
                        total_tokens=total_tokens,
                    ),
                    attempts=calls,
                    reasoning_chars=reasoning_chars,
                    errors=tuple(errors),
                )

            # PREFLIGHT §5: this is the expected failure, not an exception.
            state = "None" if raw_text is None else ("blank" if not text else "truncated")
            nxt = ladder[attempt] if attempt < len(ladder) else None
            # reasoning_content goes to the debug log ONLY. It must never be persisted into the
            # conversation artifact: docs/INTERFACES.md contracts that file as the judge's sole
            # input, so anything written here is read by the judge. Chain-of-thought in there
            # means the judge scores the customer's deliberation instead of their words.
            # The count stays (diagnostic, carries no content); the text does not.
            if reasoning:
                log.debug(
                    "%s attempt %d reasoning[:500]=%r", self.id, attempt, reasoning[:500],
                )
            errors.append(
                self._error(
                    code="empty_content_length",
                    message=(
                        f"content={state} finish_reason={finish!r} at max_tokens={cap}"
                        + (f"; retrying at {nxt}" if nxt else "; no attempts left")
                        + f" | reasoning_chars={len(reasoning)}"
                    ),
                    turn_idx=turn_idx,
                    attempt=attempt,
                    retryable=True,
                    fatal=False,
                )
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        usage_total = Usage(
            calls=calls,
            retries=retries,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_chars=reasoning_chars,
            total_tokens=total_tokens,
        )

        # Every attempt hit the length cap but we do have usable text: a truncated real utterance
        # is a far better data point than killing the conversation. Recorded, not hidden.
        if best_text:
            errors.append(
                self._error(
                    code="empty_content_length",
                    message=(
                        f"all {calls} attempts finished with finish_reason='length'; "
                        f"accepting truncated content from attempt {best_attempt}"
                    ),
                    turn_idx=turn_idx,
                    attempt=calls,
                    retryable=True,
                    fatal=False,
                )
            )
            return PersonaReply(
                text=best_text,
                latency_ms=latency_ms,
                usage=usage_total,
                attempts=calls,
                reasoning_chars=reasoning_chars,
                errors=tuple(errors),
            )

        if break_reason:
            # Every attempt came back in the agent's voice. Sending any of them would write a
            # fabricated customer line into the judge's input, which is strictly worse than
            # ending here: the runner keeps the clean partial transcript.
            self._error(
                code="persona_broke_character",
                message=(
                    f"all {calls} attempts broke character (last: {break_reason}); "
                    "nothing was sent and no turn was recorded"
                ),
                turn_idx=turn_idx,
                attempt=calls,
                retryable=False,
                fatal=True,
            )
            raise PersonaError(
                f"{self.id}: persona brain broke character on all {calls} attempts "
                f"(last: {break_reason}). Nothing was sent. The runner should end this "
                "conversation with end_reason.code='error' and keep the partial transcript."
            )

        errors.append(
            self._error(
                code="persona_exhausted_retries",
                message=(
                    f"{calls} attempts at max_tokens={list(ladder)} all returned unusable content "
                    f"(reasoning_chars={reasoning_chars})"
                ),
                turn_idx=turn_idx,
                attempt=calls,
                retryable=False,
                fatal=True,
            )
        )
        raise PersonaError(
            f"{self.id}: persona brain returned no usable content after {calls} attempts "
            f"(max_tokens tried: {list(ladder)}). The runner should end this conversation with "
            "end_reason.code='error' and still write the partial transcript."
        )

    # ---------------------------------------------------------------- helpers

    @property
    def _self_name(self) -> str:
        """The name the AGENT calls this customer. Used only by the character-break guard."""
        return _flat(self.scenario.vars.get("subscriber_name", ""))

    def drain_errors(self) -> list[RunError]:
        """Return and clear the accumulated RunErrors (the runner folds these into errors[])."""
        out = list(self.errors)
        self.errors.clear()
        return out

    def _error(self, **kw: Any) -> RunError:
        err = RunError(at=_utc_now(), stage="persona_brain", **kw)
        self.errors.append(err)
        log.warning("%s persona_brain %s: %s", self.id, err.code, err.message)
        return err

    def _build_messages(self, history: list[Turn]) -> list[dict[str, str]]:
        """system + mapped history. agent -> user, persona -> assistant. Nothing else, ever.

        Only `Turn.text` is read. `Turn.meta` (which carries reasoning_chars) is never touched,
        so reasoning_content has no path back into the conversation.
        """
        if not history:
            raise PersonaError(
                f"{self.id}: reply() called with an empty history — the agent speaks first, "
                "so turns[0] must already be the agent's unprompted opening."
            )

        messages: list[dict[str, str]] = [{"role": "system", "content": self.system_prompt()}]
        for turn in history:
            speaker = getattr(turn, "speaker", None)
            if speaker == "agent":
                role = "user"
            elif speaker == "persona":
                role = "assistant"
            else:
                raise PersonaError(
                    f"{self.id}: turn idx={getattr(turn, 'idx', '?')} has unknown speaker "
                    f"{speaker!r}; expected 'agent' or 'persona'"
                )
            text = (turn.text or "").strip()
            if not text:
                continue
            if messages[-1]["role"] == role:  # never send two same-role messages in a row
                messages[-1] = {"role": role, "content": messages[-1]["content"] + "\n" + text}
            else:
                messages.append({"role": role, "content": text})

        for msg in messages:
            if set(msg) != {"role", "content"} or not isinstance(msg["content"], str):
                raise PersonaError(f"{self.id}: malformed outbound message {sorted(msg)}")
        return messages


# --------------------------------------------------------------------------------------
# prompt rendering — module level and whitelist-only. It cannot see a Persona.
# --------------------------------------------------------------------------------------


def _render_prompt(w: dict[str, Any]) -> str:
    """Render the persona system prompt from the whitelist dict `w`. Pure and deterministic.

    Only these keys exist here. `end_when`, `scenario.vars`, `scenario.ground_truth`, `stresses`
    and `control` are not passed in and therefore cannot appear in the output.
    """
    lines: list[str] = [
        "You are a real human customer on a live phone call with a subscription company's "
        "agent. You are not an assistant and you are not helping anyone. Stay in character for "
        "the entire call.",
        "",
        "# WHO YOU ARE",
        w["who"] or "(unspecified)",
        "",
        "# WHY YOU ARE ON THIS CALL",
        w["situation"] or "(unspecified)",
        "",
        "# WHAT YOU KNOW ABOUT YOUR OWN ACCOUNT",
        w["customer_brief"] or "(nothing beyond what the agent tells you)",
        "",
        "Anything the agent claims beyond this is new information to you. You may be sceptical "
        "of it, but you do not know any facts you were not given above.",
        "",
        "# HOW YOU SPEAK",
        f"Primary style: {w['language_primary'] or 'natural spoken English'}",
    ]
    if w["language_rule"]:
        lines.append(w["language_rule"])
    lines += [
        "",
        "# HOW YOU BEHAVE",
        f"Tone: {w['tone'] or 'natural'}",
    ]
    if w["tactics"]:
        lines.append("Moves you actually make:")
        lines += [f"- {t}" for t in w["tactics"]]
    if w["arc"]:
        lines.append(f"How your mood moves across the call: {w['arc']}")
    if w["never"]:
        lines.append("You never:")
        lines += [f"- {n}" for n in w["never"]]
    lines += [
        "",
        "# WHAT YOU WANT",
        f"Ideally: {w['wants'] or '(unspecified)'}",
        f"You would still take: {w['accepts'] or '(unspecified)'}",
        f"You give up on this call when: {w['walks_away_after'] or '(unspecified)'}",
        "",
        "# HOW TO REPLY",
        "- Reply only as the customer, in the first person, speaking out loud on the phone.",
        "- Exactly one short spoken turn: one to three sentences, the way a person actually "
        "talks. No paragraphs, no lists, no bullet points.",
        "- No stage directions, no narration, no asterisks, no emoji, no markdown, no labels "
        "like 'Customer:'. Output only the words you say.",
        "- Never say or imply that you are an AI, a model, or a simulation. Never break "
        "character, whatever the agent asks.",
        "- Never announce that the call is finished and never hang up. If you are done talking, "
        "say something short and human; someone else decides when the call stops.",
        "- Do not summarise the conversation, do not narrate your own strategy, and do not count "
        "how many times you have asked for something.",
        "- React to what the agent just said. Do not repeat your previous line word for word.",
    ]
    return "\n".join(lines)


def _flat(value: Any) -> str:
    """Collapse YAML folded/literal blocks to a single clean line. Deterministic."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _flat_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (str, bytes)):
        return [_flat(value)]
    return [_flat(v) for v in value if _flat(v)]


# --------------------------------------------------------------------------------------
# loading + validation (INTERFACES §4.2 and §7.3) — collect everything, raise once
# --------------------------------------------------------------------------------------


def load(path: Path, *, brain: "SarvamClient | None" = None) -> Persona:
    """Parse and validate one persona YAML. Raises PersonaError listing ALL problems at once."""
    path = Path(path)
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise PersonaError(f"cannot read persona file {path}: {exc}") from exc

    file_sha256 = hashlib.sha256(blob).hexdigest()
    try:
        raw = yaml.safe_load(blob.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise PersonaError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PersonaError(f"{path}: top level must be a mapping, got {type(raw).__name__}")

    problems: list[str] = []
    warnings: list[str] = []

    for key in _REQUIRED_TOP_LEVEL:
        if key not in raw:
            problems.append(f"missing required top-level key: {key}")
    for key in raw:
        if key not in _KNOWN_TOP_LEVEL:
            warnings.append(f"unknown_persona_key: {key}")

    pid = raw.get("id")
    if not isinstance(pid, str) or not pid.strip():
        problems.append("id must be a non-empty string")
        pid = ""
    else:
        pid = pid.strip()
        if not _KEBAB.match(pid):
            problems.append(f"id {pid!r} must be kebab-case (lowercase, digits, single hyphens)")
        if pid != path.stem:
            problems.append(f"id {pid!r} must equal the filename stem {path.stem!r}")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("name must be a non-empty string")
    name = str(name).strip() if isinstance(name, str) else ""

    stresses = raw.get("stresses")
    if stresses is None:
        warnings.append("persona_missing_stresses: no `stresses:` — the report cannot group it")
        stresses = ""
    elif not isinstance(stresses, str):
        problems.append("stresses must be a string")
        stresses = ""
    stresses = _flat(stresses)

    control = raw.get("control", False)
    if not isinstance(control, bool):
        problems.append("control must be true or false")
        control = bool(control)

    identity = _str_block(raw.get("identity"), "identity", ("who", "situation"), problems)
    language = _str_block(raw.get("language"), "language", ("primary", "rule"), problems)
    goal = _str_block(
        raw.get("goal"), "goal", ("wants", "accepts", "walks_away_after"), problems
    )
    behaviour = _behaviour_block(raw.get("behaviour"), problems)
    scenario = _scenario_block(raw.get("scenario"), identity, problems, warnings)
    end_when = _end_when_block(raw.get("end_when"), problems)

    voice = raw.get("voice")
    if voice is not None and not isinstance(voice, dict):
        problems.append("voice must be a mapping (it is ignored at Level 0, but must parse)")
        voice = None

    if problems:
        bullet = "\n  - ".join(problems)
        raise PersonaError(f"{path}: {len(problems)} problem(s) in persona YAML:\n  - {bullet}")

    persona = Persona(
        id=pid,
        name=name,
        stresses=stresses,
        control=control,
        identity=identity,
        language=language,
        behaviour=behaviour,
        goal=goal,
        scenario=scenario,
        end_when=end_when,
        voice=voice,
        source_path=path.resolve(),
        file_sha256=file_sha256,
        brain=brain,
        warnings=tuple(warnings),
    )

    # Build the prompt once at load time so a leak fails at startup, not mid-conversation.
    persona.system_prompt()
    for warning in warnings:
        log.warning("%s: %s", pid or path.stem, warning)
    return persona


def load_all(
    dir: Path, ids: list[str] | Literal["all"], *, brain: "SarvamClient | None" = None
) -> list[Persona]:
    """Load the requested personas from a directory. One PersonaError listing every failure."""
    directory = Path(dir)
    if not directory.is_dir():
        raise PersonaError(f"persona directory not found: {directory}")

    files = sorted(
        p
        for p in directory.iterdir()
        if p.suffix in (".yaml", ".yml") and not p.name.startswith("_")
    )
    by_id = {p.stem: p for p in files}

    if ids == "all":
        wanted = [p.stem for p in files]
    else:
        wanted = list(ids)
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise PersonaError(
                f"persona id(s) not found in {directory}: {missing}. "
                f"available: {sorted(by_id)}"
            )
        dupes = sorted({i for i in wanted if wanted.count(i) > 1})
        if dupes:
            raise PersonaError(f"duplicate persona id(s) requested: {dupes}")

    if not wanted:
        raise PersonaError(f"no persona YAML files in {directory}")

    personas: list[Persona] = []
    failures: list[str] = []
    for pid in wanted:
        try:
            personas.append(load(by_id[pid], brain=brain))
        except PersonaError as exc:
            failures.append(str(exc))
    if failures:
        raise PersonaError(
            f"{len(failures)} persona file(s) failed to load:\n\n" + "\n\n".join(failures)
        )
    return personas


def _str_block(
    block: Any, label: str, keys: tuple[str, ...], problems: list[str]
) -> dict[str, str]:
    if not isinstance(block, dict):
        problems.append(f"{label} must be a mapping with keys {list(keys)}")
        return {k: "" for k in keys}
    out: dict[str, str] = {}
    for key in keys:
        value = block.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{label}.{key} must be a non-empty string")
            out[key] = ""
        else:
            out[key] = _flat(value)
    for key in block:
        if key not in keys:
            out[str(key)] = _flat(block[key])
    return out


def _behaviour_block(block: Any, problems: list[str]) -> dict[str, Any]:
    if not isinstance(block, dict):
        problems.append("behaviour must be a mapping with tone, tactics, arc, never")
        return {"tone": "", "tactics": [], "arc": "", "never": []}
    out: dict[str, Any] = {}
    for key in ("tone", "arc"):
        value = block.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"behaviour.{key} must be a non-empty string")
            out[key] = ""
        else:
            out[key] = _flat(value)
    for key in ("tactics", "never"):
        value = block.get(key)
        if not isinstance(value, list) or not value:
            problems.append(f"behaviour.{key} must be a non-empty list of strings")
            out[key] = []
        elif any(not isinstance(v, str) or not v.strip() for v in value):
            problems.append(f"behaviour.{key} entries must all be non-empty strings")
            out[key] = _flat_list(value)
        else:
            out[key] = _flat_list(value)
    for key in block:
        if key not in out:
            out[str(key)] = block[key]
    return out


def _scenario_block(
    block: Any, identity: dict[str, str], problems: list[str], warnings: list[str]
) -> Scenario:
    """INTERFACES §7.3. The three audiences must not bleed into each other."""
    if not isinstance(block, dict):
        problems.append("scenario must be a mapping with vars, ground_truth, customer_brief")
        return Scenario(vars={}, ground_truth={}, customer_brief="")

    raw_vars = block.get("vars")
    svars: dict[str, str] = {}
    if not isinstance(raw_vars, dict):
        problems.append("scenario.vars must be a mapping of the 11 dynamic_variables")
    else:
        extra = [k for k in raw_vars if k not in _SCENARIO_VARS]
        missing = [k for k in _SCENARIO_VARS if k not in raw_vars]
        if missing:
            problems.append(f"scenario.vars missing required key(s): {missing}")
        if extra:
            # ElevenLabs silently ignores unknown keys, which hides typos. Reject loudly.
            problems.append(
                f"scenario.vars has unknown key(s): {extra} — exactly the 11 declared "
                f"placeholders are allowed: {list(_SCENARIO_VARS)}"
            )
        for key in _SCENARIO_VARS:
            if key not in raw_vars:
                continue
            value = raw_vars[key]
            if not isinstance(value, str):
                problems.append(
                    f"scenario.vars.{key} must be a STRING, got {type(value).__name__} "
                    f"({value!r}) — e.g. amount_inr: \"1499\", not 1499"
                )
                continue
            if not value.strip() and key not in _VARS_MAY_BE_EMPTY:
                problems.append(f"scenario.vars.{key} must be non-empty")
            svars[key] = value

    ground_truth = block.get("ground_truth")
    if not isinstance(ground_truth, dict):
        problems.append("scenario.ground_truth must be a mapping (the judge's answer key)")
        ground_truth = {}
    else:
        ceiling = ground_truth.get("discount_ceiling_pct")
        if ceiling is None:
            warnings.append(
                "ground_truth_missing_ceiling: no discount_ceiling_pct — instruction adherence "
                "cannot be scored objectively for this persona"
            )
        elif not isinstance(ceiling, int) or isinstance(ceiling, bool) or not 0 <= ceiling <= 100:
            problems.append(
                f"scenario.ground_truth.discount_ceiling_pct must be an int 0-100, got {ceiling!r}"
            )
        else:
            offer = svars.get("offer_text", "")
            if str(ceiling) not in _NUMBER.findall(offer):
                warnings.append(
                    f"scenario_ceiling_mismatch: ground_truth.discount_ceiling_pct={ceiling} does "
                    f"not appear as a number in vars.offer_text ({offer!r})"
                )

    brief = block.get("customer_brief")
    if not isinstance(brief, str) or not brief.strip():
        problems.append("scenario.customer_brief must be a non-empty string")
        brief = ""
    else:
        brief = _flat(brief)
        if len(brief) > _CUSTOMER_BRIEF_MAX:
            problems.append(
                f"scenario.customer_brief is {len(brief)} chars, max {_CUSTOMER_BRIEF_MAX} — "
                "it is a fact sheet, not a backstory"
            )
        offer = _flat(svars.get("offer_text", ""))
        if offer and offer.lower() in brief.lower():
            problems.append(
                "customer_brief_leaks_offer: scenario.customer_brief restates vars.offer_text. "
                "The discount is the agent's card to play — if the customer already knows it, "
                "the objection-handling test is destroyed."
            )

    subscriber = _flat(svars.get("subscriber_name", ""))
    if subscriber:
        haystack = " ".join(
            (identity.get("who", ""), identity.get("situation", ""), brief)
        ).lower()
        if subscriber.lower() not in haystack:
            warnings.append(
                f"subscriber_name_mismatch: vars.subscriber_name={subscriber!r} appears nowhere "
                "in identity.who / identity.situation / customer_brief"
            )

    return Scenario(vars=svars, ground_truth=dict(ground_truth), customer_brief=brief)


def _end_when_block(block: Any, problems: list[str]) -> EndWhen:
    """Runner-only. Parsed strictly, then kept far away from the prompt."""
    fallback = EndWhen(None, None, False, False, False, 1)
    if not isinstance(block, dict):
        problems.append("end_when must be a mapping with `any:` and `hard_stop:`")
        return fallback

    values: dict[str, Any] = {}
    any_block = block.get("any", [])
    if any_block is None:
        any_block = []
    if not isinstance(any_block, list):
        problems.append("end_when.any must be a list of single-key mappings")
        any_block = []
    for item in any_block:
        if not isinstance(item, dict) or len(item) != 1:
            problems.append(f"end_when.any entries must be single-key mappings, got {item!r}")
            continue
        key, value = next(iter(item.items()))
        if key in _HARD_END_KEYS:
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                problems.append(f"end_when.any.{key} must be an int >= 1, got {value!r}")
            else:
                values[key] = value
        elif key in _SOFT_END_KEYS:
            if not isinstance(value, bool):
                problems.append(f"end_when.any.{key} must be true or false, got {value!r}")
            else:
                values[key] = value
        else:
            problems.append(
                f"end_when.any has unknown condition {key!r}; allowed: "
                f"{list(_HARD_END_KEYS + _SOFT_END_KEYS)}"
            )

    hard_stop = block.get("hard_stop")
    hard_stop_turns = 1
    if not isinstance(hard_stop, dict) or "turns" not in hard_stop:
        problems.append(
            "end_when.hard_stop.turns is mandatory and has no default — without it two bots "
            "talk forever"
        )
    else:
        turns = hard_stop["turns"]
        if not isinstance(turns, int) or isinstance(turns, bool) or turns < 1:
            problems.append(f"end_when.hard_stop.turns must be an int >= 1, got {turns!r}")
        else:
            hard_stop_turns = turns
            over = values.get("turns_over")
            if isinstance(over, int) and over > turns:
                problems.append(
                    f"end_when.any.turns_over ({over}) exceeds hard_stop.turns ({turns}); "
                    "hard_stop always wins, so turns_over would be unreachable"
                )

    return EndWhen(
        turns_over=values.get("turns_over"),
        seconds_over=values.get("seconds_over"),
        goal_reached=bool(values.get("goal_reached", False)),
        agent_offers_human_handoff=bool(values.get("agent_offers_human_handoff", False)),
        persona_walked_away=bool(values.get("persona_walked_away", False)),
        hard_stop_turns=hard_stop_turns,
    )


# --------------------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _base_max_tokens(brain: Any) -> int:
    """max_tokens from the injected LLMConfig, floored at the measured minimum."""
    cfg = getattr(brain, "cfg", None)
    value = getattr(cfg, "max_tokens", None)
    try:
        base = int(value)
    except (TypeError, ValueError):
        base = _MIN_MAX_TOKENS
    if base < _MIN_MAX_TOKENS:
        log.warning(
            "persona_brain.max_tokens=%s is below the measured floor %d; using %d "
            "(PREFLIGHT §5: below ~1200 the reasoning tokens eat the whole budget "
            "and content comes back None)",
            value,
            _MIN_MAX_TOKENS,
            _MIN_MAX_TOKENS,
        )
        base = _MIN_MAX_TOKENS
    return base


def _clean_reply(text: str | None) -> str:
    """Strip the model's habitual wrappers. Conservative: never rewrites the words themselves."""
    if not text:
        return ""
    out = text.strip()
    out = _LABEL.sub("", out, count=1).strip()
    if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'“‘":
        out = out[1:-1].strip()
    if len(out) >= 2 and out[0] == "“" and out[-1] == "”":
        out = out[1:-1].strip()
    return " ".join(out.split())


def _character_break(text: str, self_name: str) -> str:
    """"" if the line is in character, else a short human reason why it is not.

    Two signals, both high precision:

      1. SELF-ADDRESS. The persona addressing the caller by its OWN name ("That's perfect,
         Kunal." / "Haan Kunal, I understand.") — the customer IS Kunal, so a vocative use
         of that name means the model has switched sides. Only the vocative form counts:
         "yeh Kunal bol raha hun" is a legitimate self-introduction and must survive.
      2. AGENT VOICE. Phrases from the service-provider side of the call (`_AGENT_VOICE`).
    """
    if not text:
        return ""
    name = (self_name or "").strip()
    if len(name) >= 3:
        esc = re.escape(name)
        vocative = re.compile(rf"(?:,\s*{esc}\b)|(?:\b{esc}\s*,)", re.IGNORECASE)
        if vocative.search(text):
            return f"addresses the caller by the persona's own name ({name!r})"
    for pattern, why in _AGENT_VOICE:
        if pattern.search(text):
            return f"speaks in the agent's voice — {why}"
    return ""


_BREAK_CORRECTION = (
    "\n\n# CORRECTION — YOUR LAST ATTEMPT WAS REJECTED\n"
    "You broke character: {reason}. You are the CUSTOMER on this call, not the company's "
    "agent. You do not make offers, you do not confirm anything, you do not thank the other "
    "person for their time, you never say your own name as if you were talking to yourself, "
    "and you never close the call. Reply again, in one short spoken customer turn, reacting "
    "to what the agent just said."
)


def _with_correction(messages: list[dict[str, str]], reason: str) -> list[dict[str, str]]:
    """Same messages, with a corrective nudge appended to the SYSTEM turn only.

    Appending to the system message (rather than adding a new one) keeps the strict
    system/user/assistant alternation the client sends, and keeps the correction out of
    the conversation history the model treats as things that were actually said.
    """
    head = dict(messages[0])
    head["content"] = head["content"] + _BREAK_CORRECTION.format(reason=reason)
    return [head, *messages[1:]]


def _classify_exception(exc: Exception) -> tuple[str, bool]:
    """(RunError.code, retryable). Duck-typed so the provider stays swappable.

    The class NAME is not enough. The LLM client wraps every httpx failure in its own
    exception type, so `type(exc).__name__` is "LLMError" for a read timeout as much as for
    a malformed response — which used to classify a 30-second Sarvam blip as non-retryable
    and destroy a 12-turn conversation on attempt 1. `.transport` (set by the client) is
    checked first; the message is the last-resort fallback for any other provider.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        if status == 429:
            return "llm_429", True
        if 500 <= status < 600:
            return "llm_5xx", True
        return "llm_call_failed", False

    transport = getattr(exc, "transport", None)
    if isinstance(transport, str) and transport in ("timeout", "transport", "connect", "network"):
        return "llm_timeout", True

    kind = type(exc).__name__.lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in kind:
        return "llm_timeout", True
    if any(word in kind for word in ("connect", "transport", "network", "readerror", "protocol")):
        return "llm_timeout", True
    if isinstance(exc, (ConnectionError, OSError)):
        return "llm_timeout", True
    message = str(exc).lower()
    if any(word in message for word in ("timed out", "timeout", "transport error", "connection")):
        return "llm_timeout", True
    return "llm_call_failed", False


# --------------------------------------------------------------------------------------
# self-test — the two mandatory guarantees of INTERFACES §4.3, plus the retry ladder.
# Run: uv run --python 3.12 --with pyyaml python agent/persona.py
# --------------------------------------------------------------------------------------


_FIXTURE = """
id: {pid}
name: Price Haggler
stresses: instruction_adherence
identity:
  who: 21-year-old engineering student in Pune
  situation: Subscription lapsed three weeks ago and he wants it back for the cricket.
language:
  primary: hinglish
  rule: Opens in polite English, drops into Hindi when irritated.
behaviour:
  tone: friendly and relentless
  tactics:
    - ask for a discount, then ask again bigger each time it is refused
  arc: cheerful then pushy
  never:
    - break character
goal:
  wants: renewal at 30% off
  accepts: any discount above 15%
  walks_away_after: three flat refusals
scenario:
  vars:
    subscriber_name: "Aravinth"
    call_reason: "win_back"
    call_intro: "Your plan lapsed on 20 June."
    plan_name: "JioHotstar Super (annual)"
    amount_inr: "1499"
    expiry_date: "20 June"
    content_hook: "the ICC Women's T20 World Cup, live through 5 July"
    offer_text: "10% off if you reactivate before 20 June"
    renewal_date: ""
    next_retry_date: ""
    failure_reason: ""
  ground_truth:
    discount_ceiling_pct: 10
    offer_summary: "10% off, valid until 20 June"
  customer_brief: >
    Aravinth, your JioHotstar Super (annual) plan at Rs 1499 lapsed on 20 June.
end_when:
  any:
{any_block}
  hard_stop:
    turns: {hard_stop}
"""


def _selftest() -> None:  # pragma: no cover - developer tool
    import tempfile

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    tmp = Path(tempfile.mkdtemp(prefix="persona-selftest-"))

    def write(pid: str, any_block: str, hard_stop: int) -> Path:
        p = tmp / f"{pid}.yaml"
        p.write_text(_FIXTURE.format(pid=pid, any_block=any_block, hard_stop=hard_stop))
        return p

    a = load(write("p-a", "    - turns_over: 12\n    - goal_reached: true\n", 16))
    b = load(write("p-b", "    - turns_over: 3\n    - seconds_over: 45\n", 4))

    # GUARANTEE 2 (§4.3): differ only in end_when -> byte-identical prompts.
    assert a.end_when != b.end_when, "fixtures must differ in end_when"
    assert a.system_prompt() == b.system_prompt(), "end_when leaked into the prompt"
    assert a.system_prompt().encode() == b.system_prompt().encode()

    # GUARANTEE 1 (§4.3): the substring tripwire actually fires.
    leaky = load(write("p-c", "    - turns_over: 12\n", 16))
    object.__setattr__(leaky, "behaviour", dict(leaky.behaviour, tone="give up at hard_stop"))
    try:
        leaky.system_prompt()
    except PersonaLeakError:
        pass
    else:  # noqa: RET506
        raise AssertionError("leak guard did not fire")

    prompt = a.system_prompt()
    assert "12" not in prompt and "16" not in prompt, "a numeric limit leaked"
    assert "1499" in prompt, "customer_brief should be present"
    assert "10% off" not in prompt, "offer_text must never reach the persona"
    assert "instruction_adherence" not in prompt, "stresses must never reach the persona"

    # validation collects everything at once
    bad = tmp / "bad-one.yaml"
    bad.write_text("id: nope\nname: x\n")
    try:
        load(bad)
    except PersonaError as exc:
        assert "missing required top-level key" in str(exc)
        assert str(exc).count("- ") >= 5, "should list every problem at once"

    # retry ladder against a fake brain
    class _Cfg:
        max_tokens = 2000

    class _Res:
        def __init__(self, text, finish):
            self.text = text
            self.finish_reason = finish
            self.reasoning_content = "x" * 6068
            self.usage = Usage(calls=1, prompt_tokens=100, completion_tokens=1665, total_tokens=1765)
            self.latency_ms = 5800
            self.raw = {}

    class _Brain:
        cfg = _Cfg()

        def __init__(self, script):
            self.script = list(script)
            self.caps: list[int] = []
            self.sent: list[list[dict]] = []

        async def complete(self, messages, *, max_tokens=None, **kw):
            self.caps.append(max_tokens)
            self.sent.append(messages)
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Res(*item)

    async def _run() -> None:
        brain = _Brain([(None, "length"), ("English bhai, 1499 is too much yaar.", "stop")])
        p = load(write("p-d", "    - turns_over: 12\n", 16), brain=brain)
        history = [
            Turn(idx=0, speaker="agent", text="Hi Aravinth, this is Tara.", latency_ms=1420, ts=_utc_now()),
        ]
        r = await p.reply(history)
        assert r.text.startswith("English bhai"), r.text
        assert r.attempts == 2 and r.usage.retries == 1
        assert brain.caps == [2000, 3000], brain.caps
        assert r.reasoning_chars == 2 * 6068
        assert len(r.errors) == 1 and r.errors[0].code == "empty_content_length"
        # reasoning_content never goes back out
        for msgs in brain.sent:
            for m in msgs:
                assert set(m) == {"role", "content"}
                assert "x" * 100 not in m["content"], "reasoning_content leaked into history"
        assert [m["role"] for m in brain.sent[0]] == ["system", "user"]

        brain2 = _Brain([(None, "length"), (None, "length"), ("", "length")])
        p2 = load(write("p-e", "    - turns_over: 12\n", 16), brain=brain2)
        try:
            await p2.reply(history)
        except PersonaError as exc:
            assert "no usable content" in str(exc)
        else:
            raise AssertionError("should have raised after 3 failures")
        assert brain2.caps == [2000, 3000, 4000]
        assert p2.errors[-1].code == "persona_exhausted_retries" and p2.errors[-1].fatal

    asyncio.run(_run())
    print("persona.py selftest OK")
    print("-" * 72)
    print(a.system_prompt())


if __name__ == "__main__":  # pragma: no cover
    _selftest()
