#!/usr/bin/env python3
"""scripts/regress_rubric.py — FIX_SPEC D5a / D4-prompt regression for judge/rubric.py.

Standalone, offline, no arguments, no network, no Sarvam, no LLM. Exits non-zero on the
first failed assertion. Run:

    PYTHONPATH=. uv run --python 3.12 python scripts/regress_rubric.py

What it pins:
  1. `Dimension.prompt_addendum` exists, defaults to "", and old-style constructor calls
     (the ones without it) still work — so judge.py can land before or after this file.
  2. `goal_outcome`'s addendum carries the four anchors (1.0 / 0.7 / 0.4 / 0.0), the
     "a competent call is 0.6-0.8, not 1.0" instruction, and still says judge process rather
     than conversion — while NO LONGER containing the sentence that collapsed the dimension
     to 1.0 on all four conversations.
  3. `ABSENCE_EVIDENCE_PROMPT` and `GROUND_TRUTH_BREACH_PROMPT` exist, are non-empty, and
     say the specific things D4.1 / D5b require them to say (turn: -1, ALLOWLIST, verbatim
     entry, the four structured entry_kinds, the >= 3 terms rule).
  4. The public surface of the module is unchanged: DIMENSIONS/BY_KEY/band_for/
     weighted_score keep their names, types and behaviour, and the seven keys are the same
     seven keys as before.
  5. INTERFACES §8.4: no prompt text in this module leaks a forbidden persona field.

It deliberately does NOT assert anything inside judge/judge.py (another agent owns it); the
wiring check at the end is INFORMATIONAL and never fails the suite.
"""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from judge import rubric as R  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}")
        FAILURES.append(label)


def section(name: str) -> None:
    print(f"\n{name}")


# ── 1. Dimension gains prompt_addendum, backward compatibly ──────────────────────────────

section("1. Dimension.prompt_addendum (FIX_SPEC §2.2)")

fnames = [f.name for f in fields(R.Dimension)]
check("prompt_addendum" in fnames, "Dimension has a prompt_addendum field")

# The exact backward-compatibility construction named in the D5 acceptance list.
d = R.Dimension("x", "X", "q?", "zero", "one", evidence_from="agent")
check(d.prompt_addendum == "", 'Dimension(...) without prompt_addendum defaults to ""')
check(getattr(d, "prompt_addendum", None) == "",
      'getattr(dim, "prompt_addendum", "") is safe on a bare dimension')

try:
    d.prompt_addendum = "mutated"          # type: ignore[misc]
    frozen = False
except Exception:
    frozen = True
check(frozen, "Dimension is still a frozen dataclass")

d2 = R.Dimension("y", "Y", "q?", "zero", "one", "any", True, "addendum text")
check(d2.prompt_addendum == "addendum text" and d2.require_agent_quote is True,
      "positional construction still works with the new trailing field")

check(all(isinstance(getattr(x, "prompt_addendum", ""), str) for x in R.DIMENSIONS),
      "every dimension's prompt_addendum is a str")


# ── 2. goal_outcome must discriminate again (D5a) ────────────────────────────────────────

section("2. goal_outcome addendum — anchored bands (FIX_SPEC D5a)")

go = R.BY_KEY["goal_outcome"]
add = go.prompt_addendum

check(bool(add.strip()), "goal_outcome.prompt_addendum is non-empty")
for anchor in ("1.0", "0.7", "0.4", "0.0"):
    check(anchor in add, f"goal_outcome addendum states the {anchor} anchor")
check("0.6" in add, 'goal_outcome addendum states the not-1.0 default band ("0.6")')

lower = add.lower()
check("process" in lower and "convert" in lower and "not" in lower,
      "goal_outcome addendum still says judge PROCESS, not conversion")
check("must not be scored as a failure to convert" in lower,
      "goal_outcome addendum still exempts a correct refusal from being a conversion failure")
check("reserve 1.0" in lower,
      "goal_outcome addendum reserves 1.0 for quotable excellence")

DEAD = "Score 1.0 when the agent did everything correctly available to it"
check(DEAD.lower() not in lower,
      "goal_outcome addendum no longer contains the unconditional 1.0 sentence")
check("did everything correctly available to it" not in lower,
      "…nor any reworded survival of it")

# Identity of the dimension is unchanged (D5a: key, weight source, bands do not move).
check(go.key == "goal_outcome" and go.evidence_from == "any" and go.require_agent_quote,
      "goal_outcome identity unchanged: key / evidence_from=any / require_agent_quote")

# The four anchors must be distinguishable to a reader, i.e. the addendum has to describe
# what separates them — otherwise it re-collapses to 1.0 exactly like the old wording.
for word in ("excellent", "adequate", "passive"):
    check(word in lower, f"goal_outcome addendum names the '{word}' band")


# ── 3. The two module constants (D4.1 prompt, D5b prompt) ────────────────────────────────

section("3. GROUND_TRUTH_BREACH_PROMPT (FIX_SPEC D4.1)")

gt = getattr(R, "GROUND_TRUTH_BREACH_PROMPT", "")
check(isinstance(gt, str) and bool(gt.strip()), "GROUND_TRUTH_BREACH_PROMPT exists, non-empty")
gtl = gt.lower()
check("allowlist" in gtl, "…calls claims_agent_may_make an ALLOWLIST")
check("claims_agent_may_make" in gt, "…names claims_agent_may_make explicitly")
check("claims_agent_must_not_make" in gt, "…names claims_agent_must_not_make explicitly")
check("never be a breach" in gtl or "never a breach" in gtl,
      "…states that an allowlisted claim can never be a breach")
check("verbatim" in gtl, "…demands the must_not_make entry be copied VERBATIM")
for kind in ("must_not_make", "discount_ceiling_pct", "valid_prices_inr", "valid_dates",
             "valid_plan_names"):
    check(kind in gt, f"…names entry_kind {kind}")
check("breaches" in gtl, "…names the `breaches` array")
check("invalid" in gtl and "fail" in gtl,
      "…states a fail with no named entry is INVALID")
check("empty" in gtl and "pass" in gtl, "…requires breaches to be empty on a pass")
# The numeric-consistency guard (D4.2) is code-side, but the judge must be told about it or
# it will keep re-admitting a permitted number through a free-text entry.
check("bound" in gtl, "…warns that an entry's own numeric bound must actually be broken")
check("script" in gtl,
      "…tells the judge to use a structured entry_kind for non-Latin-script values")

section("4. ABSENCE_EVIDENCE_PROMPT (FIX_SPEC D5b)")

ab = getattr(R, "ABSENCE_EVIDENCE_PROMPT", "")
check(isinstance(ab, str) and bool(ab.strip()), "ABSENCE_EVIDENCE_PROMPT exists, non-empty")
abl = ab.lower()
check("turn: -1" in abl, 'ABSENCE_EVIDENCE_PROMPT states turn: -1')
check('"absence"' in abl and '"quote"' in abl, "…names both evidence kinds")
check("terms" in abl, "…names the `terms` probe array")
check("3" in ab and "12" in ab, "…states the 3-12 term range")
check("escalation_safety" in ab,
      "…states escalation_safety must always be answerable")
check("single hit" in abl or "one hit" in abl,
      "…warns that one contradicting hit kills the claim")
# Canonical term sets, so a judge cannot pass the audit with deliberately useless probes.
for token in ("transfer", "manager", "insaan", "एजेंट", "sorry", "maaf"):
    check(token in ab, f"…supplies canonical probe term {token!r}")
check("devanagari" in abl, "…requires probes in every language the call used")


# ── 5. Public surface unchanged ──────────────────────────────────────────────────────────

section("5. Public surface (FIX_SPEC §2.2: nothing else changes name or type)")

EXPECTED_KEYS = ("goal_outcome", "hallucination", "instruction_adherence", "language_handling",
                 "objection_handling", "escalation_safety", "conversation_flow")
keys = tuple(d.key for d in R.DIMENSIONS)
check(keys == EXPECTED_KEYS, f"DIMENSIONS keys unchanged and ordered: {keys}")
check(isinstance(R.DIMENSIONS, tuple) and len(R.DIMENSIONS) == 7, "DIMENSIONS is a 7-tuple")
check(R.BY_KEY == {d.key: d for d in R.DIMENSIONS}, "BY_KEY is consistent with DIMENSIONS")
check(all(d.evidence_from in ("agent", "persona", "any") for d in R.DIMENSIONS),
      "every evidence_from is a legal value")
check(all(d.require_agent_quote is False for d in R.DIMENSIONS if d.evidence_from == "agent"),
      "require_agent_quote is only used by relational dimensions")
check(R.BY_KEY["hallucination"].evidence_from == "agent",
      "hallucination still takes AGENT-only evidence")
check(R.BY_KEY["language_handling"].evidence_from == "any"
      and R.BY_KEY["language_handling"].require_agent_quote,
      "language_handling still relational + require_agent_quote")

check(R.band_for(100.0) == "production-ready", "band_for(100) == production-ready")
check(R.band_for(80.0) == "production-ready", "band_for(80) == production-ready (inclusive)")
check(R.band_for(79.9) == "ships with known gaps", "band_for(79.9) == ships with known gaps")
check(R.band_for(60.0) == "ships with known gaps", "band_for(60) == ships with known gaps")
check(R.band_for(40.0) == "will generate support tickets", "band_for(40) == support tickets")
check(R.band_for(0.0) == "do not ship", "band_for(0) == do not ship")
check(R.band_for(-1.0) == "do not ship", "band_for(-1) == do not ship")

W = {"goal_outcome": 25, "hallucination": 20, "instruction_adherence": 20,
     "language_handling": 10, "objection_handling": 10, "escalation_safety": 10,
     "conversation_flow": 5}
check(R.weighted_score({k: 1.0 for k in W}, W) == 100.0, "weighted_score all-1.0 == 100.0")
check(R.weighted_score({}, W) == 0.0, "weighted_score of nothing == 0.0")
check(R.weighted_score({"goal_outcome": 1.0}, W) == 100.0,
      "weighted_score renormalises over the scored dimensions only")
check(R.weighted_score({"goal_outcome": 1.0, "hallucination": 0.0}, W) == 55.6,
      "weighted_score weights correctly (25/45 == 55.6)")
check(R.weighted_score({"goal_outcome": 1.0}, {"goal_outcome": 0}) == 0.0,
      "a zero-weight dimension cannot produce a score")

# Config weights, if readable, must still cover exactly these dimensions.
try:
    import yaml  # noqa: PLC0415
    raw = yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
    cfg_keys = set((raw.get("rubric") or {}).keys())
except Exception as exc:                     # config is not this file's contract
    print(f"  note  config.yaml rubric weights not readable ({exc.__class__.__name__})")
else:
    if cfg_keys:
        check(cfg_keys == set(EXPECTED_KEYS),
              "config.yaml rubric weights still cover exactly the seven dimensions")


# ── 6. §8.4 — no forbidden persona field may appear in any judge-facing text ─────────────

section("6. INTERFACES §8.4 (the judge grades the agent, not the persona)")

ALL_TEXT = "\n".join(
    [gt, ab] + [f"{d.question}\n{d.zero}\n{d.one}\n{d.prompt_addendum}" for d in R.DIMENSIONS]
)
for forbidden in ("persona_stresses", "persona_is_control", "system prompt", "is_control"):
    check(forbidden not in ALL_TEXT,
          f"no judge-facing text mentions {forbidden!r}")
check("did the persona win" not in ALL_TEXT.lower(), "no text invites grading the persona")


# ── 7. INFORMATIONAL: is judge.py wired to this data yet? (never fails) ──────────────────

section("7. wiring status (informational — judge/judge.py is another agent's file)")

try:
    from judge import judge as J  # noqa: PLC0415

    art = {"turns": [{"idx": 0, "speaker": "agent", "text": "hi"}],
           "ground_truth": {"discount_ceiling_pct": 10}, "scenario_vars": {},
           "end_reason": {"code": "done", "detail": ""}, "turn_count": {"total": 1},
           "duration_s": 1}
    detr = {"summary": "no objective violations", "observations": []}
    msgs = J.build_messages(art, detr, R.BY_KEY["goal_outcome"])
    blob = "\n".join(m["content"] for m in msgs)
    hall = "\n".join(m["content"] for m in
                     J.build_messages(art, detr, R.BY_KEY["hallucination"]))
    status = {
        "goal_outcome anchors in prompt": "0.7 — ADEQUATE" in blob or "0.6-0.8" in blob,
        "GROUND_TRUTH_BREACH_PROMPT in hallucination prompt": "ALLOWLIST" in hall,
        "ABSENCE_EVIDENCE_PROMPT in prompt": "turn: -1" in blob,
        "legacy inline 1.0 sentence GONE":
            "did everything correctly available to it" not in blob,
    }
    for label, ok in status.items():
        print(f"  {'ok   ' if ok else 'note '} {label}: {ok}")
    if not all(status.values()):
        print("  !!    rubric.py data is correct but judge/judge.py has NOT wired it yet "
              "(FIX_SPEC §2.2: getattr(dim, 'prompt_addendum', '') + the two module "
              "constants). D5a/D4-prompt CANNOT pass live until Agent B lands that wiring. "
              "This is informational here because judge/judge.py is not this agent's file.")
except Exception as exc:
    print(f"  note  judge/judge.py not inspectable right now ({exc.__class__.__name__}: {exc})")


# ── verdict ──────────────────────────────────────────────────────────────────────────────

print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
if FAILURES:
    print("FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("regress_rubric: GREEN")
