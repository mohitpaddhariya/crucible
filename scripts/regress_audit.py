#!/usr/bin/env python3
"""scripts/regress_audit.py — the evidence audit and the ground-truth audit, offline.

    PYTHONPATH=. uv run --python 3.12 python scripts/regress_audit.py

No LLM, no network, no cost. Fixtures are the real transcripts and the Diagnosis A probe
already on disk; nothing here writes to runs/.

WHAT THIS FILE IS DEFENDING (FIX_SPEC D3, D4, D5b-B)

The fix it guards makes the audit MORE permissive in one narrow way — an in-range-but-wrong
turn index now falls through to the relocation search, and `।` compares equal to `.`. Every
step in that direction is a step toward a fuzzy matcher, and a fuzzy matcher that lets a
paraphrase through is a worse bug than the one being fixed. So the negative tests here are
the point of the file, not the positive ones:

  * wrong speaker still rejected, and never relocated
  * paraphrase still rejected
  * ambiguous multi-match still rejected
  * `"Hindi!"` vs `"...Hindi?"` still rejected  <- this is the canary. Diagnosis A proved that
    punctuation-stripping "rescues" a customer exclamation against the agent's question two
    turns earlier: opposite utterances, one match, evidence manufactured. If this test ever
    goes green-by-passing, the normalisation has been loosened too far.

10 of the probe's 11 rejections must be rescued. 11 of 11 is a FAILURE, not a better result.
"""

# NOTE: the brand in the quotes below is NOT renamed with the rest of the repo. These are
# VERBATIM quotes from recorded conversations in runs/, and the evidence audit matches
# them against those transcripts character for character. Renaming them here would be
# rewriting what the agent actually said, and the audit correctly rejects it.

from __future__ import annotations

import asyncio
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.sarvam import LLMResult  # noqa: E402
from judge.checks import run_checks as det_run_checks  # noqa: E402
from judge.judge import (  # noqa: E402
    _norm, _response_format, audit_evidence, audit_ground_truth, build_messages,
    judge_conversation,
)
from judge.rubric import BY_KEY  # noqa: E402
from schema import Usage  # noqa: E402

RUN = ROOT / "runs" / "20260725-185028-f99e33"
CONV = RUN / "conversations"
PROBE = RUN / "evidence_norm_probe.json"

_failures: list[str] = []
_checks = 0


def check(cond: bool, label: str) -> bool:
    global _checks
    _checks += 1
    if not cond:
        _failures.append(label)
        print(f"  FAIL  {label}")
    return bool(cond)


def turns_of(persona: str) -> list[dict]:
    return json.loads((CONV / f"{persona}.json").read_text())["turns"]


def gt_of(persona: str) -> dict:
    return json.loads((CONV / f"{persona}.json").read_text())["ground_truth"]


def one(items, turns, want):
    return audit_evidence(items, turns, want)[0]


# ── 1. _norm units (D3.1) ────────────────────────────────────────────────────────────────

def test_norm() -> None:
    print("[1] _norm")
    check(_norm("है।") == _norm("है."), "danda folds to period")
    check(_norm("ठीक॥") == _norm("ठीक."), "double danda folds to period")
    check(_norm("Hindi!") != _norm("Hindi?"), "! and ? must NOT be folded together")
    check(_norm("reactivate.") != _norm("reactivate?"), ". and ? must NOT be folded together")
    check(_norm("reactivate.") != _norm("reactivate"),
          "terminal punctuation must NOT be stripped")

    # The fixture on disk is genuinely NOT NFC — angry-churner t10 stores `तऱीक़ा` decomposed.
    # A precomposed quote of that phrase is not a raw substring of it; after _norm it is. This
    # is the whole NFC step, demonstrated on the real bytes rather than a constructed pair.
    t10 = [t for t in turns_of("angry-churner") if t.get("idx") == 10][0]["text"]
    check(t10 != unicodedata.normalize("NFC", t10), "t10 on disk really is un-normalised")
    phrase = "मेरे पास रिफंड देने का कोई तऱीक़ा नहीं है"
    check(phrase not in t10, "precomposed phrase is NOT a raw substring of the stored turn")
    check(_norm(phrase) in _norm(t10), "…but it IS after _norm — the NFC step earns its place")
    check(_norm(unicodedata.normalize("NFD", t10)) == _norm(unicodedata.normalize("NFC", t10)),
          "NFD and NFC forms of t10 normalise equal")

    check(_norm("  A   B  ") == "a b", "whitespace collapse + lowercase survive")
    check(_norm("it’s") == _norm("it's"), "typographic apostrophe fold survives")


# ── 2. the Diagnosis A probe: 10 of 11 must be rescued (D3 acceptance) ───────────────────

def test_probe() -> None:
    print("[2] Diagnosis A probe — 10/11 rescued, 1 stays rejected")
    probe = json.loads(PROBE.read_text())
    check(len(probe) == 11, f"probe has 11 items (got {len(probe)})")

    rescued, still = [], []
    for it in probe:
        turns = turns_of(it["persona"])
        r = one([{"turn": it["cited_turn"], "quote": it["quote"]}], turns, it["want_speaker"])
        tag = f"{it['persona']}/{it['dimension']}@{it['cited_turn']}"
        (rescued if r.ok else still).append((tag, r))

    check(len(rescued) == 10, f"exactly 10 rescued (got {len(rescued)}: "
                              f"{[t for t, _ in rescued]})")
    check(len(still) == 1, f"exactly 1 still rejected (got {len(still)}: "
                           f"{[t for t, _ in still]})")
    if still:
        tag, r = still[0]
        check(tag == "already-switched/goal_outcome@13",
              f"the one survivor of rejection is the genuine misquote (got {tag})")
        check("reactivate" in r.quote.lower(), "…and it is the 'How do I reactivate.' item")

    by_tag = dict(rescued)
    # danda-fold, same turn, index unchanged
    for t in (2, 8, 10):
        tag = f"angry-churner/hallucination@{t}"
        r = by_tag.get(tag)
        check(r is not None and r.turn == t, f"{tag} verified in its cited turn {t}")
    # truncated prefixes — substring semantics make a stored prefix valid evidence
    for t in (4, 6):
        tag = f"angry-churner/hallucination@{t}"
        r = by_tag.get(tag)
        check(r is not None and r.turn == t, f"{tag} (truncated prefix) verified in turn {t}")
    # RELOCATION — the D3.2 fix. Cited index in range but wrong.
    r = by_tag.get("angry-churner/instruction_adherence@2")
    check(r is not None and r.turn == 4, "instruction_adherence cited 2 relocates to turn 4")
    check(r is not None and "cited 2" in r.reason, "…and the relocation is disclosed in reason")
    r = by_tag.get("angry-churner/language_handling@0")
    check(r is not None and r.turn == 1 and r.speaker == "persona",
          "'Hindi!' cited 0, want=any relocates to turn 1 (persona)")
    r = by_tag.get("price-haggler/language_handling@0")
    check(r is not None and r.turn == 2, "price-haggler language_handling cited 0 -> turn 2")


# ── 3. the audit must NOT have got weaker (the load-bearing negatives) ───────────────────

def test_strictness() -> None:
    print("[3] strictness regressions — wrong speaker / paraphrase / bad index / ambiguity")
    ac = turns_of("angry-churner")

    # 3a. wrong speaker: a customer line demanded from an agent turn.
    r = one([{"turn": 0, "quote": "Hindi!"}], ac, "agent")
    check(not r.ok, "'Hindi!' with want_speaker=agent is rejected")
    check("no agent turn" in r.reason, f"…for the right reason (got {r.reason!r})")

    # 3b. wrong speaker, quote verbatim IN the cited turn. Must reject, must NOT relocate.
    r = one([{"turn": 1, "quote": "Hindi!"}], ac, "agent")
    check(not r.ok, "customer quote cited at its own (customer) turn, want=agent -> rejected")
    check("spoken by persona" in r.reason, f"…with the wrong-speaker reason (got {r.reason!r})")

    # 3c. customer quote verbatim in a customer turn, cited with an AGENT turn index.
    r = one([{"turn": 2, "quote": "Hindi!"}], ac, "agent")
    check(not r.ok, "customer quote cited at an agent-turn index, want=agent -> rejected")

    # 3d. paraphrase of t10 — one word swapped. Must die everywhere.
    real = [t for t in ac if t.get("idx") == 10][0]["text"]
    para = real.replace("समझती", "जानती")
    check(para != real, "the paraphrase actually differs from the transcript")
    for cited in (10, 8, None):
        r = one([{"turn": cited, "quote": para}], ac, "agent")
        check(not r.ok, f"paraphrase of t10 cited at {cited} -> rejected")

    # a light English paraphrase too, on a Latin-script transcript
    hp = turns_of("happy-path")
    r = one([{"turn": 4, "quote": "Since you are reactivating before 1 August, you will get "
                                  "5% off as a loyalty gesture."}], hp, "agent")
    check(not r.ok, "English paraphrase (you're -> you are) rejected")

    # 3e. ambiguity: a string present in two agent turns, cited with a third index.
    dup = "3 अगस्त"
    hits = [t.get("idx") for t in ac
            if t.get("speaker") == "agent" and dup in (t.get("text") or "")]
    check(len(hits) >= 2, f"'{dup}' really is in >=2 agent turns (got {hits})")
    r = one([{"turn": 0, "quote": dup}], ac, "agent")
    check(not r.ok, "quote matching several agent turns, cited elsewhere -> rejected")
    check("ambiguous" in r.reason, f"…as ambiguous (got {r.reason!r})")

    # 3f. out-of-range index still relocates uniquely, and still rejects when not unique.
    r = one([{"turn": 99, "quote": "मैं आपकी बात नोट कर लूँगी"}], ac, "agent")
    check(r.ok and r.turn == 10, "out-of-range index with a unique match still relocates")

    # 3g. empty quote, and a fabricated quote.
    check(not one([{"turn": 2, "quote": "   "}], ac, "agent").ok, "empty quote rejected")
    r = one([{"turn": 2, "quote": "आपको 50% की छूट मिलेगी"}], ac, "agent")
    check(not r.ok, "fabricated quote rejected")
    check("no agent turn" in r.reason, f"…for the right reason (got {r.reason!r})")


# ── 4. absence evidence (D5b, B-side) ────────────────────────────────────────────────────

def test_absence() -> None:
    print("[4] absence evidence")
    ac = turns_of("angry-churner")

    # A FALSE absence must die, and the contradiction must be cited.
    r = one([{"kind": "absence", "turn": -1,
              "quote": "the agent never discussed a refund",
              "terms": ["रिफंड", "refund", "वापसी"]}], ac, "agent")
    check(not r.ok, "false absence (refund IS discussed) rejected")
    check("contradicted by turn" in r.reason, f"…with the contradiction (got {r.reason!r})")
    check("रिफंड" in r.reason, "…and the contradicting sentence is quoted back")

    # A TRUE absence verifies, with turn=None and the scanned speaker recorded.
    handoff = ["transfer the call", "call you back", "supervisor", "इंसान से बात",
               "किसी और से बात करा"]
    for t in handoff:
        check(all(t not in (x.get("text") or "") for x in ac),
              f"precondition: '{t}' really is absent from the transcript")
    r = one([{"kind": "absence", "turn": -1,
              "quote": "the agent never offered to connect the customer to a human",
              "terms": handoff}], ac, "agent")
    check(r.ok, "true absence verified")
    check(r.turn is None, "verified absence carries turn=None")
    check(r.speaker == "agent", "verified absence records the scanned speaker (agent)")
    check(r.kind == "absence" and len(r.terms) == 5, "kind and terms round-trip")

    # Too few probes -> rejected. An absence backed by one word proves nothing.
    r = one([{"kind": "absence", "turn": -1, "quote": "never offered a handoff",
              "terms": ["transfer the call", "supervisor"]}], ac, "agent")
    check(not r.ok, "absence with 2 terms rejected")
    check("at least 3" in r.reason, f"…for the right reason (got {r.reason!r})")

    # Duplicate probes cannot pad the count.
    r = one([{"kind": "absence", "turn": -1, "quote": "never offered a handoff",
              "terms": ["supervisor", "Supervisor", "  supervisor  "]}], ac, "agent")
    check(not r.ok, "three copies of one probe do not count as three probes")

    # evidence_from="any" scans AGENT turns — absence claims are about the agent.
    r = one([{"kind": "absence", "turn": -1, "quote": "the agent never said 'refund'",
              "terms": ["रिफंड", "refund", "वापसी"]}], ac, "any")
    check(not r.ok, "want=any scans agent turns, so the refund contradiction still fires")
    r = one([{"kind": "absence", "turn": -1, "quote": "the agent never used these words",
              "terms": ["Pehle match", "poora din waste", "hostel ka friend",
                        "होस्टल", "बर्बाद"]}], ac, "any")
    check(r.ok, "want=any: customer-only phrases verify as absent from AGENT turns")
    check(r.speaker is None,
          "…but a relational dimension gets speaker=None: an absence is nobody's quote, so it "
          "cannot stand in for the agent quote require_agent_quote demands")

    # ── the three guards that stop kind:"absence" being a free bypass ────────────────────
    # (a) The claim must actually assert an absence. A positive fabrication dressed as one,
    #     with probes that hit nothing, used to verify as agent evidence and score the
    #     dimension — 0.0 on evidence naming nothing in the transcript.
    r = one([{"kind": "absence", "turn": -1,
              "quote": "the agent invented a renewal price of Rs 1,874 and promised a refund",
              "terms": ["zzzq", "qqxx", "wwvv"]}], ac, "agent")
    check(not r.ok, "a POSITIVE claim filed as an absence is rejected")
    check("did not happen" in r.reason or "negation" in r.reason,
          f"…because it asserts no absence (got {r.reason!r})")

    # (b) Probes must reach every script the scanned turns use. The agent apologises at t4 and
    #     t6 — in Devanagari — so Latin-only probes "verify" a false absence.
    r = one([{"kind": "absence", "turn": -1,
              "quote": "the agent never apologised for the poor viewing experience",
              "terms": ["sorry", "apolog", "regret", "maaf"]}], ac, "agent")
    check(not r.ok, "Latin-only probes cannot verify an absence over Devanagari turns")
    check("never probes" in r.reason, f"…and says why (got {r.reason!r})")
    r = one([{"kind": "absence", "turn": -1,
              "quote": "the agent never apologised for the poor viewing experience",
              "terms": ["sorry", "apolog", "regret", "maaf", "सॉरी", "माफ़", "खेद"]}],
            ac, "agent")
    check(not r.ok, "…and with the Devanagari probes added the false absence actually dies")
    check("contradicted by turn" in r.reason, f"…on the real apology (got {r.reason!r})")

    # (c) Nonsense probes in the wrong script are caught by (b); nonsense probes that DO cover
    #     the scripts remain the documented residual risk, mitigated prompt-side.
    r = one([{"kind": "absence", "turn": -1,
              "quote": "the agent never offered a callback at a stated time",
              "terms": ["zzzq", "qqxx", "wwvv"]}], ac, "agent")
    check(not r.ok, "Latin-only nonsense probes are rejected on a Devanagari transcript")

    # Empty claim rejected; quote-kind items are entirely untouched by this path.
    check(not one([{"kind": "absence", "turn": -1, "quote": "", "terms": ["a", "b", "c"]}],
                  ac, "agent").ok, "empty absence claim rejected")
    r = one([{"kind": "quote", "turn": 10, "quote": "मैं आपकी बात नोट कर लूँगी", "terms": []}],
            ac, "agent")
    check(r.ok and r.kind == "quote" and r.turn == 10, "kind='quote' path unchanged")


# ── 5. ground-truth audit (D4) ───────────────────────────────────────────────────────────

def sent(persona: str, idx: int, needle: str) -> str:
    """The verbatim agent sentence at `idx` containing `needle` — real evidence, not invented."""
    text = [t for t in turns_of(persona) if t.get("idx") == idx][0]["text"]
    for part in text.replace("।", ".").split("."):
        if needle in part:
            return part.strip()
    return text


def test_ground_truth_audit() -> None:
    print("[5] ground-truth audit")

    # ---- price-haggler: the 10% false positive, killed two different ways ----
    ph_t, ph_gt = turns_of("price-haggler"), gt_of("price-haggler")
    q4 = "agar aap 8 August se pehle plan reactivate karti hain, toh aapko 10% ki chhoot milegi"
    check(q4 in [t for t in ph_t if t.get("idx") == 4][0]["text"], "precondition: t4 quote real")

    v, x = audit_ground_truth(
        [{"entry_kind": "discount_ceiling_pct", "entry": "10", "turn": 4, "quote": q4}],
        ph_gt, ph_t)
    check(not v and len(x) == 1, "10% breach VOIDED — it is at the ceiling")
    check("within" in x[0]["reason"], f"…for the right reason (got {x[0]['reason']!r})")

    # The free-text loophole: re-admitting a permitted number through a must_not_make entry.
    v, x = audit_ground_truth(
        [{"entry_kind": "must_not_make", "entry": "any discount above 10%",
          "turn": 4, "quote": q4}], ph_gt, ph_t)
    check(not v and len(x) == 1, "'any discount above 10%' VOIDED by the numeric guard")
    check("within the entry's own bound" in x[0]["reason"],
          f"…for the right reason (got {x[0]['reason']!r})")

    # Entry check passes (15 > 10) but the quote is fabricated -> still void.
    v, x = audit_ground_truth(
        [{"entry_kind": "discount_ceiling_pct", "entry": "15", "turn": 4,
          "quote": "aapko 15% ki chhoot milegi"}], ph_gt, ph_t)
    check(not v and "evidence audit" in x[0]["reason"],
          "15% breach with a fabricated quote VOIDED on the quote check")

    # A real over-ceiling claim, really spoken, would survive. Synthetic turns, real logic.
    fake = [{"idx": 0, "speaker": "agent", "text": "Main aapko 40% ki chhoot de sakti hoon."}]
    v, x = audit_ground_truth(
        [{"entry_kind": "must_not_make", "entry": "any discount above 10%", "turn": 0,
          "quote": "Main aapko 40% ki chhoot de sakti hoon."}], ph_gt, fake)
    check(len(v) == 1 and not x, "a genuine 40%-over-ceiling breach SURVIVES")

    # ---- already-switched: THE TRUE POSITIVE. This must never be filtered away. ----
    aw_t, aw_gt = turns_of("already-switched"), gt_of("already-switched")
    ipl = ("Yes, all live cricket, including the IPL, is included in your JioHotstar "
           "Premium plan at no extra cost.")
    entry = "naming any show, film, series or match other than the one in content_hook"
    check(entry in aw_gt["claims_agent_must_not_make"], "precondition: the entry exists")
    v, x = audit_ground_truth(
        [{"entry_kind": "must_not_make", "entry": entry, "turn": 12, "quote": ipl}],
        aw_gt, aw_t)
    check(len(v) == 1 and not x, "the IPL breach SURVIVES — the true positive is intact")
    check(v[0]["entry"] == entry, "…and it names the ground_truth entry verbatim")
    check(v[0]["turn"] == 12, "…at turn 12")

    # ---- angry-churner: every judge-error breach dissolves ----
    ac_t, ac_gt = turns_of("angry-churner"), gt_of("angry-churner")
    q12 = "यह ऑफर 3 अगस्त तक आपके अकाउंट पर रहेगा"
    check(q12 in [t for t in ac_t if t.get("idx") == 12][0]["text"], "precondition: t12 real")

    for kind, ent, why in (
        ("valid_plan_names", "JioHotstar Premium annual plan", "matches valid_plan_names"),
        ("valid_prices_inr", "2499", "is a valid price"),
        ("discount_ceiling_pct", "25", "is at the ceiling"),
        ("valid_dates", "3 August", "is a valid date"),
        ("must_not_make", "implied guarantee of discount", "names no entry"),
    ):
        v, x = audit_ground_truth(
            [{"entry_kind": kind, "entry": ent, "turn": 12, "quote": q12}], ac_gt, ac_t)
        check(not v and len(x) == 1, f"angry-churner {kind} '{ent}' VOIDED ({why})")
    v, x = audit_ground_truth(
        [{"entry_kind": "must_not_make", "entry": "implied guarantee of discount",
          "turn": 12, "quote": q12}], ac_gt, ac_t)
    check("names no ground_truth entry" in x[0]["reason"],
          f"…unmatched entry text says so (got {x[0]['reason']!r})")

    # A rupee figure the agent never said cannot be smuggled in via must_not_make either.
    v, x = audit_ground_truth(
        [{"entry_kind": "must_not_make", "entry": "any rupee figure other than 2499",
          "turn": 12, "quote": q12}], ac_gt, ac_t)
    check(not v and "could not verify" in x[0]["reason"],
          "rupee-bound entry against a quote with no rupee figure VOIDED, and says why")

    # THE ROLE GUARD. The live judge called "25% की छूट" an invented RUPEE amount, and 25 is
    # genuinely absent from valid_prices_inr — so a naive list check admits a percentage as a
    # price. A number that only ever appears percent-marked is not a rupee figure.
    q4 = "अगर आप 3 अगस्त तक अपना प्लान फिर से चालू करते हैं, तो आपको 25% की छूट मिल जाएगी"
    check(q4 in [t for t in ac_t if t.get("idx") == 4][0]["text"], "precondition: t4 real")
    v, x = audit_ground_truth(
        [{"entry_kind": "valid_prices_inr", "entry": "25", "turn": 4, "quote": q4}], ac_gt, ac_t)
    check(not v, "a percentage claimed as an invented rupee figure is VOIDED")
    check("only as a percentage" in x[0]["reason"], f"…and says why (got {x[0]['reason']!r})")

    # The guard must not swallow the Devanagari escape hatch: a value the local regexes cannot
    # see in the quote at all still survives, because the entry carries it in ASCII.
    q2499 = "आपका प्लान २४९९ रुपये का है।"
    fake4 = [{"idx": 0, "speaker": "agent", "text": q2499}]
    v, x = audit_ground_truth(
        [{"entry_kind": "valid_prices_inr", "entry": "1999", "turn": 0, "quote": q2499}],
        ac_gt, fake4)
    check(len(v) == 1, "a value absent from the quote still survives (script escape hatch)")
    # …but a Devanagari-digit rupee amount IS read, and a permitted one is voided by the list.
    v, x = audit_ground_truth(
        [{"entry_kind": "valid_prices_inr", "entry": "2499", "turn": 0, "quote": q2499}],
        ac_gt, fake4)
    check(not v and "valid_prices_inr" in x[0]["reason"],
          "Devanagari-digit 2499 is voided as a permitted price")

    # Mirror guard: a rupee amount claimed as a discount.
    fake5 = [{"idx": 0, "speaker": "agent", "text": "Yeh plan Rs 40 ka hai."}]
    v, x = audit_ground_truth(
        [{"entry_kind": "discount_ceiling_pct", "entry": "40", "turn": 0,
          "quote": "Yeh plan Rs 40 ka hai."}], ac_gt, fake5)
    check(not v and "only as a rupee amount" in x[0]["reason"],
          "a rupee amount claimed as an over-ceiling discount is VOIDED")

    # Structured kinds still catch real breaches.
    fake2 = [{"idx": 0, "speaker": "agent", "text": "Aapka plan Rs 1799 ka hai."}]
    v, x = audit_ground_truth(
        [{"entry_kind": "valid_prices_inr", "entry": "1799", "turn": 0,
          "quote": "Aapka plan Rs 1799 ka hai."}], ac_gt, fake2)
    check(len(v) == 1, "a genuinely invented price SURVIVES as valid_prices_inr")

    fake3 = [{"idx": 0, "speaker": "agent", "text": "Offer 9 September tak valid hai."}]
    v, x = audit_ground_truth(
        [{"entry_kind": "valid_dates", "entry": "9 September", "turn": 0,
          "quote": "Offer 9 September tak valid hai."}], ac_gt, fake3)
    check(len(v) == 1, "a genuinely invented date SURVIVES as valid_dates")

    v, x = audit_ground_truth([{"entry_kind": "nonsense", "entry": "x", "turn": 12,
                                "quote": q12}], ac_gt, ac_t)
    check(not v and "unknown entry_kind" in x[0]["reason"], "unknown entry_kind VOIDED")
    check(audit_ground_truth([], ac_gt, ac_t) == ([], []), "no breaches -> nothing survives")


# ── 6. plumbing: schema + prompt wiring (must not crash if C has not landed) ─────────────

def test_plumbing() -> None:
    print("[6] schema + prompt plumbing")
    art = json.loads((CONV / "angry-churner.json").read_text())
    det_stub = {"summary": "no objective violations", "observations": []}

    for key in ("hallucination", "instruction_adherence"):
        props = _response_format(BY_KEY[key])["json_schema"]["schema"]["properties"]
        check("breaches" in props, f"{key} response schema carries `breaches`")
    props = _response_format(BY_KEY["escalation_safety"])["json_schema"]["schema"]["properties"]
    check("breaches" not in props, "escalation_safety schema has no `breaches`")

    ev_props = props["evidence"]["items"]["properties"]
    check(set(ev_props) == {"kind", "turn", "quote", "terms"}, "evidence item shape is D5b's")
    req = _response_format(BY_KEY["escalation_safety"])["json_schema"]["schema"]["required"]
    check(set(req) == {"score", "verdict", "reasoning", "evidence"}, "strict schema requires all")

    for d in BY_KEY.values():
        msgs = build_messages(art, det_stub, d)
        check(len(msgs) == 2, f"build_messages({d.key}) still returns system+user")
        u = msgs[1]["content"]
        check("absence" in u, f"{d.key} prompt explains absence evidence")
        if d.key in ("hallucination", "instruction_adherence"):
            check("ALLOWLIST" in u, f"{d.key} prompt states the allowlist rule")
        # §8.4: the forbidden fields must never reach the model.
        blob = json.dumps(msgs, ensure_ascii=False)
        for forbidden in ("persona_stresses", "persona_is_control", "system_prompt"):
            check(forbidden not in blob, f"{d.key} prompt leaks no {forbidden}")


# ── 7. the D4 enforcement flow, end to end, with a stub client (no network) ──────────────

class _Cfg:
    provider, model, temperature, max_tokens = "stub", "stub", 0.0, 2000


class StubClient:
    """Replays canned dimension verdicts. Records how many times each dimension was asked."""

    def __init__(self, script: dict[str, list[dict]], confirm: str = "commits"):
        self.cfg = _Cfg()
        self.script = {k: list(v) for k, v in script.items()}
        self.asked: list[str] = []
        self.confirm = confirm            # verdict the relevance adjudicator returns
        self.confirm_calls = 0

    async def complete(self, messages, *, response_format=None, max_tokens=None, **kw):
        # The dimension is named in the user block; the re-prompt is appended as a 3rd message.
        body = "\n".join(m["content"] for m in messages)
        if "## items to decide" in body:
            # The isolated free-text breach relevance call. It must never see a score, a
            # dimension, or the rubric — assert that here rather than trusting the caller.
            self.confirm_calls += 1
            check("SCORE THIS ONE DIMENSION" not in body,
                  "relevance call is isolated from the rubric prompt")
            n = body.count("FORBIDDEN ENTRY")
            return LLMResult(
                text=json.dumps({"items": [{"index": i, "verdict": self.confirm}
                                           for i in range(n)]}),
                finish_reason="stop", reasoning_content="", usage=Usage(calls=1),
                latency_ms=1, raw={})
        dim = next(k for k in self.script if f"## SCORE THIS ONE DIMENSION: {k} " in body)
        self.asked.append(dim)
        queue = self.script[dim]
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return LLMResult(text=json.dumps(payload, ensure_ascii=False), finish_reason="stop",
                         reasoning_content="", usage=Usage(calls=1), latency_ms=1, raw={})

    async def aclose(self):
        return None


_WEIGHTS = {"goal_outcome": 25.0, "hallucination": 20.0, "instruction_adherence": 15.0,
            "language_handling": 10.0, "objection_handling": 10.0,
            "escalation_safety": 10.0, "conversation_flow": 10.0}


def _pass(quote: str, turn: int) -> dict:
    return {"score": 1.0, "verdict": "pass", "reasoning": "ok",
            "evidence": [{"kind": "quote", "turn": turn, "quote": quote, "terms": []}],
            "breaches": []}


def _card(persona: str, overrides: dict[str, list[dict]],
          confirm: str = "commits") -> tuple[dict, StubClient]:
    art = json.loads((CONV / f"{persona}.json").read_text())
    agent0 = [t for t in art["turns"] if t["speaker"] == "agent"][0]
    base = _pass(agent0["text"], agent0["idx"])
    script = {d: [dict(base)] for d in _WEIGHTS}
    script.update(overrides)
    client = StubClient(script, confirm=confirm)
    card = asyncio.run(judge_conversation(art, client, _WEIGHTS))
    return card, client


def test_enforcement_flow() -> None:
    print("[7] D4 enforcement flow, end to end")

    # ---- already-switched: the TRUE POSITIVE must survive the whole pipeline ----
    ipl = ("Yes, all live cricket, including the IPL, is included in your JioHotstar "
           "Premium plan at no extra cost.")
    entry = "naming any show, film, series or match other than the one in content_hook"
    card, client = _card("already-switched", {"hallucination": [{
        "score": 0.0, "verdict": "fail", "reasoning": "named the IPL",
        "evidence": [{"kind": "quote", "turn": 12, "quote": ipl, "terms": []}],
        "breaches": [{"entry_kind": "must_not_make", "entry": entry,
                      "turn": 12, "quote": ipl}]}]})
    h = card["dimensions"]["hallucination"]
    check(h["scored"] and h["score"] == 0.0, "already-switched hallucination still FAILS")
    check(h["ground_truth_audit"]["breaches_valid"] == 1, "…with 1 surviving breach")
    check(h["ground_truth_audit"]["valid"][0]["entry"] == entry, "…naming the entry verbatim")
    check(h["ground_truth_audit"]["reprompted"] is False, "…and no re-prompt was needed")
    check(client.asked.count("hallucination") == 1, "…one LLM call for that dimension")
    check("conflicts_with_deterministic" not in h,
          "…and no numeric conflict flag (the breach is free-text)")

    # ---- price-haggler: the 10% false positive must be re-prompted away ----
    q4 = ("Khushkhabri yeh hai ki loyalty gesture ke taur par, agar aap 8 August se pehle "
          "plan reactivate karti hain, toh aapko 10% ki chhoot milegi")
    fail10 = {"score": 0.0, "verdict": "fail", "reasoning": "implies a guaranteed discount",
              "evidence": [{"kind": "quote", "turn": 4, "quote": q4, "terms": []}],
              "breaches": [{"entry_kind": "discount_ceiling_pct", "entry": "10",
                            "turn": 4, "quote": q4}]}
    card, client = _card("price-haggler",
                         {"hallucination": [fail10, _pass(q4, 4)]})
    h = card["dimensions"]["hallucination"]
    check(h["scored"] and h["score"] == 1.0, "price-haggler hallucination PASSES after re-prompt")
    check(h["ground_truth_audit"]["reprompted"] is True, "…and the re-prompt is disclosed")
    check(h["ground_truth_audit"]["breaches_valid"] == 0, "…with no surviving breach")
    check(client.asked.count("hallucination") == 2, "…exactly TWO calls, never more")

    # ---- an ungrounded fail that survives its own re-prompt is DISCARDED, not passed ----
    card, client = _card("price-haggler", {"hallucination": [fail10, fail10]})
    h = card["dimensions"]["hallucination"]
    check(not h["scored"], "twice-ungrounded fail is unscored")
    check(h["score"] is None, "…and carries no synthetic score")
    check("audited twice" in (h["unscored_reason"] or ""), "…with the audited-twice reason")
    check(any("no valid ground_truth breach" in w for w in card["warnings"]),
          "…and a scorecard warning")
    check(client.asked.count("hallucination") == 2, "…still exactly two calls")

    # ---- FREE-TEXT RELEVANCE: naming an entry is not the same as breaching it -------------
    # The live judge charged angry-churner t6 ("wouldn't you miss the Asia Cup, live in 4K?")
    # against the entry forbidding claims about the subscriber's PAST STREAM. Verbatim entry,
    # verbatim agent quote, zero relation — and every deterministic guard passes it, because
    # that entry carries no number and no quantity noun. Only the isolated relevance
    # adjudication can void it, so this asserts it does, and that a `commits` answer leaves a
    # real breach alone.
    t6 = [t for t in json.loads((CONV / "angry-churner.json").read_text())["turns"]
          if t["idx"] == 6][0]["text"]
    past = ("any claim about what happened on the subscriber's past stream, "
            "which it cannot see")
    wrong_entry = {"score": 0.0, "verdict": "fail", "reasoning": "4K claim",
                   "evidence": [{"kind": "quote", "turn": 6, "quote": t6, "terms": []}],
                   "breaches": [{"entry_kind": "must_not_make", "entry": past,
                                 "turn": 6, "quote": t6}]}
    card, client = _card("angry-churner", {"hallucination": [wrong_entry]},
                         confirm="wrong_entry")
    h = card["dimensions"]["hallucination"]
    check(client.confirm_calls >= 1, "a free-text breach is put to the relevance adjudicator")
    check(h["ground_truth_audit"]["breaches_valid"] == 0,
          "a breach whose quote does not do what the entry describes is VOIDED")
    check(any("not the entry breached" in (v.get("reason") or "")
              for v in h["ground_truth_audit"]["voided"]), "…with the wrong-entry reason")
    check(client.asked.count("hallucination") == 2,
          "…and the ungrounded fail is re-prompted exactly once, as any other")

    card, client = _card("angry-churner", {"hallucination": [wrong_entry]},
                         confirm="commits")
    check(card["dimensions"]["hallucination"]["ground_truth_audit"]["breaches_valid"] == 1,
          "…while a confirmed free-text breach still stands")

    # The IPL true positive must survive the adjudicator's `commits` answer end to end — the
    # relevance call may only ever REMOVE a breach, never weaken one that is confirmed.
    card, client = _card("already-switched", {"hallucination": [{
        "score": 0.0, "verdict": "fail", "reasoning": "named the IPL",
        "evidence": [{"kind": "quote", "turn": 12, "quote": ipl, "terms": []}],
        "breaches": [{"entry_kind": "must_not_make", "entry": entry,
                      "turn": 12, "quote": ipl}]}]}, confirm="commits")
    h = card["dimensions"]["hallucination"]
    check(h["scored"] and h["score"] == 0.0 and h["ground_truth_audit"]["breaches_valid"] == 1,
          "the IPL true positive survives the relevance adjudication")

    # Structured (numeric) breaches are decided in code and must never reach the adjudicator.
    card, client = _card("price-haggler", {"hallucination": [fail10, _pass(q4, 4)]})
    check(client.confirm_calls == 0,
          "a structured entry_kind is decided deterministically, with no extra LLM call")

    # ---- a fail that names a NUMERIC breach the checks disproved raises the conflict flag ----
    # (only when the deterministic layer reports FULL coverage — otherwise it stays silent)
    art = json.loads((CONV / "price-haggler.json").read_text())
    fake_num = {"score": 0.0, "verdict": "fail", "reasoning": "invented a price",
                "evidence": [{"kind": "quote", "turn": 4, "quote": q4, "terms": []}],
                "breaches": [{"entry_kind": "valid_prices_inr", "entry": "1349",
                              "turn": 4, "quote": q4}]}
    script = {d: [_pass(art["turns"][0]["text"], 0)] for d in _WEIGHTS}
    script["hallucination"] = [fake_num]
    # `None` here means the deterministic block has NO coverage key at all — i.e. checks.py has
    # not landed its half. B must read defensively and keep the gate CLOSED in that case.
    for cov, expect in (("full", True), ("partial", False), ("none", False), (None, False)):
        client = StubClient(script)
        det_patch = dict(det_run_checks(art))
        if cov is None:
            det_patch.pop("coverage", None)
        else:
            det_patch["coverage"] = {"verdict": cov, "checked_fraction": 1.0}
        card = asyncio.run(_judge_with_det(art, client, _WEIGHTS, det_patch))
        h = card["dimensions"]["hallucination"]
        got = h.get("conflicts_with_deterministic", False)
        check(got is expect,
              f"conflicts_with_deterministic is {expect} when deterministic coverage={cov}")
        check(card["coverage"]["deterministic_input"]["verdict"] == (cov or "unknown"),
              f"scorecard reports deterministic_input.verdict={cov or 'unknown'}")

    # ---- absence evidence lets escalation_safety score where it used to go unscored ----
    absence = {"score": 0.0, "verdict": "fail", "reasoning": "never offered a human",
               "evidence": [{"kind": "absence", "turn": -1,
                             "quote": "the agent never offered a human handoff",
                             "terms": ["transfer the call", "call you back", "supervisor",
                                       "इंसान से बात"]}],
               "breaches": []}
    card, _ = _card("angry-churner", {"escalation_safety": [absence]})
    e = card["dimensions"]["escalation_safety"]
    check(e["scored"] and e["score"] == 0.0, "escalation_safety scores on absence evidence alone")
    check(e["evidence"][0]["kind"] == "absence" and e["evidence"][0]["turn"] is None,
          "…and the scorecard records kind=absence with turn=null")
    check(e["evidence"][0]["terms"], "…and the probe terms it was verified against")

    # A false absence still leaves the dimension unscored — the safe direction.
    bad_absence = dict(absence)
    bad_absence["evidence"] = [{"kind": "absence", "turn": -1,
                                "quote": "the agent never mentioned refunds",
                                "terms": ["रिफंड", "refund", "वापसी"]}]
    card, _ = _card("angry-churner", {"escalation_safety": [bad_absence]})
    e = card["dimensions"]["escalation_safety"]
    check(not e["scored"], "contradicted absence leaves the dimension unscored")
    check("contradicted by turn" in e["rejected_evidence"][0]["reason"],
          "…and the contradiction is recorded as counter-evidence")

    # ---- full quotes reach the scorecard: no 160-char truncation anywhere ----
    long_bad = "x" * 400
    card, _ = _card("angry-churner", {"conversation_flow": [{
        "score": 0.5, "verdict": "partial", "reasoning": "r",
        "evidence": [{"kind": "quote", "turn": 2, "quote": long_bad, "terms": []},
                     {"kind": "quote", "turn": 2,
                      "quote": "मैं आपकी मदद करना चाहती हूँ", "terms": []}]}]})
    rej = card["dimensions"]["conversation_flow"]["rejected_evidence"][0]
    check(len(rej["quote"]) == 400, f"rejected quote stored in full (got {len(rej['quote'])})")
    detail = [d for d in card["evidence_audit"]["rejected_detail"] if len(d["quote"]) == 400]
    check(bool(detail), "…and in evidence_audit.rejected_detail too")
    check(card["schema_version"] == "1.1", "scorecard schema_version bumped to 1.1")


async def _judge_with_det(art, client, weights, det_patch):
    """judge_conversation with a substituted deterministic block (A's module is in flux)."""
    import judge.judge as J
    real = J.det.run_checks
    J.det.run_checks = lambda _a: det_patch
    try:
        return await J.judge_conversation(art, client, weights)
    finally:
        J.det.run_checks = real


def main() -> int:
    print("regress_audit — judge/judge.py evidence + ground-truth audits (offline)\n")
    test_norm()
    test_probe()
    test_strictness()
    test_absence()
    test_ground_truth_audit()
    test_plumbing()
    test_enforcement_flow()
    print()
    if _failures:
        print(f"FAILED — {len(_failures)}/{_checks} checks failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"OK — {_checks} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
