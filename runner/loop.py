"""runner/loop.py — one persona, one conversation, one artifact on disk.

Contract: docs/INTERFACES.md §6 and §8.

The order of operations in `run_conversation()` is §6.1 verbatim, and the two rules that
make or break it are:

  * THE AGENT SPEAKS FIRST. `open()` does not consume the opening turn; the first thing
    we do afterwards is `recv_agent_turn(25)`. Sending first deadlocks turn one.
  * A crashed conversation is a data point, not a lost run. EVERY exception path still
    writes a complete `conversations/<persona_id>.json`.

Stages talk through files. This module writes; the judge reads, later, from disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.persona import Persona, PersonaError
from agent.referee import ConversationState, Referee
from agent.sarvam import SarvamClient
from config import Config
from schema import SCHEMA_VERSION, EndReason, RunError, Turn, Usage
from targets.base import TargetClosed, TargetError, TargetProtocolError, TargetTimeout
from targets.elevenlabs import ElevenLabsTarget

log = logging.getLogger("voice_spar.loop")

OPENING_TIMEOUT_S = 25.0   # measured: the opening lands in ~1-2 s (spike §3)
TURN_TIMEOUT_S = 90.0      # measured: ~1 s/turn from Tara (spike §6). Deliberately generous.


def utc_now() -> str:
    """ISO-8601 UTC, millisecond precision, 'Z' suffix (§8.3)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ======================================================================================
# Budget — shared across every conversation in the run, and it ABORTS, it does not warn
# ======================================================================================


class BudgetTracker:
    """One per run. Updated after every LLM call; read before every LLM call.

    When `budget_inr` is exhausted, in-flight conversations end with `budget_exceeded`
    and not-yet-started personas are recorded in `run.json` under `skipped[]`.

    If a model has no price in `config.yaml pricing:` its cost is None (§8.3: never
    silently zero) and `unpriced_models` records it — a run whose rates are all 0.0 has
    an INERT budget guard, and `run.py` refuses to start such a run unless the operator
    passes `--allow-inert-budget`.

    EVERY model that spends tokens must be charged here. The referee is a per-turn call on
    the more expensive model (105b): leaving it uncharged understated the guard's input by
    ~37% of tokens on a measured run, so `spent_inr` disagreed with the per-conversation
    `cost_inr` printed next to it and the cap could be blown by 40-60%.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self.limit_inr = float(cfg.run.budget_inr)
        self.spent_inr = 0.0
        self.unpriced_models: set[str] = set()
        #: Every model this run will actually bill for. Used by `inert`, which must answer
        #: correctly BEFORE the first call, i.e. before `unpriced_models` has been touched.
        self.models_in_use: tuple[str, ...] = (cfg.persona_brain.model, cfg.referee.model)
        self._lock = asyncio.Lock()

    def cost_of(self, model: str, usage: Usage) -> float | None:
        """INR for one usage block, or None when the model is unpriced."""
        rate_in = self._cfg.price_per_1m(model, "input")
        rate_out = self._cfg.price_per_1m(model, "output")
        if rate_in is None or rate_out is None:
            self.unpriced_models.add(model)
            return None
        return (usage.prompt_tokens * rate_in + usage.completion_tokens * rate_out) / 1_000_000.0

    async def charge(self, model: str, usage: Usage) -> float | None:
        cost = self.cost_of(model, usage)
        if cost is None:
            return None
        async with self._lock:
            self.spent_inr += cost
        return cost

    @property
    def exceeded(self) -> bool:
        return self.spent_inr >= self.limit_inr

    @property
    def inert(self) -> bool:
        """True when the guard cannot possibly fire, decided from config alone.

        Any model that will spend tokens without a usable rate makes the cap unenforceable:
        its spend is invisible, so `exceeded` can stay False no matter how much is burned.
        This is a pre-flight fact, not a post-hoc observation — the old version read
        `unpriced_models`, which is empty until the first call has already been paid for.
        """
        if self.limit_inr <= 0:
            return True
        for model in self.models_in_use:
            if (self._cfg.price_per_1m(model, "input") is None
                    or self._cfg.price_per_1m(model, "output") is None):
                return True
        return False

    def summary(self) -> dict[str, Any]:  # noqa: D102
        return {
            "budget_inr": self.limit_inr,
            "spent_inr": round(self.spent_inr, 6),
            "exceeded": self.exceeded,
            "unpriced_models": sorted(self.unpriced_models),
        }


# ======================================================================================
# Result
# ======================================================================================


def _parts_meta(agent_turn: Any) -> dict[str, int]:
    """`{"agent_response_parts": n}` when a turn arrived as several frames, else `{}`.

    Absent in the normal case so the artifact shape is unchanged; present, and therefore
    auditable, whenever the target had to merge a filler utterance with the real answer.
    """
    parts = int(getattr(agent_turn, "parts", 1) or 1)
    return {"agent_response_parts": parts} if parts > 1 else {}


def _usage_delta(now: Usage, charged: Usage) -> Usage:
    """`now - charged`, field by field. `Referee.usage` is cumulative, so only the new part
    may be charged to the shared budget — charging the running total would compound it."""
    return Usage(
        calls=now.calls - charged.calls,
        retries=now.retries - charged.retries,
        prompt_tokens=now.prompt_tokens - charged.prompt_tokens,
        completion_tokens=now.completion_tokens - charged.completion_tokens,
        reasoning_chars=now.reasoning_chars - charged.reasoning_chars,
        total_tokens=now.total_tokens - charged.total_tokens,
    )


@dataclass
class ConversationResult:
    persona_id: str
    ok: bool
    end_reason: EndReason
    artifact_path: Path
    conversation_id: str | None
    turn_count: dict[str, int]
    duration_s: float
    usage: dict[str, Usage] = field(default_factory=dict)
    cost_inr: float | None = None
    errors: list[RunError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ======================================================================================
# The loop
# ======================================================================================


async def run_conversation(
    persona: Persona,
    *,
    cfg: Config,
    run_id: str,
    run_dir: Path,
    budget: BudgetTracker,
    referee_llm: SarvamClient | None,
    agent_info: dict[str, Any],
    on_event=None,
) -> ConversationResult:
    """Hold one conversation and write its artifact. Never raises — always writes."""

    def emit(kind: str, message: str) -> None:
        if on_event is not None:
            on_event(persona.id, kind, message)

    turns: list[Turn] = []
    errors: list[RunError] = list()
    warnings: list[str] = list(persona.warnings)
    persona_usage = Usage()
    end_reason: EndReason | None = None
    conversation_id: str | None = None

    raw_log = run_dir / "raw" / f"{persona.id}.jsonl"
    prompt_path = run_dir / "prompts" / f"{persona.id}.system.txt"
    artifact_path = run_dir / "conversations" / f"{persona.id}.json"

    # DEBUG ONLY — the judge must never read this file (§8.4).
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(persona.system_prompt(), encoding="utf-8")

    target = ElevenLabsTarget(
        api_key=cfg.secrets.elevenlabs_api_key,
        agent_id=cfg.target.agent_id,
        raw_log_path=raw_log,
        text_only=True,            # RUNTIME override only. The live agent is never modified.
        auth=cfg.target.auth,
    )

    referee = Referee(
        persona,
        cfg.referee,
        referee_llm,
        max_conversation_seconds=cfg.run.max_conversation_seconds,
    )

    started_at = utc_now()
    wall_start = time.monotonic()
    state = ConversationState(persona=persona, turns=turns, errors=errors)

    referee_charged = Usage()  # how much of referee.usage the budget has already seen

    async def charge_referee() -> None:
        """Bill the referee's new tokens to the shared budget.

        The referee makes one 105b call per turn and none of it used to be charged, so the
        run-level guard was fed a number that ignored the more expensive model entirely.
        """
        nonlocal referee_charged
        delta = _usage_delta(referee.usage, referee_charged)
        if delta.calls or delta.prompt_tokens or delta.completion_tokens:
            await budget.charge(cfg.referee.model, delta)
            referee_charged = referee.usage

    def record_error(stage, code: str, message: str, *, turn_idx: int | None = None,
                     fatal: bool = False, retryable: bool = False) -> None:
        errors.append(RunError(at=utc_now(), stage=stage, code=code, message=message[:1000],
                               turn_idx=turn_idx, attempt=1, retryable=retryable, fatal=fatal))

    try:
        conversation_id = await target.open(persona.scenario.vars)
        # elapsed_s is measured from the moment open() returned (§5.1).
        state.started_monotonic = time.monotonic()
        wall_start = state.started_monotonic
        emit("open", f"conversation_id={conversation_id}")

        # ── turn 0: the agent's unprompted opening. ALWAYS received before we speak.
        opening = await target.recv_agent_turn(OPENING_TIMEOUT_S)
        turns.append(Turn(idx=0, speaker="agent", text=opening.text,
                          latency_ms=opening.latency_ms, ts=utc_now(),
                          event_id=opening.event_id,
                          meta={"is_opening": True, **_parts_meta(opening)}))
        state.agent_turn_count += 1
        if target.event_id_regressions:
            record_error("target", "event_id_regression",
                         "agent_response event_id did not advance", turn_idx=0)
        emit("agent", opening.text)

        while True:
            state.sync_elapsed()

            # Budget is a RUN-level hard stop: it kills in-flight conversations too.
            if budget.exceeded:
                end_reason = EndReason(
                    code="budget_exceeded", kind="hard",
                    detail=f"run budget ₹{budget.limit_inr} exhausted (spent ₹{budget.spent_inr:.4f})",
                    at_turn=state.last_turn_idx, evidence=None,
                )
                record_error("runner", "budget_exceeded", end_reason.detail, fatal=True)
                break

            reason = await referee.check(state)
            await charge_referee()   # the referee's tokens count against the run budget too
            if reason is not None:
                end_reason = reason
                break

            # ── persona speaks
            reply = await persona.reply(turns)
            errors.extend(persona.drain_errors())
            persona_usage = persona_usage + reply.usage
            await budget.charge(cfg.persona_brain.model, reply.usage)

            # `sent` starts False on purpose. A persona line can be generated and then never
            # reach the agent — the step-9 time cap below, or a socket that dies on the send.
            # Without this flag the judge sees a customer turn the agent never answered and
            # scores it as the agent ignoring the customer. It is flipped to True the instant
            # the user_message frame actually goes out.
            turns.append(Turn(idx=len(turns), speaker="persona", text=reply.text,
                              latency_ms=reply.latency_ms, ts=utc_now(), event_id=None,
                              meta={"attempts": reply.attempts,
                                    "reasoning_chars": reply.reasoning_chars,
                                    "sent": False}))
            emit("persona", reply.text)

            # §6.1 step 9 — re-check the hard ceiling so it can never be overshot.
            #
            # NOTE, and this is the one place the contract argues with itself. §6.1 puts
            # `exchange_count += 1` at step 8, before the send. §5.2 defines exchange_count
            # as "persona utterances SENT (== user_message frames sent)". Those disagree,
            # and §8.2's worked example settles it: turns_over: 12 yields 25 turns /
            # 13 agent / 12 persona / at_turn 24 — a transcript that ends on an AGENT turn.
            # Incrementing before the send gives 24 turns ending on a customer line the
            # agent never heard, which also reads to the judge like the agent ignored it.
            #
            # So: the counter increments after the frame actually goes out (§5.2), and this
            # step-9 check is what it is for either way — a guard against the TIME caps
            # (seconds_over, wall_clock_cap), which a slow persona turn can blow past. Turn
            # ceilings are unchanged since step 4, so they simply cannot fire here.
            state.sync_elapsed()
            reason = referee.check_hard(state)
            if reason is not None:
                end_reason = reason
                break

            # ── agent replies
            await target.send_user_turn(reply.text)
            state.exchange_count += 1
            turns[-1] = replace(turns[-1], meta={**turns[-1].meta, "sent": True})
            before = target.event_id_regressions
            agent_turn = await target.recv_agent_turn(TURN_TIMEOUT_S)
            turns.append(Turn(idx=len(turns), speaker="agent", text=agent_turn.text,
                              latency_ms=agent_turn.latency_ms, ts=utc_now(),
                              event_id=agent_turn.event_id, meta=_parts_meta(agent_turn)))
            state.agent_turn_count += 1
            if target.event_id_regressions > before:
                record_error("target", "event_id_regression",
                             f"agent_response event_id {agent_turn.event_id} did not advance",
                             turn_idx=len(turns) - 1)
            emit("agent", agent_turn.text)

    except TargetTimeout as exc:
        record_error("target", "target_timeout", str(exc), turn_idx=len(turns) - 1, fatal=True)
        end_reason = EndReason(code="target_disconnected", kind="error",
                               detail=f"target timeout: {exc}", at_turn=len(turns) - 1)
    except TargetClosed as exc:
        # WHAT WE ACTUALLY KNOW, corrected. One live run (20260725-174122-733423) ended with
        # the server closing cleanly (1000) — but that run's turn 13 was the persona
        # impersonating Tara and CLOSING THE CALL in her voice, and the server hung up after
        # being told goodbye by its own "customer". So it is NOT established that the agent
        # hangs up after its own farewell, and the spike's §8 is not contradicted by that
        # artifact. The character break is fixed in agent/persona.py; whether a clean call
        # ever sees a server-side close is now an OPEN QUESTION, not a finding.
        #
        # The close_code split below stays, because telling "peer closed cleanly" from
        # "socket dropped" is worth having either way, and it is exercised offline by
        # scripts/smoke_loop_offline.py scenario 6 (a live 1000 close has still never been
        # round-tripped through this path). end_reason.code stays `target_disconnected`:
        # schema.EndCode is a closed Literal and inventing a member is a contract change,
        # not a runner decision. No contract change is proposed on this evidence.
        hung_up = exc.close_code == 1000
        record_error("target", "target_closed",
                     f"{'peer closed cleanly (close 1000, normal closure)' if hung_up else 'socket dropped'}"
                     f" — {exc}",
                     turn_idx=len(turns) - 1, fatal=True)
        if hung_up:
            warnings.append(
                "agent_closed_socket: the server closed the WebSocket cleanly (1000). "
                "docs/INTERFACES.md §3.3 and the spike §8 both state the server never hangs "
                "up, so read the last few turns before trusting either: the one prior "
                "occurrence followed a corrupted customer turn that said goodbye in the "
                "agent's own voice. The transcript up to this point is complete."
            )
        end_reason = EndReason(code="target_disconnected", kind="error",
                               detail=(f"peer closed the socket cleanly (1000) — {exc}"
                                       if hung_up else f"target closed: {exc}"),
                               at_turn=len(turns) - 1)
    except TargetProtocolError as exc:
        record_error("target", "target_closed", str(exc), turn_idx=len(turns) - 1, fatal=True)
        end_reason = EndReason(code="error", kind="error",
                               detail=f"target protocol error: {exc}", at_turn=len(turns) - 1)
    except PersonaError as exc:
        errors.extend(persona.drain_errors())
        record_error("persona_brain", "persona_exhausted_retries", str(exc),
                     turn_idx=len(turns) - 1, fatal=True)
        end_reason = EndReason(code="error", kind="error",
                               detail=f"persona brain failed: {exc}", at_turn=len(turns) - 1)
    except asyncio.CancelledError:
        record_error("runner", "budget_exceeded", "conversation cancelled by the runner",
                     turn_idx=len(turns) - 1, fatal=True)
        end_reason = EndReason(code="error", kind="error", detail="cancelled",
                               at_turn=len(turns) - 1)
    except TargetError as exc:
        record_error("target", "target_closed", str(exc), turn_idx=len(turns) - 1, fatal=True)
        end_reason = EndReason(code="target_disconnected", kind="error",
                               detail=f"{type(exc).__name__}: {exc}", at_turn=len(turns) - 1)
    except Exception as exc:  # noqa: BLE001 — one persona failing must not kill the run
        log.exception("%s: unexpected failure", persona.id)
        record_error("runner", "error", f"{type(exc).__name__}: {exc}",
                     turn_idx=len(turns) - 1, fatal=True)
        end_reason = EndReason(code="error", kind="error",
                               detail=f"{type(exc).__name__}: {exc}", at_turn=len(turns) - 1)
    finally:
        try:
            await target.close(end_reason.code if end_reason else "runner_decided")
        except Exception:  # close() promises not to raise; belt and braces
            pass

    if end_reason is None:  # defensive: the loop only exits via a break that sets this
        end_reason = EndReason(code="error", kind="error",
                               detail="loop exited without an end reason",
                               at_turn=len(turns) - 1)

    # A conversation that died mid-flight still spent the referee's tokens. Charge them.
    try:
        await charge_referee()
    except Exception:  # noqa: BLE001 — accounting must never mask the real end reason
        log.debug("%s: final referee charge failed", persona.id, exc_info=True)

    ended_at = utc_now()
    duration_s = round(time.monotonic() - wall_start, 2)

    if target.unknown_events:
        warnings.append(f"unknown_events: {target.unknown_events}")
    merged = getattr(target, "agent_response_parts_merged", 0)
    if merged:
        warnings.append(
            f"agent_response_coalesced: {merged} extra agent_response frame(s) were merged "
            "into the turn they belong to (the agent split a turn, e.g. a filler line before "
            "a tool call). Without the merge the transcript would be one turn out of step "
            "from that point on. Affected turns carry meta.agent_response_parts."
        )

    artifact = _build_artifact(
        persona=persona, cfg=cfg, run_id=run_id, target=target, referee=referee,
        agent_info=agent_info, turns=turns, end_reason=end_reason,
        persona_usage=persona_usage, budget=budget, errors=errors, warnings=warnings,
        started_at=started_at, ended_at=ended_at, duration_s=duration_s,
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    return ConversationResult(
        persona_id=persona.id,
        ok=end_reason.kind != "error",
        end_reason=end_reason,
        artifact_path=artifact_path,
        conversation_id=conversation_id,
        turn_count=artifact["turn_count"],
        duration_s=duration_s,
        usage={"persona_brain": persona_usage, "referee": referee.usage},
        cost_inr=artifact["cost"]["total_inr"],
        errors=errors,
        warnings=warnings,
    )


# ======================================================================================
# The artifact (§8.2) — every key always present; the judge never writes .get(default)
# ======================================================================================


def _build_artifact(*, persona: Persona, cfg: Config, run_id: str, target: ElevenLabsTarget,
                    referee: Referee, agent_info: dict[str, Any], turns: list[Turn],
                    end_reason: EndReason, persona_usage: Usage, budget: BudgetTracker,
                    errors: list[RunError], warnings: list[str], started_at: str,
                    ended_at: str, duration_s: float) -> dict[str, Any]:

    persona_cost = budget.cost_of(cfg.persona_brain.model, persona_usage)
    referee_cost = budget.cost_of(cfg.referee.model, referee.usage)
    if persona_cost is None:
        warnings.append(f"cost_rate_missing: no pricing for {cfg.persona_brain.model}")
    if referee_cost is None:
        warnings.append(f"cost_rate_missing: no pricing for {cfg.referee.model}")
    total_cost = None if (persona_cost is None or referee_cost is None) else round(
        persona_cost + referee_cost, 6
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "level": 0,

        "persona_id": persona.id,
        "persona_name": persona.name,
        "persona_stresses": persona.stresses,
        "persona_is_control": persona.control,
        "persona_file_sha256": persona.file_sha256,

        "target": {
            "adapter": cfg.target.adapter,
            "agent_id": cfg.target.agent_id,
            "agent_name": agent_info.get("name"),
            "agent_llm": agent_info.get("llm"),
            "conversation_id": target.conversation_id,
            "mode": "text",
            "text_only_override_sent": target.text_only_override_sent,
            "auth_method": target.auth_method,
            "audio_frames_discarded": target.audio_frames_discarded,
            "unknown_events": dict(target.unknown_events),
        },

        "models": {
            "persona_brain": {
                "provider": cfg.persona_brain.provider,
                "model": cfg.persona_brain.model,
                "temperature": cfg.persona_brain.temperature,
                "max_tokens": cfg.persona_brain.max_tokens,
            },
            "referee": {
                "provider": cfg.referee.provider,
                "model": cfg.referee.model,
                "temperature": cfg.referee.temperature,
                "max_tokens": cfg.referee.max_tokens,
            },
        },

        "scenario_vars": dict(persona.scenario.vars),
        "ground_truth": dict(persona.scenario.ground_truth),

        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": duration_s,

        "end_reason": {
            "code": end_reason.code,
            "kind": end_reason.kind,
            "detail": end_reason.detail,
            "at_turn": end_reason.at_turn,
            "evidence": end_reason.evidence,
        },

        "turn_count": {
            "total": len(turns),
            "agent": sum(1 for t in turns if t.speaker == "agent"),
            "persona": sum(1 for t in turns if t.speaker == "persona"),
        },

        "turns": [
            {
                "idx": t.idx,
                "speaker": t.speaker,
                "text": t.text,
                "latency_ms": int(t.latency_ms),
                "ts": t.ts,
                "event_id": t.event_id,
                "meta": dict(t.meta),
            }
            for t in turns
        ],

        "usage": {
            "persona_brain": _usage_dict(persona_usage),
            "referee": _usage_dict(referee.usage),
            "elevenlabs": {
                "conversations": 1 if target.conversation_id else 0,
                "agent_turns": target.agent_turns,
                "agent_characters": target.agent_characters,
                "user_characters": target.user_characters,
            },
        },

        "cost": {
            "currency": "INR",
            "persona_brain_inr": None if persona_cost is None else round(persona_cost, 6),
            "referee_inr": None if referee_cost is None else round(referee_cost, 6),
            "elevenlabs_inr": 0.0,
            "total_inr": total_cost,
            "rates_source": f"{cfg.config_path.name} pricing:",
        },

        "errors": [
            {
                "at": e.at,
                "stage": e.stage,
                "code": e.code,
                "message": e.message,
                "turn_idx": e.turn_idx,
                "attempt": e.attempt,
                "retryable": e.retryable,
                "fatal": e.fatal,
            }
            for e in errors
        ],

        "warnings": list(dict.fromkeys(warnings)),

        "artifacts": {
            "raw_events": f"raw/{persona.id}.jsonl",
            "system_prompt": f"prompts/{persona.id}.system.txt",
        },
    }


def _usage_dict(u: Usage) -> dict[str, int]:
    return {
        "calls": u.calls,
        "retries": u.retries,
        "prompt_tokens": u.prompt_tokens,
        "completion_tokens": u.completion_tokens,
        "reasoning_chars": u.reasoning_chars,
        "total_tokens": u.total_tokens,
    }


__all__ = ["run_conversation", "BudgetTracker", "ConversationResult", "utc_now"]
