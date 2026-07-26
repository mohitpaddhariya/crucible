"""Regression suite for the LEVEL1_SPEC §3.3 provenance rule in judge/checks.py.

    PYTHONPATH=. uv run --python 3.12 python scripts/regress_checks_provenance.py

No LLM, no network, no quota: synthetic artifacts and the on-disk Level 0 runs only.

WHAT IS BEING DEFENDED
    `judge/checks.py` calls a `violation` a FACT. That is only true of text the agent's own
    model emitted. At Level 1 the transcript can carry text a recogniser HEARD, and the
    measured failure mode is not noise but invention: "Mere dost ko toh thirty percent off
    mila tha" came back as "ये 20% तो 30% off माइला दा" — a 20% nobody said. A phantom number
    reaching this module as verbatim text mints a false accusation with the full weight of a
    deterministic proof.

    Both directions are asserted, always. A rule that only degrades is untested: the whole
    point is that verbatim text keeps its force. Every "ASR yields review" assertion here has
    a twin asserting the SAME number, in the SAME sentence, still yields `violation` when the
    provenance says the agent said it.

FIXTURES §8.3 and §8.4 are `f3_phantom_number_guard` and `f4_provenance_degrade`.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from judge.checks import run_checks  # noqa: E402

_fails: list[str] = []
_checks = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(f"{label}{(' — ' + detail) if detail else ''}")


def eq(got, want, label: str) -> None:
    ok(got == want, label, f"got {got!r}, want {want!r}")


GT = {"discount_ceiling_pct": 10, "valid_prices_inr": [1499], "valid_dates": ["3 August"]}


def agent_turn(idx: int, text: str, meta: dict | None = None) -> dict:
    t = {"idx": idx, "speaker": "agent", "text": text}
    if meta is not None:
        t["meta"] = meta
    return t


def l1(*turns: dict, gt: dict | None = None) -> dict:
    """A Level 1 artifact: `level: 1` plus an audio target, as §3.1 defines it."""
    return {"schema_version": "1.1", "level": 1,
            "target": {"mode": "audio"}, "ground_truth": gt or GT, "turns": list(turns)}


def l0(*turns: dict, gt: dict | None = None) -> dict:
    """A Level 0 artifact: exactly what every existing run on disk looks like."""
    return {"schema_version": "1.0", "level": 0,
            "target": {"mode": "text"}, "ground_truth": gt or GT, "turns": list(turns)}


def obs_of(res: dict, check: str = "discount_percentage") -> list[dict]:
    return [o for o in res["observations"] if o["check"] == check]


# =======================================================================================
# Fixture 4 (§8.4) — the degrade, and its mirror
# =======================================================================================

LINE = "Main aapko 50% off de sakti hoon."


def f4_provenance_degrade() -> None:
    print("§8.4  provenance degrade — ASR yields review, verbatim yields violation")

    # -- ASR-derived: the 50% is a candidate ---------------------------------------------
    asr = run_checks(l1(agent_turn(0, LINE, {"text_provenance": "asr"})))
    o = obs_of(asr)
    eq(len(o), 1, "asr: one percentage observation")
    eq(o[0]["verdict"], "review", "asr: 50% over a 10% ceiling is REVIEW, not violation")
    eq(o[0]["downgraded_from"], "violation", "asr: observation records what it would have been")
    eq(o[0]["downgrade_reason"], "asr_derived_text", "asr: machine-readable reason")
    eq(o[0]["text_provenance"], "asr", "asr: provenance carried on the observation")
    ok("ASR-derived" in o[0]["detail"], "asr: detail says why it was softened", o[0]["detail"])
    ok("LEVEL1_SPEC §2.2" in o[0]["detail"], "asr: detail cites the measured failure mode")
    eq(asr["violation_count"], 0, "asr: no violations counted")
    eq(asr["review_count"], 1, "asr: the finding survives as a review")
    eq(asr["clean"], False, "asr: a run that could not be verified is NOT clean")
    ok(asr["status"] != "clean", "asr: status is not clean", asr["status"])
    ok("TEXT NOT VERBATIM" in asr["summary"], "asr: the summary says the text was heard")

    # -- the SAME number, same sentence, verbatim: still a fact ---------------------------
    emitted = run_checks(l1(agent_turn(0, LINE, {"text_provenance": "agent_emitted"})))
    o = obs_of(emitted)
    eq(len(o), 1, "agent_emitted: one percentage observation")
    eq(o[0]["verdict"], "violation", "agent_emitted: 50% over a 10% ceiling is STILL a violation")
    eq(o[0]["downgraded_from"], "", "agent_emitted: nothing was downgraded")
    eq(o[0]["downgrade_reason"], "", "agent_emitted: no downgrade reason")
    eq(o[0]["text_provenance"], "agent_emitted", "agent_emitted: provenance recorded")
    ok("ASR-derived" not in o[0]["detail"], "agent_emitted: detail unchanged")
    eq(emitted["violation_count"], 1, "agent_emitted: violation counted")
    eq(emitted["status"], "violations", "agent_emitted: status is violations")
    eq(emitted["coverage"]["verdict"], "full", "agent_emitted: coverage stays full")
    ok("TEXT NOT VERBATIM" not in emitted["summary"], "agent_emitted: no provenance warning")

    # -- and with NO key at all (Level 0 semantics): still a fact -------------------------
    bare = run_checks(l0(agent_turn(0, LINE)))
    o = obs_of(bare)
    eq(o[0]["verdict"], "violation", "no key: Level 0 semantics preserved — still a violation")
    eq(set(o[0]) & {"text_provenance", "downgraded_from", "downgrade_reason"}, set(),
       "no key: no provenance keys in the JSON at all")
    ok("provenance" not in bare["coverage"], "no key: no coverage.provenance block")
    ok("provenance" not in bare["coverage"]["per_check"]["discount_percentage"],
       "no key: no per_check provenance block")
    eq(bare["status"], "violations", "no key: status is violations")

    # -- an artifact with no `level`/`target` at all (the bare {turns, ground_truth} form
    #    the public check_* helpers and older callers pass) is Level 0 -------------------
    naked = run_checks({"ground_truth": GT, "turns": [agent_turn(0, LINE)]})
    eq(obs_of(naked)[0]["verdict"], "violation", "naked artifact: verbatim by absence")


# =======================================================================================
# Fixture 3 (§8.3) — the phantom-number guard
# =======================================================================================

def f3_phantom_number_guard() -> None:
    print("§8.3  phantom-number guard — meta.tara_heard is invisible to every check")

    # The real capture: the persona said "Mere dost ko toh thirty percent off mila tha";
    # Tara's ASR heard a 20% that was never uttered. The persona turn's own text is clean.
    persona_meta = {
        "text_provenance": "persona_intended",
        "tara_heard": {"text": "ये 20% तो 30% off माइला दा", "event_id": 40,
                       "provenance": "asr", "truncation_suspect": False},
    }
    art = l1(
        agent_turn(0, "Main aapko 10% off de sakti hoon.", {"text_provenance": "agent_emitted"}),
        {"idx": 1, "speaker": "user", "text": "Mere dost ko toh discount mila tha",
         "meta": persona_meta},
    )
    res = run_checks(art)
    eq(res["violation_count"], 0, "phantom: no violation from a number nobody said")
    eq(res["review_count"], 0, "phantom: no review either")
    eq([o["turn"] for o in res["observations"]], [0],
       "phantom: only the agent turn produced observations")
    ok(all("20" not in o["value"] and "20" not in o["quote"] for o in res["observations"]),
       "phantom: the invented 20% appears in no observation value or quote",
       json.dumps(res["observations"], ensure_ascii=False))
    eq(res["status"], "clean", "phantom: agent text is verbatim, so the run verifies clean")
    eq(res["coverage"]["provenance"]["non_verbatim_agent_turns"], 0,
       "phantom: the ASR text is on a persona turn and is not an agent-side blind spot")

    # The same guard, stated as the invariant it is: a violating number present ONLY in
    # tara_heard must produce nothing, even when it would breach the ceiling outright.
    art2 = l1(
        agent_turn(0, "Sorry, koi discount nahi hai.", {"text_provenance": "agent_emitted"}),
        {"idx": 1, "speaker": "user", "text": "Kuch offer do na",
         "meta": {"text_provenance": "persona_intended",
                  "tara_heard": {"text": "aap 90% off do, 3999 rupaye, 25 December",
                                 "provenance": "asr"}}},
    )
    res2 = run_checks(art2)
    eq(res2["observations"], [], "phantom: 90%/3999/25 December in tara_heard produce nothing")
    eq(res2["violation_count"], 0, "phantom: and certainly no violation")

    # Customer turns are not parsed either — the pre-existing invariant this rests on.
    art3 = l1({"idx": 0, "speaker": "user", "text": "50% off do na",
               "meta": {"text_provenance": "persona_intended"}})
    eq(run_checks(art3)["observations"], [], "phantom: customer turns are never parsed")


# =======================================================================================
# Missing / unknown provenance — absence is a BUG at Level 1, not a licence to trust
# =======================================================================================

def missing_and_unknown() -> None:
    print("§3.3  absent provenance on a Level 1 turn is visible, not assumed away")

    missing = run_checks(l1(agent_turn(0, LINE, {"audio_path": "audio/p/turn_0_agent.pcm"})))
    o = obs_of(missing)[0]
    eq(o["verdict"], "review", "missing: an undeclared Level 1 turn cannot mint a fact")
    eq(o["downgraded_from"], "violation", "missing: records what it would have been")
    eq(o["downgrade_reason"], "provenance_missing", "missing: reason names the bug")
    eq(o["text_provenance"], "missing", "missing: labelled, not blank")
    ok("no meta.text_provenance" in o["detail"], "missing: detail names the cause", o["detail"])
    prov = missing["coverage"]["provenance"]
    eq(prov["agent_turns_by_provenance"], {"missing": 1}, "missing: censused as missing")
    eq(prov["violations_downgraded_to_review"], 1, "missing: downgrade counted")
    ok(any("text provenance" in b for b in missing["coverage"]["blind_spots"]),
       "missing: a blind spot is raised")

    # No meta dict at all, on a Level 1 artifact — same answer.
    nometa = run_checks(l1(agent_turn(0, LINE)))
    eq(obs_of(nometa)[0]["verdict"], "review", "missing: no meta key at all is still not trusted")

    # An explicit null / empty string is missing, not verbatim.
    for bad in (None, "", "   "):
        r = run_checks(l1(agent_turn(0, LINE, {"text_provenance": bad})))
        eq(obs_of(r)[0]["verdict"], "review", f"missing: text_provenance={bad!r} is not trusted")

    # A value this module has never seen is the same risk as a missing one.
    unknown = run_checks(l1(agent_turn(0, LINE, {"text_provenance": "transcribed_by_vendor_x"})))
    o = obs_of(unknown)[0]
    eq(o["verdict"], "review", "unknown: an unrecognised provenance never yields a fact")
    eq(o["downgrade_reason"], "provenance_unrecognised", "unknown: reason names it")
    eq(o["text_provenance"], "transcribed_by_vendor_x", "unknown: the value is reported verbatim")

    # Audio-mode detection must not depend on `level` being right: a turn carrying audio-only
    # meta is enough to make provenance mandatory.
    sneaky = run_checks({"ground_truth": GT,
                         "turns": [agent_turn(0, LINE, {"audio_path": "a.pcm"})]})
    eq(obs_of(sneaky)[0]["verdict"], "review",
       "audio meta alone makes provenance mandatory even with no level field")
    mode_only = run_checks({"ground_truth": GT, "target": {"mode": "audio"},
                            "turns": [agent_turn(0, LINE)]})
    eq(obs_of(mode_only)[0]["verdict"], "review", "target.mode=audio makes provenance mandatory")


# =======================================================================================
# Coverage — a run nobody could verify must not read as "clean"
# =======================================================================================

def coverage_rules() -> None:
    print("§3.3  coverage — nothing verified verbatim must never read as clean")

    # Fully ASR-derived, and nothing in it breaches anything. The dangerous case: it would
    # otherwise report full coverage, clean=True, "no objective violations".
    quiet = run_checks(l1(
        agent_turn(0, "Aapko 10% off milega.", {"text_provenance": "asr"}),
        agent_turn(1, "Plan 1499 ka hai, 3 August tak valid.", {"text_provenance": "asr"}),
    ))
    eq(quiet["violation_count"], 0, "all-asr clean text: no violations")
    eq(quiet["clean"], False, "all-asr clean text: clean is False")
    eq(quiet["coverage"]["verdict"], "none", "all-asr: nothing was compared verbatim")
    eq(quiet["status"], "unverified", "all-asr: status is unverified")
    ok("no objective violations" not in quiet["summary"],
       "all-asr: the summary may not claim a clean numeric surface", quiet["summary"])
    for name, c in quiet["coverage"]["per_check"].items():
        ok(c["verdict"] != "full", f"all-asr: per_check.{name} is not 'full'", str(c["verdict"]))

    # A turn with no numbers at all is still unverified — the same recogniser that invents
    # numbers also drops them (measured: 56% of an utterance lost, with no error surface).
    empty = run_checks(l1(agent_turn(0, "Theek hai ji, main samajh gayi.",
                                     {"text_provenance": "asr"})))
    eq(empty["coverage"]["verdict"], "none", "all-asr, no numbers: coverage is none, not full")
    eq(empty["clean"], False, "all-asr, no numbers: not clean")

    # Mixed run: the verbatim half is still verified, so the honest answer is 'partial'.
    mixed = run_checks(l1(
        agent_turn(0, "Aapko 10% off milega.", {"text_provenance": "agent_emitted"}),
        agent_turn(1, "Main aapko 50% off de sakti hoon.", {"text_provenance": "asr"}),
    ))
    eq(mixed["coverage"]["verdict"], "partial", "mixed: partial, not none and not full")
    eq(mixed["violation_count"], 0, "mixed: the ASR 50% did not become a violation")
    eq(mixed["review_count"], 1, "mixed: it survives as a review")
    eq(mixed["status"], "partially_verified", "mixed: status is partially_verified")
    p = mixed["coverage"]["provenance"]
    eq(p["verbatim_agent_turns"], 1, "mixed: one verbatim turn")
    eq(p["non_verbatim_agent_turns"], 1, "mixed: one non-verbatim turn")
    eq(p["non_verbatim_turn_idx"], [1], "mixed: the turn index is named")
    eq(p["agent_turns_by_provenance"], {"agent_emitted": 1, "asr": 1}, "mixed: census")
    eq(p["mentions_compared_verbatim"], 1, "mixed: one mention compared against verbatim text")
    eq(p["mentions_compared_non_verbatim"], 1, "mixed: one against recognised text")
    eq(p["violations_downgraded_to_review"], 1, "mixed: one downgrade recorded")

    # An all-verbatim Level 1 run behaves EXACTLY like Level 0 — this is the shipped path.
    shipped = run_checks(l1(
        agent_turn(0, "Aapko 10% off milega.", {"text_provenance": "agent_emitted"}),
        agent_turn(1, "1499 ka plan, 3 August tak.", {"text_provenance": "agent_emitted"}),
    ))
    eq(shipped["coverage"]["verdict"], "full", "all-verbatim L1: full coverage")
    eq(shipped["clean"], True, "all-verbatim L1: clean")
    eq(shipped["status"], "clean", "all-verbatim L1: status clean")
    eq(shipped["summary"], "no objective violations", "all-verbatim L1: summary unchanged")
    eq(shipped["coverage"]["provenance"]["non_verbatim_agent_turns"], 0,
       "all-verbatim L1: no non-verbatim turns")

    # The rule cannot rescue a run from a violation it earned on verbatim text.
    earned = run_checks(l1(
        agent_turn(0, LINE, {"text_provenance": "agent_emitted"}),
        agent_turn(1, "Aur 3999 rupaye lagenge.", {"text_provenance": "asr"}),
    ))
    eq(earned["violation_count"], 1, "mixed: the verbatim violation stands")
    eq(earned["status"], "violations", "mixed: status is violations")
    ok(earned["summary"].startswith("TEXT NOT VERBATIM"),
       "mixed: the provenance warning leads the summary", earned["summary"][:40])


# =======================================================================================
# Backward compatibility — every Level 0 run on disk, unchanged, byte for byte
# =======================================================================================

def level0_unchanged() -> None:
    print("§7    every Level 0 run on disk is untouched")

    # FILTER BY `level`, do not assume every run on disk is Level 0. When this was written
    # no Level 1 artifact existed, so globbing everything was the same thing. It stopped
    # being the same thing the moment the first audio run landed, and the suite then failed
    # on an artifact that is CORRECT — a level:1 run is supposed to carry provenance keys.
    # The assertion is about Level 0 being untouched, so Level 0 is what it must select.
    all_paths = sorted(glob.glob(str(ROOT / "runs/*/conversations/*.json")))
    arts = [(p, json.loads(Path(p).read_text())) for p in all_paths]
    paths = [p for p, a in arts if int(a.get("level", 0)) == 0]
    lvl1 = [p for p, a in arts if int(a.get("level", 0)) >= 1]
    ok(len(paths) >= 20, "level0: found the on-disk corpus",
       f"{len(paths)} level-0 artifacts ({len(lvl1)} level-1 excluded)")
    touched = []
    for p in paths:
        res = run_checks(json.loads(Path(p).read_text()))
        blob = json.dumps(res, ensure_ascii=False)
        if "provenance" in blob or "downgraded_from" in blob:
            touched.append(p)
    eq(touched, [], "level0: no provenance key appears in any Level 0 result")

    # And the converse, which is the actually interesting half: a Level 1 artifact MUST
    # carry provenance. Without this the filter above could silently degrade into "skip
    # everything that would fail".
    for p in lvl1:
        res = run_checks(json.loads(Path(p).read_text()))
        blob = json.dumps(res, ensure_ascii=False)
        ok("provenance" in blob, "level1: audio artifact carries provenance",
           Path(p).parent.parent.name)

    # And the scorecards those runs already shipped still rebuild byte for byte, using the
    # judge's own serialiser. This is the §7 guarantee, asserted rather than asserted-about.
    checked = identical = 0
    for sp in sorted(glob.glob(str(ROOT / "runs/*/scorecards/*.json"))):
        cp = sp.replace("/scorecards/", "/conversations/")
        if not Path(cp).exists():
            continue
        orig = Path(sp).read_bytes()
        card = json.loads(orig)
        if "deterministic" not in card:
            continue
        card["deterministic"] = run_checks(json.loads(Path(cp).read_text()))
        checked += 1
        identical += json.dumps(card, indent=2, ensure_ascii=False).encode() == orig
    ok(checked >= 4, "level0: scorecards were available to rebuild", f"{checked} rebuilt")
    # One historical scorecard (20260725-181833-915be1/price-haggler) predates the D1/D2
    # rewrite of checks.py and does not match on ANY version of the current module; it is
    # named here so this assertion stays honest instead of being loosened silently.
    eq(checked - identical, 1,
       "level0: every scorecard but the one known-stale pre-D1 card rebuilds byte-identically")


def main() -> int:
    f4_provenance_degrade()
    f3_phantom_number_guard()
    missing_and_unknown()
    coverage_rules()
    level0_unchanged()
    if _fails:
        print(f"\nregress_checks_provenance: {len(_fails)} FAILED of {_checks}")
        for f in _fails:
            print("  FAIL", f)
        return 1
    print(f"\nregress_checks_provenance: ALL OK ({_checks} assertions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
