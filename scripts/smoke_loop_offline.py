"""Offline smoke test of runner/loop.py — no network, no credentials, no quota.

Fakes the ElevenLabs socket and the Sarvam brain, then asserts the artifact contract:
the agent speaks first, the transcript alternates, hard_stop holds, and a mid-conversation
failure still writes a complete conversations/<persona_id>.json.

Scenarios 6-11 are the regression cover for the defects fixed on 25 July 2026:
  6  a clean 1000 close is reported as a peer close, with the warning and detail text
     (that path had never been executed at all)
  7  a turn split across two agent_response frames is merged, not skewed
  8  the referee's tokens are charged to the shared budget
  9  a Sarvam timeout is retried instead of killing the conversation
 10  a persona line in the agent's voice is never sent and never recorded
 11  a soft end reason must be provable from the transcript, by the right speaker

    uv run --python 3.12 python scripts/smoke_loop_offline.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import persona as persona_mod  # noqa: E402
from agent import referee as referee_mod  # noqa: E402
from agent.sarvam import LLMError, LLMResult  # noqa: E402
from config import load_config  # noqa: E402
from runner import loop as loop_mod  # noqa: E402
from runner.loop import BudgetTracker, run_conversation  # noqa: E402
from schema import Turn, Usage  # noqa: E402
from targets.base import AgentTurn, TargetClosed  # noqa: E402
import targets.elevenlabs as el_mod  # noqa: E402


class FakeTarget:
    def __init__(self, *, api_key, agent_id, raw_log_path=None, text_only=True, auth="header",
                 die_after=None, close_code=None):
        self.agent_id = agent_id
        self.auth_method = auth
        self.text_only = text_only
        self.text_only_override_sent = False
        self.conversation_id = None
        self.audio_frames_discarded = 29
        self.unknown_events: dict[str, int] = {}
        self.event_id_regressions = 0
        self.agent_turns = 0
        self.agent_characters = 0
        self.user_characters = 0
        self.agent_response_parts_merged = 0
        self._n = 0
        self._die_after = die_after
        self._close_code = close_code
        self.sent: list[str] = []

    async def open(self, scenario_vars):
        assert len(scenario_vars) == 11, "all 11 dynamic_variables must be sent"
        assert all(isinstance(v, str) for v in scenario_vars.values()), "all values are strings"
        self.conversation_id = "conv_fake_0001"
        self.text_only_override_sent = self.text_only
        return self.conversation_id

    async def recv_agent_turn(self, timeout_s=90.0):
        if self._die_after is not None and self._n >= self._die_after:
            raise TargetClosed(
                "fake socket died mid-conversation: received 1000 (OK); then sent 1000 (OK)"
                if self._close_code == 1000 else "fake socket died mid-conversation",
                close_code=self._close_code,
            )
        self._n += 1
        text = ("Hi Kunal, this is Tara from JioHotstar." if self._n == 1
                else f"I can do 10% off, nothing more. (agent turn {self._n})")
        self.agent_turns += 1
        self.agent_characters += len(text)
        return AgentTurn(text=text, event_id=self._n, latency_ms=800, raw={"type": "agent_response"})

    async def send_user_turn(self, text):
        assert self._n >= 1, "spoke before the agent's opening"
        self.sent.append(text)
        self.user_characters += len(text)

    async def close(self, reason="runner_decided"):
        self.closed = reason


#: A line the persona must never be allowed to say: it is Tara's voice, not Kunal's.
IN_AGENT_VOICE = ("That's perfect, Kunal. I've noted it. The 10% offer will be there and "
                  "valid until 8 August. Take your time to check with other apps.")


class FakeBrain:
    def __init__(self, cfg, fail_at=None, verdict=None, timeout_at=None, break_char=None):
        self.cfg = cfg
        self.calls = 0
        self.fail_at = fail_at
        self.verdict = verdict
        self.timeout_at = timeout_at      # attempt numbers that raise a Sarvam timeout
        self.break_char = break_char      # attempt numbers that come back in the agent's voice
        self.seen: list[list[dict]] = []

    async def complete(self, messages, *, response_format=None, max_tokens=None, temperature=None):
        self.calls += 1
        self.seen.append(messages)
        if response_format is not None:  # referee
            body = self.verdict or (
                '{"goal_reached": false, "agent_offers_human_handoff": false, '
                '"persona_walked_away": false, "evidence": ""}'
            )
            return LLMResult(text=body, finish_reason="stop", reasoning_content="r" * 100,
                             usage=Usage(calls=1, prompt_tokens=300, completion_tokens=900,
                                         reasoning_chars=100, total_tokens=1200),
                             latency_ms=4000, raw={})
        if self.timeout_at and self.calls in self.timeout_at:
            raise LLMError("persona: request timed out: ReadTimeout", transport="timeout")
        if self.break_char and self.calls in self.break_char:
            return LLMResult(text=IN_AGENT_VOICE, finish_reason="stop",
                             reasoning_content="r" * 6000,
                             usage=Usage(calls=1, prompt_tokens=400, completion_tokens=1665,
                                         reasoning_chars=6000, total_tokens=2065),
                             latency_ms=5800, raw={})
        if self.fail_at is not None and self.calls >= self.fail_at:
            return LLMResult(text=None, finish_reason="length", reasoning_content="r" * 6000,
                             usage=Usage(calls=1, prompt_tokens=400, completion_tokens=2000,
                                         reasoning_chars=6000, total_tokens=2400),
                             latency_ms=5800, raw={})
        return LLMResult(text=f"Arre yaar, 10% se kya hoga? (customer turn {self.calls})",
                         finish_reason="stop", reasoning_content="r" * 6000,
                         usage=Usage(calls=1, prompt_tokens=400, completion_tokens=1665,
                                     reasoning_chars=6000, total_tokens=2065),
                         latency_ms=5800, raw={})

    async def aclose(self):
        pass


class FakeWS:
    """Minimal duck-type of a websockets connection, with frames gated on our sends.

    `script` is [(after_n_user_messages, frame, delay_s), ...]. A frame is readable once
    that many user_message frames have gone out AND `delay_s` has passed since the last
    one did — real turn boundaries and real inter-frame gaps, not just a drained queue.
    """

    def __init__(self, script):
        self.script = [(gate, frame, delay) for gate, frame, *rest in script
                       for delay in ((rest[0] if rest else 0.0),)]
        self.user_sends = 0
        self.gate_at = asyncio.get_event_loop().time()

    async def recv(self):
        while True:
            now = asyncio.get_event_loop().time()
            for i, (gate, frame, delay) in enumerate(self.script):
                if gate <= self.user_sends and now >= self.gate_at + delay:
                    self.script.pop(i)
                    return json.dumps(frame)
            await asyncio.sleep(0.01)   # nothing to say yet; the caller's timeout decides

    async def send(self, raw):
        if json.loads(raw).get("type") == "user_message":
            self.user_sends += 1
            self.gate_at = asyncio.get_event_loop().time()

    async def close(self):
        pass


def _resp(text: str, event_id: int) -> dict:
    return {"type": "agent_response",
            "agent_response_event": {"agent_response": text, "event_id": event_id}}


async def split_turn_transcript():
    """THE REPRO for the one-turn skew: filler -> tool_response -> real answer.

    Drives the REAL ElevenLabsTarget, so it is the merge logic under test, not a fake.
    """
    el_mod.AGENT_TURN_SETTLE_S = 0.3   # keep the smoke test fast; restored by the caller
    target = el_mod.ElevenLabsTarget(api_key="dummy", agent_id="agent_dummy")
    # The real answer lands 0.9 s after the filler — well past the 0.3 s quiet window. It is
    # only caught because the tool frame and the streamed parts prove the agent is still
    # talking, which is what buys the extension. Drop that signal and this test skews.
    target._ws = FakeWS([
        (0, _resp("Hi Kunal, this is Tara.", 1)),
        (1, _resp("Let me just check that for you.", 2)),
        (1, {"type": "agent_tool_response", "agent_tool_response_event": {"tool_name": "x"}}, 0.15),
        (1, {"type": "agent_chat_response_part",
             "agent_chat_response_part_event": {"text": "ANSWER"}}, 0.6),
        (1, _resp("ANSWER-TO-USER-1", 3), 0.9),
        (2, _resp("ANSWER-TO-USER-2", 4)),
    ])
    target._opened = True
    target.conversation_id = "conv_fake_split"

    texts = [(await target.recv_agent_turn(5.0)).text]
    await target.send_user_turn("USER-1")
    texts.append((await target.recv_agent_turn(5.0)).text)
    await target.send_user_turn("USER-2")
    texts.append((await target.recv_agent_turn(5.0)).text)
    return texts, target


async def evidence_audit_checks(cfg):
    """A soft verdict must be provable from the transcript, by the right speaker."""
    p = persona_mod.load(cfg.personas_dir / "price-haggler.yaml")
    turns = [
        Turn(idx=0, speaker="agent", latency_ms=1, ts="2026-07-25T00:00:00.000Z",
             text="Thanks for your time, Kunal. Have a great day!"),
        Turn(idx=1, speaker="persona", latency_ms=1, ts="2026-07-25T00:00:01.000Z",
             text="Main dusre app mein check karta hoon."),
    ]

    async def verdict(body):
        ref = referee_mod.Referee(p, cfg.referee, FakeBrain(cfg.persona_brain, verdict=body))
        state = referee_mod.ConversationState(persona=p, turns=list(turns))
        state.exchange_count = 1
        return await ref._check_soft(state), state

    def walked(evidence):
        return json.dumps({"goal_reached": False, "agent_offers_human_handoff": False,
                           "persona_walked_away": True, "evidence": evidence})

    # THE REGRESSION: the farewell is the AGENT's line. It cannot prove the CUSTOMER left.
    reason, state = await verdict(walked('turn 0 — "Have a great day!"'))
    assert reason is None, reason
    assert any(e.code == "referee_bad_evidence" for e in state.errors), state.errors

    # a quote nobody said
    reason, state = await verdict(walked('turn 1 — "Chalo, deal hai."'))
    assert reason is None, reason
    assert any(e.code == "referee_bad_evidence" for e in state.errors)

    # a turn index that does not exist
    reason, state = await verdict(walked('turn 7 — "Main dusre app mein check karta hoon."'))
    assert reason is None, reason

    # the real thing still passes, and comes back normalised
    reason, _ = await verdict(walked('turn 1 — "dusre app mein check karta hoon"'))
    assert reason is not None and reason.code == "persona_walked_away", reason
    assert reason.evidence == 'turn 1 — "dusre app mein check karta hoon"', reason.evidence

    # no turn number is tolerated — it is looked up, among the right speaker's turns only
    reason, _ = await verdict(walked('"Main dusre app mein check karta hoon."'))
    assert reason is not None and reason.evidence.startswith("turn 1 — "), reason


async def _fake_agent_info(http, api_key, agent_id):
    return {"name": "jiohotstar-tara-winback-recovery", "llm": "qwen35-397b-a17b",
            "text_only_overridable": True}


def fake_sent(art):
    """The persona lines that actually went out as user_message frames."""
    return [t for t in art["turns"] if t["speaker"] == "persona" and t["meta"]["sent"]]


async def scenario(tmp: Path, cfg, *, die_after=None, fail_at=None, tag="a", close_code=None,
                   timeout_at=None, break_char=None, budget=None, referee_brain=None):
    p = persona_mod.load(cfg.personas_dir / "price-haggler.yaml")
    brain = FakeBrain(cfg.persona_brain, fail_at=fail_at, timeout_at=timeout_at,
                      break_char=break_char)
    object.__setattr__(p, "brain", brain)

    loop_mod.ElevenLabsTarget = lambda **kw: FakeTarget(  # type: ignore
        **kw, die_after=die_after, close_code=close_code)
    run_dir = tmp / tag
    (run_dir / "conversations").mkdir(parents=True, exist_ok=True)
    result = await run_conversation(
        p, cfg=cfg, run_id=f"smoke-{tag}", run_dir=run_dir,
        budget=budget if budget is not None else BudgetTracker(cfg),
        referee_llm=referee_brain or FakeBrain(cfg.persona_brain),
        agent_info={"name": "tara", "llm": "qwen"},
    )
    art = json.loads(result.artifact_path.read_text())
    return result, art, brain


async def main() -> int:
    from dataclasses import replace as dc_replace

    cfg = load_config(ROOT / "config.yaml", ROOT / ".env")
    # The shipped pricing: block is 0.0, i.e. UNPRICED — cost is null and the budget guard
    # is inert by design until real rates land. Give the smoke run rates so the money paths
    # (cost.*, BudgetTracker) are exercised for real rather than multiplied by zero.
    cfg = dc_replace(cfg, pricing={"sarvam-30b": {"input": 10.0, "output": 30.0},
                                   "sarvam-105b": {"input": 40.0, "output": 120.0}})
    tmp = Path(tempfile.mkdtemp(prefix="spar-smoke-"))
    real_target = loop_mod.ElevenLabsTarget
    real_settle = el_mod.AGENT_TURN_SETTLE_S

    try:
        # 1. a clean conversation runs to the persona's turns_over: 12
        r, art, brain = await scenario(tmp, cfg, tag="clean")
        assert art["turns"][0]["speaker"] == "agent", "the agent must speak first"
        assert art["turns"][0]["meta"]["is_opening"] is True
        speakers = [t["speaker"] for t in art["turns"]]
        assert speakers == ["agent", "persona"] * 12 + ["agent"], speakers
        assert art["end_reason"]["code"] == "turns_over", art["end_reason"]
        assert art["end_reason"]["kind"] == "hard"
        assert art["end_reason"]["evidence"] is None, "hard endings carry no evidence"
        assert art["turn_count"] == {"total": 25, "agent": 13, "persona": 12}
        assert art["end_reason"]["at_turn"] == 24, art["end_reason"]
        assert art["turns"][1]["event_id"] is None, "persona turns never carry an event_id"
        # every persona line in a clean run actually reached the agent
        assert all(t["meta"]["sent"] for t in art["turns"] if t["speaker"] == "persona")
        assert len(fake_sent(art)) == 12
        assert art["usage"]["persona_brain"]["calls"] == 12
        # the soft check is skipped on the first pass (exchange_count == 0): 11, not 12
        assert art["usage"]["referee"]["calls"] == 11, art["usage"]["referee"]
        assert art["cost"]["total_inr"] is not None
        for key in ("schema_version", "run_id", "level", "persona_id", "persona_name",
                    "persona_stresses", "persona_is_control", "persona_file_sha256", "target",
                    "models", "scenario_vars", "ground_truth", "started_at", "ended_at",
                    "duration_s", "end_reason", "turn_count", "turns", "usage", "cost",
                    "errors", "warnings", "artifacts"):
            assert key in art, f"artifact is missing required key {key}"
        assert len(art["scenario_vars"]) == 11
        # reasoning_content never re-enters history
        for msgs in brain.seen:
            for m in msgs:
                assert "rrrrrrrrrr" not in m["content"], "reasoning_content leaked into history"
        # the system prompt never carries the exit rules
        prompt = (tmp / "clean" / "prompts" / "price-haggler.system.txt").read_text()
        for tok in ("end_when", "hard_stop", "turns_over", "seconds_over"):
            assert tok not in prompt.lower(), tok
        print("  [1] clean conversation           OK  "
              f"({art['turn_count']['total']} turns, {art['end_reason']['code']})")

        # 2. the socket dying mid-conversation still writes a complete artifact
        r, art, _ = await scenario(tmp, cfg, die_after=3, tag="dropped")
        assert art["end_reason"]["code"] == "target_disconnected", art["end_reason"]
        assert art["end_reason"]["kind"] == "error"
        assert art["turn_count"] == {"total": 6, "agent": 3, "persona": 3}, art["turn_count"]
        assert any(e["fatal"] for e in art["errors"])
        assert r.ok is False
        print("  [2] dropped socket               OK  "
              f"(partial transcript of {art['turn_count']['total']} turns still written)")

        # 3. the brain returning content=None forever ends the conversation, not the run
        r, art, _ = await scenario(tmp, cfg, fail_at=3, tag="brainfail")
        assert art["end_reason"]["code"] == "error", art["end_reason"]
        assert any(e["code"] == "empty_content_length" for e in art["errors"])
        assert any(e["code"] == "persona_exhausted_retries" for e in art["errors"])
        print("  [3] persona brain exhausted      OK  "
              f"({art['turn_count']['total']} turns kept, {len(art['errors'])} errors recorded)")

        # 4. the budget cap aborts mid-conversation
        cfg_broke = cfg
        budget = BudgetTracker(cfg_broke)
        budget.spent_inr = 999.0
        p = persona_mod.load(cfg.personas_dir / "price-haggler.yaml")
        object.__setattr__(p, "brain", FakeBrain(cfg.persona_brain))
        loop_mod.ElevenLabsTarget = lambda **kw: FakeTarget(**kw)  # type: ignore
        res = await run_conversation(p, cfg=cfg_broke, run_id="smoke-budget",
                                     run_dir=tmp / "budget", budget=budget,
                                     referee_llm=None, agent_info={})
        art = json.loads(res.artifact_path.read_text())
        assert art["end_reason"]["code"] == "budget_exceeded", art["end_reason"]
        assert art["turn_count"]["persona"] == 0
        print("  [4] budget cap aborts mid-run    OK  "
              f"({art['end_reason']['detail']})")

        # 5. THE HEADLINE RULE, through the real execute_run(): four personas in parallel,
        #    one of them with a socket that dies instantly. Three good conversations must
        #    still land, and run.json must record the failure rather than lose the run.
        from runner import run as run_mod

        def target_factory(**kw):
            # angry-churner's socket dies on its very first recv
            return FakeTarget(**kw, die_after=0 if "angry-churner" in str(kw.get("raw_log_path")) else None)

        loop_mod.ElevenLabsTarget = target_factory  # type: ignore
        run_mod.SarvamClient = lambda *a, **kw: FakeBrain(cfg.persona_brain)  # type: ignore
        run_mod.fetch_agent_info = _fake_agent_info  # type: ignore

        cfg_run = dc_replace(cfg, run=dc_replace(cfg.run, out_dir=tmp / "runs", max_parallel=4))
        manifest = await run_mod.execute_run(cfg_run, only=None, run_id="smoke-run")

        assert manifest["totals"]["conversations"] == 4, manifest["totals"]
        assert manifest["totals"]["ok"] == 3, manifest["totals"]
        assert manifest["totals"]["failed"] == 1, manifest["totals"]
        failed = [p for p in manifest["personas"] if not p["ok"]]
        assert len(failed) == 1 and failed[0]["persona_id"] == "angry-churner", failed
        assert failed[0]["end_reason"] == "target_disconnected"
        run_dir = tmp / "runs" / "smoke-run"
        for p in manifest["personas"]:
            assert (run_dir / "conversations" / f"{p['persona_id']}.json").is_file()
        assert (run_dir / "run.json").is_file()
        print(f"  [5] one persona dies, run lives  OK  "
              f"({manifest['totals']['ok']} ok / {manifest['totals']['failed']} failed, "
              f"all {manifest['totals']['conversations']} artifacts written)")

        # 6. a CLEAN peer close (1000) is reported as a peer close, not as a bare crash.
        #    This path had never executed — the one live artifact that reached it predates
        #    the code and carries none of its text.
        r, art, _ = await scenario(tmp, cfg, die_after=3, close_code=1000, tag="closed1000")
        assert art["end_reason"]["code"] == "target_disconnected", art["end_reason"]
        assert "peer closed the socket cleanly (1000)" in art["end_reason"]["detail"]
        assert any(w.startswith("agent_closed_socket:") for w in art["warnings"]), art["warnings"]
        closed = [e for e in art["errors"] if e["code"] == "target_closed"]
        assert closed and "peer closed cleanly (close 1000" in closed[0]["message"], closed
        assert art["turn_count"]["total"] == 6, art["turn_count"]
        print("  [6] clean 1000 close             OK  "
              f"(warning + detail round-tripped, {art['turn_count']['total']} turns kept)")

        # 7. a turn split across two agent_response frames is MERGED, not skewed. Driven
        #    through the real ElevenLabsTarget against a fake socket — this is the exact
        #    filler -> tool_response -> answer sequence the live agent can emit.
        merged_turns, target = await split_turn_transcript()
        assert merged_turns[0] == "Hi Kunal, this is Tara.", merged_turns
        assert "ANSWER-TO-USER-1" in merged_turns[1], merged_turns
        assert "Let me just check that for you." in merged_turns[1], merged_turns
        assert "ANSWER-TO-USER-2" in merged_turns[2], merged_turns
        assert target.agent_response_parts_merged == 1, target.agent_response_parts_merged
        print("  [7] split agent turn merged      OK  "
              "(answer to USER-1 stays the answer to USER-1)")

        # 8. the referee's tokens are charged to the shared budget, not just reported.
        budget = BudgetTracker(cfg)
        r, art, _ = await scenario(tmp, cfg, tag="charged", budget=budget)
        assert art["usage"]["referee"]["calls"] == 11, art["usage"]["referee"]
        assert art["cost"]["referee_inr"] > 0.0, art["cost"]
        assert abs(budget.spent_inr - art["cost"]["total_inr"]) < 1e-6, (
            budget.spent_inr, art["cost"])
        print("  [8] referee charged to budget    OK  "
              f"(Rs {budget.spent_inr:.4f} spent == Rs {art['cost']['total_inr']:.4f} reported)")

        # 9. a Sarvam timeout is RETRYABLE. One blip must not destroy a 12-turn conversation.
        r, art, _ = await scenario(tmp, cfg, timeout_at={1, 5}, tag="timeout")
        assert art["end_reason"]["code"] == "turns_over", art["end_reason"]
        assert art["turn_count"]["persona"] == 12, art["turn_count"]
        timeouts = [e for e in art["errors"] if e["code"] == "llm_timeout"]
        assert len(timeouts) == 2 and all(e["retryable"] and not e["fatal"] for e in timeouts)
        print("  [9] sarvam timeout retried       OK  "
              f"({len(timeouts)} timeouts survived, {art['turn_count']['persona']} customer turns)")

        # 10. a persona line in the AGENT's voice never reaches the wire or the transcript.
        r, art, brain = await scenario(tmp, cfg, break_char={1, 3}, tag="breakchar")
        assert art["end_reason"]["code"] == "turns_over", art["end_reason"]
        for turn in art["turns"]:
            if turn["speaker"] == "persona":
                assert "I've noted it" not in turn["text"], turn
                assert not persona_mod._character_break(turn["text"], "Kunal"), turn
        broke = [e for e in art["errors"] if e["code"] == "persona_broke_character"]
        assert len(broke) == 2 and all(e["retryable"] and not e["fatal"] for e in broke), broke
        # ...and if it breaks character every single time, nothing is sent at all.
        r2, art2, _ = await scenario(tmp, cfg, break_char=set(range(1, 99)), tag="breakall")
        assert art2["end_reason"]["code"] == "error", art2["end_reason"]
        assert art2["turn_count"]["persona"] == 0, art2["turn_count"]
        assert any(e["code"] == "persona_broke_character" and e["fatal"] for e in art2["errors"])
        print("  [10] character break blocked     OK  "
              f"({len(broke)} rejected + retried; a persistent break sends nothing)")

        # 11. the referee's soft verdict is audited against the transcript before it can end
        #     anything: a quote the cited speaker never said is discarded.
        await evidence_audit_checks(cfg)
        print("  [11] soft evidence audited       OK  "
              "(wrong speaker / missing quote / bad turn index all rejected)")
    finally:
        loop_mod.ElevenLabsTarget = real_target
        el_mod.AGENT_TURN_SETTLE_S = real_settle

    print("\nsmoke_loop_offline: ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
