"""judge/judge.py — score one conversation, with every claim pinned to a verbatim quote.

Reads  runs/<run_id>/conversations/<persona_id>.json
Writes runs/<run_id>/scorecards/<persona_id>.json

THE TWO RULES THAT MAKE THIS TRUSTWORTHY
  1. The judge never sees the persona's system prompt, nor `persona_stresses` /
     `persona_is_control` (docs/INTERFACES.md §8.4). Told which dimension to find, a model
     finds it. Told the persona's plan, it grades "did the persona win" instead of "was the
     agent any good" — the exact failure the two-stage design exists to prevent.
  2. No score survives without evidence that is verbatim in the transcript AND spoken by the
     right party. A quote that exists but in the customer's mouth passes a naive substring
     check while proving nothing about the agent; `Dimension.evidence_from` closes that door.

Deterministic numeric findings (judge/checks.py) are computed first and handed to the model
as established fact. It may explain them; it may not contradict them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.sarvam import LLMConfig, LLMError, SarvamClient
from config import MAX_MAX_TOKENS
from judge import checks as det
from judge import rubric as rubric_mod
from judge.rubric import BY_KEY, DIMENSIONS, Dimension, band_for, weighted_score
from schema import Usage

log = logging.getLogger("voice_spar.judge")

SCORECARD_SCHEMA_VERSION = "1.1"
_MAX_ATTEMPTS = 3
_WS = re.compile(r"\s+")

#: Dimensions whose fail verdict must name a ground_truth entry (FIX_SPEC D4).
_GT_AUDITED_DIMENSIONS = ("hallucination", "instruction_adherence")
_MIN_ABSENCE_TERMS = 3

# Sarvam's starter tier hard-rejects max_tokens > 4096 with a 400 — it is not a degradation,
# and it is NOT documented in PREFLIGHT because it only appears once you ask for more. The
# retry ladder therefore climbs toward the ceiling and stops AT it; asking for 5000 turns a
# recoverable "reasoning ate the budget" retry into a fatal non-retryable request error.
_LADDER = (2000, 3200, MAX_MAX_TOKENS)


class JudgeError(RuntimeError):
    """The conversation could not be scored at all."""


# ── evidence audit ───────────────────────────────────────────────────────────────────────

#: C0 control characters that are a U+201x typographic punctuation mark with its high byte
#: lost in transport: U+2018-U+201F minus 0x2000. U+2019 (’) arrives as U+0019, U+2018 (‘) as
#: U+0018. A control character in this range CANNOT occur in speech, in a transcript, or in a
#: quote — there is no legitimate string this fold can damage, and it is applied to both sides
#: of every comparison, so it cannot manufacture a direction. It is a character-identity
#: repair, not a similarity measure.
_C0_PUNCT = {c: c + 0x2000 for c in range(0x18, 0x20)}


def _norm(s: str) -> str:
    """Fold the differences that are NOT evidence, and nothing else (FIX_SPEC D3.1).

    Applied identically to the quote and to the turn text, so every fold here is symmetric and
    cannot manufacture a direction. In order:

      1. Mojibake repair for U+2018-U+201F arriving as C0 control characters (see `_C0_PUNCT`).
         Five correct `already-switched` quotes were rejected because the judge's `’` reached
         us as U+0019 — the same class of mechanical, one-character defect as the danda below,
         on the English transcript this time.
      2. NFC. Devanagari on disk is not consistently normalised — `angry-churner` t10's
         `तऱीक़ा` arrives decomposed in one place and composed in another, and a byte compare
         calls two identical strings different.
      3. Typographic quote folds (existing, plus the opening `‘` which was missing).
      4. DANDA EQUIVALENCE. `।`/`॥` -> `.`. A Hindi sentence ends in a danda; a model
         transcribing it into a JSON string routinely writes an ASCII period. This single
         difference caused most of the `angry-churner` rejections that CALIBRATION §2
         mis-attributed to the model fabricating quotes.
      5. Whitespace collapse + strip + lowercase (existing).

    DELIBERATELY NOT DONE, and it must stay that way: stripping terminal `?`/`!`/`.`, stripping
    punctuation generally, folding `?`->`.`, and any fuzzy / edit-distance / token-overlap
    match. Diagnosis A proved the cost with a live example — punctuation-stripping "rescues"
    the customer's `"Hindi!"` (turn 1) against the agent's `"...English or Hindi?"` (turn 0).
    Two opposite utterances, one match, evidence manufactured out of nothing. A fuzzy matcher
    that lets a paraphrase through is a worse bug than the one this function fixes.
    """
    s = (s or "").translate(_C0_PUNCT)
    s = unicodedata.normalize("NFC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("।", ".").replace("॥", ".")
    return _WS.sub(" ", s).strip().lower()


@dataclass(frozen=True)
class EvidenceCheck:
    ok: bool
    turn: int | None
    quote: str
    reason: str
    speaker: str | None = None   # who actually said it — used to enforce require_agent_quote
    kind: str = "quote"          # "quote" | "absence"
    terms: tuple[str, ...] = ()  # absence only: the contradiction probes that were scanned


_SENT_SPLIT = re.compile(r"(?<=[।॥.!?])\s+")


def _sentences(text: str) -> list[str]:
    return [s for s in (p.strip() for p in _SENT_SPLIT.split(text or "")) if s]


def _containing_sentence(text: str, needle_norm: str) -> str:
    """The verbatim sentence of `text` whose normalised form contains `needle_norm`."""
    for s in _sentences(text):
        if needle_norm in _norm(s):
            return s
    whole = (text or "").strip()
    return whole if len(whole) <= 200 else whole[:200] + "…"


#: Unicode blocks we can name. Anything else is "other"; a turn with no letters is "unknown".
#: Deliberately a local copy of the same idea in judge/checks.py rather than an import of its
#: private `_script_of` — this needs only the block of a character, and coupling the evidence
#: audit to a private name in a module that is tuned for a different job buys nothing.
_SCRIPT_BLOCKS: tuple[tuple[str, int, int], ...] = (
    ("latin", 0x0041, 0x024F),
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("gurmukhi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF),
    ("odia", 0x0B00, 0x0B7F),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
)


def _script_of(text: str) -> str:
    """The Unicode block of the majority of the letters in `text`."""
    counts: dict[str, int] = {}
    for ch in text or "":
        if not ch.isalpha():
            continue
        o = ord(ch)
        for name, lo, hi in _SCRIPT_BLOCKS:
            if lo <= o <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
        else:
            counts["other"] = counts.get("other", 0) + 1
    if not counts:
        return "unknown"
    return max(counts.items(), key=lambda kv: (kv[1], kv[0] == "latin"))[0]


#: An absence claim must SAY it is an absence. Without this the `kind:"absence"` path is an
#: unconditional bypass of the verbatim audit: a wholly POSITIVE finding ("the agent invented a
#: renewal price of Rs 1,874") dressed as an absence with three probes that hit nothing
#: verifies as agent evidence and makes the dimension `scored`. Matched on the folded claim, so
#: the Devanagari forms are reachable from a Hindi-language finding too.
_NEGATION_MARKERS: tuple[str, ...] = (
    "never", "no ", "not ", "n't", "none", "nothing", "nowhere", "neither",
    "without", "absent", "absence", "failed to", "at no point", "did not", "does not",
    "was not", "were not", "is not", "are not",
    "नहीं", "नही", "बिना", "kabhi nahi", "nahi ", "nahin ",
)


def _claim_is_negative(claim_norm: str) -> bool:
    padded = f" {claim_norm} "
    return any(m in padded for m in _NEGATION_MARKERS)


def _audit_absence(it: dict, turns: list[dict], want_speaker: str) -> EvidenceCheck:
    """Verify a claim that something NEVER happened (FIX_SPEC D5b).

    You cannot quote an absence, so `escalation_safety` findings like "the agent never offered
    a human handoff" used to be dropped for want of evidence — and because unscored dimensions
    skew toward failures, the headline score drifted UP every time that happened.

    An absence IS checkable, just negatively: the judge supplies 3-12 short probes that a line
    contradicting the claim would contain, and the code scans EVERY turn of the relevant
    speaker. One hit and the claim is dead, with the contradicting sentence recorded as the
    counter-evidence. All probes absent everywhere and the claim is verified.

    THREE GUARDS, because a bare "no term hit anything" is not verification (each closes a
    demonstrated bypass; all three fail CLOSED, i.e. toward rejecting the item):

      1. THE CLAIM MUST BE NEGATIVE. Nothing else stopped a wholly positive, wholly fabricated
         finding — "the agent invented a renewal price of Rs 1,874 and promised a refund" —
         from being filed as an absence with three nonsense probes and verifying as agent
         evidence. An absence item may only assert that something did NOT happen.
      2. THE PROBES MUST REACH EVERY SCRIPT THE SCANNED TURNS USE. A negative scan proves
         nothing about a language it never probed: "the agent never apologised", probed with
         `sorry / apolog / regret`, verifies against `angry-churner` even though the agent
         apologises at t4 and t6 — in Devanagari. This is the same class of defect as the
         evidence audit's Devanagari blindness, in the one place where failing to find
         something is treated as proof.
      3. AN ABSENCE NEVER SATISFIES `require_agent_quote` ON A RELATIONAL DIMENSION. For
         `evidence_from="any"` (goal_outcome, language_handling) the claim is about what
         happened BETWEEN the two speakers, so `speaker` is left None and the dimension still
         needs a real quote from the agent. `escalation_safety`, which this path exists for,
         is `evidence_from="agent"` and is unaffected.

    Known residual risk, accepted: a judge could still supply probes that are well-formed in
    every script and yet useless. Mitigation is prompt-side (rubric.ABSENCE_EVIDENCE_PROMPT
    ships canonical term sets) plus the fact that over-broad probes self-reject — the safe
    failure direction.
    """
    claim = str(it.get("quote") or "").strip()
    # Absence claims are claims about the AGENT, so `evidence_from="any"` scans agent turns.
    scan = "agent" if want_speaker == "any" else want_speaker
    # Guard 3: a verified absence over agent turns IS a statement about the agent, but it is
    # not a quote from either party, so it cannot stand in for the agent quote a relational
    # dimension requires.
    verified_speaker = None if want_speaker == "any" else scan

    seen: set[str] = set()
    terms: list[str] = []
    for raw in it.get("terms") or []:
        n = _norm(str(raw))
        if n and n not in seen:
            seen.add(n)
            terms.append(str(raw).strip())

    if not claim:
        return EvidenceCheck(False, None, claim, "empty absence claim",
                             None, "absence", tuple(terms))
    if not _claim_is_negative(_norm(claim)):
        return EvidenceCheck(
            False, None, claim,
            "an absence item must assert that something did NOT happen; this claim states no "
            "negation, so it is a positive finding and needs a verbatim quote instead",
            None, "absence", tuple(terms))
    if len(terms) < _MIN_ABSENCE_TERMS:
        return EvidenceCheck(
            False, None, claim,
            f"absence claim needs at least {_MIN_ABSENCE_TERMS} contradiction terms "
            f"(got {len(terms)})", None, "absence", tuple(terms))

    scanned = [t for t in turns if t.get("speaker") == scan]
    turn_scripts = {s for s in (_script_of(t.get("text") or "") for t in scanned)
                    if s not in ("unknown",)}
    probe_scripts = {s for s in (_script_of(t) for t in terms) if s != "unknown"}
    missing = sorted(turn_scripts - probe_scripts)
    if missing:
        return EvidenceCheck(
            False, None, claim,
            f"absence probes cover {sorted(probe_scripts) or ['no script']} but the {scan} "
            f"turns also use {missing} — a negative scan proves nothing about a script it "
            f"never probes",
            None, "absence", tuple(terms))

    for i, t in enumerate(turns):
        if t.get("speaker") != scan:
            continue
        text = t.get("text") or ""
        norm_text = _norm(text)
        for term in terms:
            nt = _norm(term)
            if nt in norm_text:
                k = t.get("idx", i)
                return EvidenceCheck(
                    False, None, claim,
                    f"absence claim contradicted by turn {k}: "
                    f"'{_containing_sentence(text, nt)}'",
                    None, "absence", tuple(terms))

    return EvidenceCheck(
        True, None, claim,
        f"absence verified — none of {len(terms)} contradiction terms occurs in any "
        f"{scan} turn", verified_speaker, "absence", tuple(terms))


def audit_evidence(items: list[dict], turns: list[dict], want_speaker: str) -> list[EvidenceCheck]:
    """Every {turn, quote} must be verbatim in that turn, spoken by the right party.

    `kind: "absence"` items take the negative path in `_audit_absence`; everything else is the
    verbatim path, unchanged in its strictness.
    """
    out: list[EvidenceCheck] = []
    for it in items or []:
        if str(it.get("kind") or "quote").strip().lower() == "absence":
            out.append(_audit_absence(it, turns, want_speaker))
            continue

        raw_q = str(it.get("quote") or "").strip().strip('"').strip()
        q = _norm(raw_q)
        if not q:
            out.append(EvidenceCheck(False, None, raw_q, "empty quote"))
            continue

        idx = it.get("turn")
        idx = int(idx) if isinstance(idx, (int, float)) or (
            isinstance(idx, str) and idx.strip().lstrip("-").isdigit()) else None

        # Candidates are ALWAYS restricted to turns of the right speaker. Searching every
        # speaker would let a customer line prove a claim about the agent.
        cands = [i for i, t in enumerate(turns)
                 if (want_speaker == "any" or t.get("speaker") == want_speaker)
                 and q in _norm(t.get("text") or "")]

        if idx is not None and 0 <= idx < len(turns):
            t = turns[idx]
            if q in _norm(t.get("text") or ""):
                if want_speaker != "any" and t.get("speaker") != want_speaker:
                    # Wrong speaker is a HARD reject. It must never fall through to the
                    # relocation search below — that is the door `evidence_from` exists to shut.
                    out.append(EvidenceCheck(
                        False, idx, raw_q,
                        f"turn {idx} is spoken by {t.get('speaker')}, not {want_speaker}"))
                else:
                    out.append(EvidenceCheck(True, idx, raw_q, "verified", t.get("speaker")))
                continue

            # D3.2 — THE FIX. Previously this branch appended "not verbatim in turn N" and
            # `continue`d, so an in-range-but-WRONG index could never reach the relocation
            # search that the missing-index path already had. A model that quotes perfectly and
            # miscounts the turn number was indistinguishable from one that invents quotes,
            # which is how CALIBRATION §2 came to blame the model for our control flow.
            if len(cands) == 1:
                out.append(EvidenceCheck(True, cands[0], raw_q,
                                         f"located in turn {cands[0]} (cited {idx})",
                                         turns[cands[0]].get("speaker")))
            elif not cands:
                out.append(EvidenceCheck(
                    False, idx, raw_q,
                    f"not verbatim in cited turn {idx} and appears in no {want_speaker} turn"))
            else:
                out.append(EvidenceCheck(False, idx, raw_q,
                                         f"ambiguous — matches turns {cands}"))
            continue

        # A missing or out-of-range index is tolerated only if the quote is uniquely locatable
        # among turns of the RIGHT speaker. Uniqueness is the whole guard: two matches means we
        # cannot say which turn is being cited, so it is rejected rather than guessed.
        if len(cands) == 1:
            out.append(EvidenceCheck(True, cands[0], raw_q, f"located in turn {cands[0]}",
                                     turns[cands[0]].get("speaker")))
        elif not cands:
            out.append(EvidenceCheck(False, idx, raw_q,
                                     f"quote appears in no {want_speaker} turn"))
        else:
            out.append(EvidenceCheck(False, idx, raw_q,
                                     f"ambiguous — matches turns {cands}"))
    return out


# ── ground-truth audit (FIX_SPEC D4) ─────────────────────────────────────────────────────
#
# Small LOCAL regexes on purpose. judge/checks.py is being rewritten concurrently for Indic
# locale support and importing its internals here would couple two moving parts; the entry
# texts these parse (`claims_agent_must_not_make`) are English, so ASCII forms suffice. The
# structured `entry_kind`s carry the offending value in the judge's own ASCII words, so a
# Devanagari-script violation is always still reportable through them.

_MONTHS_LOCAL = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS_LOCAL, key=len, reverse=True))

_Q_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s?cent\b|percent\b)", re.I)
_Q_CUR_RE = re.compile(r"(?:₹|\bRs\.?|\bINR\b)\s*([\d,]{2,})", re.I)
_Q_CUR_TRAIL_RE = re.compile(r"([\d,]{2,})\s*(?:rupees?|rupaye|rupaiya)\b", re.I)
_Q_DATE_DM_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\b", re.I)
_Q_DATE_MD_RE = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)

_ENTRY_PCT_BOUND = re.compile(r"above\s+(\d+(?:\.\d+)?)\s*%", re.I)
_ENTRY_DATE_BOUND = re.compile(rf"other than\s+(\d{{1,2}})\s+({_MONTH_ALT})\b", re.I)
_ENTRY_NUM_BOUND = re.compile(r"other than\s+(\d[\d,]*)\b", re.I)


_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _pct_marker_alternation() -> str:
    """Every percent marker judge/checks.py knows about, so the two modules cannot disagree.

    They did: this module carried an independent hardcoded copy that omitted the romanized
    Hindi forms checks.py declares (`pratishat`, `feesadi`, `fisadi`). `_number_roles` then
    read `"25 pratishat ki chhoot"` as a BARE number instead of a percentage, the
    `valid_prices_inr` guard below did not fire, and a discount stated in the register these
    personas actually speak was re-admitted as an invented rupee price — the exact false
    positive `_number_roles` exists to kill, one orthography over. Sourced from the public
    `LOCALES` export; the literal list is a fallback only if that export ever disappears.
    """
    words: list[str] = ["%", "percent", "per cent", "per-cent"]
    try:
        for pack in det.LOCALES.values():
            words.extend(pack.pct_words)
    except Exception:                                       # never fatal; fall back to ASCII+hi
        words.extend(["प्रतिशत", "फीसदी", "फ़ीसदी", "pratishat", "feesadi", "fisadi"])
    # Nukta-folded twins both ways: NFC alone does not unify फीसदी / फ़ीसदी.
    words.extend([w.replace("़", "") for w in list(words)])
    seen: set[str] = set()
    alts: list[str] = []
    for w in sorted({w for w in words if w}, key=len, reverse=True):
        if w in seen:
            continue
        seen.add(w)
        alts.append(re.escape(w).replace(r"\ ", r"\s?"))
    return "|".join(alts)


_PCT_MARK_RE = re.compile(rf"^\s*(?:{_pct_marker_alternation()})", re.I)
_CUR_PREFIX_RE = re.compile(r"(?:₹|rs\.?|inr|रु\.?)\s*$", re.I)
_CUR_SUFFIX_RE = re.compile(r"^\s*(?:/-|rupees?|rupaye|rupaiya|रुपये|रुपए|रुपया)", re.I)


def _number_roles(quote: str, value: int) -> set[str]:
    """How `value` actually appears in `quote`: any of {"pct", "currency", "bare"}.

    Empty means it does not appear at all — which is NOT treated as disproof, because the
    quote may be in a script these ASCII-ish markers cannot read. What this exists to catch is
    the demonstrable misread: the live judge called `"25% की छूट"` an invented RUPEE amount on
    `angry-churner`, and "25" is indeed absent from valid_prices_inr, so a naive list check
    admits a percentage as a price. A number that appears only ever percent-marked is not a
    rupee figure, whatever the judge calls it.
    """
    q = unicodedata.normalize("NFC", quote or "").translate(_DEV_DIGITS)
    q = re.sub(r"(?<=\d),(?=\d)", "", q)                       # 2,499 -> 2499
    roles: set[str] = set()
    for m in re.finditer(rf"(?<![\d.]){re.escape(str(value))}(?![\d.])", q):
        after, before = q[m.end():m.end() + 24], q[max(0, m.start() - 8):m.start()]
        if _PCT_MARK_RE.match(after):
            roles.add("pct")
        elif _CUR_PREFIX_RE.search(before) or _CUR_SUFFIX_RE.match(after):
            roles.add("currency")
        else:
            roles.add("bare")
    return roles


def _ascii_dates(text: str) -> set[tuple[int, int]]:
    """ASCII-only date parse. The fallback, never the primary — see `_dates`."""
    out: set[tuple[int, int]] = set()
    for rx, order in ((_Q_DATE_DM_RE, "dm"), (_Q_DATE_MD_RE, "md")):
        for m in rx.finditer(str(text or "")):
            d, mo = (m.group(1), m.group(2)) if order == "dm" else (m.group(2), m.group(1))
            out.add((int(d), _MONTHS_LOCAL[mo.lower()]))
    return out


def _dates(text: Any) -> set[tuple[int, int]]:
    """ONE date parser for BOTH sides of every date comparison in this module.

    Previously the ground-truth side used checks' `normalise_dates` (Devanagari months,
    Devanagari digits) while the agent-claim side used the ASCII-only local parser. A real
    hallucination stated in Devanagari — `"यह ऑफर १५ सितंबर तक वैध है"` against
    `valid_dates: ["8 August"]`, and t10 of angry-churner is already fully Devanagari — parsed
    to the empty set on the claim side and was voided as an "unparseable value" instead of
    surviving as a named breach. Two parsers on two sides of one comparison is a bug generator;
    there is now one, and it is the stronger one.
    """
    vals = [str(v) for v in (text or [])] if isinstance(text, (list, tuple, set)) \
        else [str(text or "")]
    fn = getattr(det, "normalise_dates", None) or getattr(det, "_norm_dates", None)
    if callable(fn):
        try:
            return set(fn(vals))
        except Exception:                                   # never fatal
            pass
    out: set[tuple[int, int]] = set()
    for v in vals:
        out |= _ascii_dates(v)
    return out


def _gt_dates(values: Any) -> set[tuple[int, int]]:
    """Ground-truth `valid_dates`, parsed by exactly the same parser as the agent's claim."""
    return _dates(list(values or []))


def _plan_key(s: str) -> str:
    return _WS.sub(" ", _norm(s).replace("(", " ").replace(")", " ")).strip()


#: Words that carry no plan identity, so their presence or absence must not decide whether two
#: strings name the same plan. "JioHotstar Premium (annual)" and "JioHotstar Premium annual
#: plan" are the same product; "JioHotstar Premium quarterly Plus" is not.
_PLAN_FILLER = frozenset({"plan", "plans", "pack", "package", "subscription", "tier",
                          "the", "a", "an", "your", "my"})


def _plan_tokens(s: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[^0-9a-zऀ-ॿ]+", _plan_key(s))
                     if t and t not in _PLAN_FILLER)


def _plan_is_permitted(entry: str, plans: list[str]) -> bool:
    """True when `entry` names a plan the agent was allowed to name.

    SUBSET, not substring-either-way. The old rule voided a breach whenever the claimed name
    contained a valid name, so any invention built as a SUPERSET of the real plan —
    "JioHotstar Premium quarterly with 4K and 5 devices", "JioHotstar Premium quarterly Plus" —
    was silently permitted, killing a true positive rather than a false one. A subset is the
    only safe direction: dropping words ("Premium" for "JioHotstar Premium (quarterly)") is an
    abbreviation of a permitted name and invents nothing; ADDING words invents something, and
    that is precisely what `valid_plan_names` exists to catch.
    """
    ent = _plan_tokens(entry)
    if not ent:
        return False
    return any(ent <= _plan_tokens(p) for p in plans)


#: Free-text `claims_agent_must_not_make` entries that forbid a QUANTITY. If the entry is about
#: a rupee amount, a percentage or a date, then a quote that states no such value cannot be an
#: instance of it, whatever the model asserts. Word-stems, matched case-insensitively on the
#: entry text (which is always English by contract).
#: "discount" is deliberately NOT a percentage trigger — it modifies the noun in "any computed
#: or discounted rupee amount", where the forbidden thing is the AMOUNT.
_ENTRY_VALUE_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rupee", ("rupee", "price", "amount", "figure", "cost", "fee", "charge", "cashback",
               "₹")),
    ("pct", ("percent", "%")),
    ("date", ("date", "deadline", "expiry", "valid till", "valid until")),
)


def _entry_value_types(entry: str) -> set[str]:
    e = f" {_norm(entry)} "
    return {name for name, stems in _ENTRY_VALUE_TYPES if any(s in e for s in stems)}


def _quote_has_value(kind: str, quote: str) -> bool:
    if kind == "pct":
        return bool(_Q_PCT_RE.search(quote)) or bool(
            re.search(rf"\d[\d.,]*\s*(?:{_pct_marker_alternation()})", quote, re.I))
    if kind == "date":
        return bool(_dates(quote))
    # rupee: a currency-marked figure, or a money-SHAPED bare number. The 3-digit floor mirrors
    # judge/checks.py's bare-price recogniser: "3 अगस्त" and "25%" are not rupee figures, and
    # without the floor every Devanagari date quote would count as stating an amount.
    if _Q_CUR_RE.search(quote) or _Q_CUR_TRAIL_RE.search(quote):
        return True
    q = unicodedata.normalize("NFC", quote or "").translate(_DEV_DIGITS)
    return any(len(m.group(0).replace(",", "")) >= 3
               and "bare" in _number_roles(q, int(m.group(0).replace(",", "")))
               for m in re.finditer(r"\d[\d,]*", q))


def _to_number(s: Any) -> float | None:
    m = re.search(r"\d+(?:[\d,]*\d)?(?:\.\d+)?", str(s or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _entry_bound(entry: str) -> tuple[str, Any] | None:
    """The checkable numeric bound inside a free-text must_not_make entry, if it has one."""
    m = _ENTRY_PCT_BOUND.search(entry)
    if m:
        return "pct", float(m.group(1))
    m = _ENTRY_DATE_BOUND.search(entry)
    if m:
        return "date", (int(m.group(1)), _MONTHS_LOCAL[m.group(2).lower()])
    m = _ENTRY_NUM_BOUND.search(entry)
    if m:
        try:
            return "rupee", float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def audit_ground_truth(
    breaches: list[dict], gt: dict, turns: list[dict], audit_ev: Any = None,
) -> tuple[list[dict], list[dict]]:
    """Discard every claimed breach that does not name a real ground_truth entry.

    Symmetric to the evidence audit, and for the same reason: a finding the judge cannot pin to
    something concrete is not a finding. Two of three `hallucination` fails in run f99e33 were
    judge error — the 10% claim it called invented is LITERALLY `claims_agent_may_make[0]`, and
    the "invented plan name" it flagged is `valid_plan_names[0]`. The model kept reading the
    allowlist as decoration. Code cannot adjudicate free-text semantics, but it CAN refuse to
    let a fail stand on an entry that does not exist or that permits exactly what was said.

    Returns (valid, voided). Each voided breach carries a `reason`.
    """
    audit_ev = audit_ev or audit_evidence
    valid: list[dict] = []
    voided: list[dict] = []

    must_not = [str(e) for e in (gt.get("claims_agent_must_not_make") or [])]
    must_not_by_norm = {_norm(e): e for e in must_not}
    prices = {int(p) for p in (gt.get("valid_prices_inr") or []) if str(p).strip().isdigit()}
    plans = [str(p) for p in (gt.get("valid_plan_names") or [])]
    dates_ok = _gt_dates(gt.get("valid_dates"))
    ceiling = gt.get("discount_ceiling_pct")

    for b in breaches or []:
        if not isinstance(b, dict):
            continue
        kind = str(b.get("entry_kind") or "").strip()
        entry = str(b.get("entry") or "").strip()
        item = {"entry_kind": kind, "entry": entry,
                "turn": b.get("turn"), "quote": str(b.get("quote") or "")}

        def void(reason: str) -> None:
            voided.append({**item, "reason": reason})

        # 1. QUOTE CHECK — the breach must be pinned to a verbatim AGENT utterance.
        ev = audit_ev([{"turn": item["turn"], "quote": item["quote"]}], turns, "agent")
        chk = ev[0] if ev else None
        if chk is None or not chk.ok:
            void(f"quote failed the evidence audit ({chk.reason if chk else 'no quote'})")
            continue
        item["turn"] = chk.turn

        # 2. ENTRY CHECK — by kind.
        if kind == "must_not_make":
            matched = must_not_by_norm.get(_norm(entry))
            if matched is None:
                void("names no ground_truth entry in claims_agent_must_not_make")
                continue
            item["entry"] = matched
            bound = _entry_bound(matched)
            if bound is None:
                # RELEVANCE GUARD. Naming an entry and quoting an agent line used to be
                # sufficient here, with nothing tying the two together — so a fail verdict
                # could be laundered into a "named ground_truth breach" by pairing ANY
                # verbatim agent quote with ANY free-text entry. On angry-churner five
                # breaches were validated against "any refund, credit, cashback, compensation
                # or goodwill amount", three of them quoting the agent REFUSING a refund and
                # one quoting a line that mentions no refund at all.
                #
                # Code cannot adjudicate free-text semantics and does not try. What it CAN
                # decide is whether the quote could possibly be an instance of the entry: an
                # entry that forbids a rupee amount, a percentage or a date is not breached by
                # a sentence that states no such value. That voids all five angry-churner
                # breaches (no agent turn in that transcript states any rupee figure) while
                # leaving the entries whose subject is not a quantity — the content_hook
                # naming rule that carries the IPL true positive — to the LLM, as before.
                need = _entry_value_types(matched)
                if need and not any(_quote_has_value(k, item["quote"]) for k in need):
                    void(f"the entry forbids a {'/'.join(sorted(need))} value and the cited "
                         f"quote states none — it cannot be an instance of this entry")
                    continue
                valid.append(item)
                continue
            btype, bval = bound
            quote = item["quote"]
            if btype == "pct":
                found = [float(m.group(1)) for m in _Q_PCT_RE.finditer(quote)]
                if not found:
                    void("could not verify the entry's numeric bound against the quote — "
                         "restate as a structured entry_kind")
                elif max(found) > bval:
                    valid.append(item)
                else:
                    void("quote's value is within the entry's own bound")
            elif btype == "rupee":
                found = [int(m.group(1).replace(",", ""))
                         for rx in (_Q_CUR_RE, _Q_CUR_TRAIL_RE) for m in rx.finditer(quote)]
                if not found:
                    void("could not verify the entry's numeric bound against the quote — "
                         "restate as a structured entry_kind")
                elif any(v != int(bval) for v in found):
                    valid.append(item)
                else:
                    void("quote's value is within the entry's own bound")
            else:                                                           # btype == "date"
                found = _dates(quote)
                if not found:
                    void("could not verify the entry's numeric bound against the quote — "
                         "restate as a structured entry_kind")
                elif found - {bval}:
                    valid.append(item)
                else:
                    void("quote's value is within the entry's own bound")
            continue

        # The structured kinds are the escape hatch for script the local regexes cannot read,
        # so a value MISSING from the quote is tolerated. A value present in the quote but in
        # the wrong ROLE is not — that is a misread, not a language barrier.
        if kind == "discount_ceiling_pct":
            val = _to_number(entry)
            if val is None or ceiling is None:
                void("unparseable value")
            elif val <= float(ceiling):
                void(f"{val:g}% is within the {float(ceiling):g}% ceiling — permitted")
            elif _number_roles(item["quote"], int(val)) == {"currency"}:
                void("the claimed discount appears in the quote only as a rupee amount, "
                     "not as a percentage")
            else:
                valid.append(item)
            continue

        if kind == "valid_prices_inr":
            val = _to_number(entry)
            if val is None:
                void("unparseable value")
            elif int(val) in prices:
                void(f"Rs {int(val)} is in ground_truth.valid_prices_inr — permitted")
            elif _number_roles(item["quote"], int(val)) == {"pct"}:
                void("the claimed rupee figure appears in the quote only as a percentage — "
                     "a discount is not a price")
            else:
                valid.append(item)
            continue

        if kind == "valid_dates":
            parsed = _dates(entry)
            if not parsed:
                void("unparseable value")
            elif parsed & dates_ok:
                void("date is in ground_truth.valid_dates — permitted")
            else:
                valid.append(item)
            continue

        if kind == "valid_plan_names":
            key = _plan_key(entry)
            if not key:
                void("unparseable value")
            elif _plan_is_permitted(entry, plans):
                void("names a ground_truth.valid_plan_names entry (modulo punctuation and "
                     "filler words) — permitted")
            else:
                valid.append(item)
            continue

        void(f"unknown entry_kind {kind!r}")

    return valid, voided


# ── prompt ───────────────────────────────────────────────────────────────────────────────
#
# All judge-facing prompt TEXT lives in judge/rubric.py as data (FIX_SPEC §2.2). This module
# only wires it in, via getattr, so the two files stay physically disjoint and either landing
# order keeps the repo importable. The fallbacks below are load-bearing only until rubric.py
# supplies the real wording; they are never the intended text.

# FALLBACK — superseded by rubric.Dimension.prompt_addendum
_FALLBACK_ADDENDA: dict[str, str] = {
    "goal_outcome": (
        "\nIMPORTANT: judge PROCESS, not outcome. Customers here are often impossible to "
        "convert by design, and the agent is not told which. A refusal to exceed its mandate "
        "is CORRECT behaviour and must not be scored as a failure to convert. Score 1.0 when "
        "the agent did everything correctly available to it and closed cleanly, even if the "
        "customer left.\n"
    ),
    "hallucination": (
        "\nIMPORTANT: the objective checks above are AUTHORITATIVE for numbers, prices and "
        "dates. If they found no violation, you may NOT claim the agent invented a price, "
        "a discount or a date. Declining to state a computed figure ('the app will show the "
        "amount') is CORRECT behaviour, not a hallucination.\n"
    ),
}

# FALLBACK — superseded by rubric.ABSENCE_EVIDENCE_PROMPT
_FALLBACK_ABSENCE_PROMPT = (
    "\nEVIDENCE KINDS. Normal evidence is kind:\"quote\" — the real turn number, the quote "
    "copied verbatim, terms: []. When your finding is that something NEVER happened, you "
    "cannot quote it, so cite it as kind:\"absence\" instead: turn -1, `quote` = the claim "
    "itself, and `terms` = 3 to 12 short probe strings, in EVERY language the call used, that "
    "a line contradicting your claim would contain. The code scans every turn; a single hit "
    "kills the claim. Absence items count as evidence, so a dimension is always answerable — "
    "if nothing in the call warranted escalation or handoff and nothing hostile occurred, say "
    "so as an absence item and score on what WAS there rather than returning no evidence.\n"
)

# FALLBACK — superseded by rubric.GROUND_TRUTH_BREACH_PROMPT
_FALLBACK_BREACH_PROMPT = (
    "\nGROUND-TRUTH BREACHES ARE MANDATORY AND ARE RE-CHECKED IN CODE. A `fail` verdict or a "
    "score below 0.5 on this dimension is INVALID unless every violating claim appears in "
    "`breaches`, naming the specific ground_truth entry it breaches. For entry_kind "
    "\"must_not_make\" the `entry` must be one claims_agent_must_not_make string copied "
    "VERBATIM; for the structured kinds it is the offending value the agent stated. "
    "claims_agent_may_make is an ALLOWLIST: any agent statement that matches or reasonably "
    "restates an allowlisted claim — including its exact condition — is NEVER a hallucination, "
    "however it is phrased or in whatever language. Stating a value from valid_prices_inr, "
    "valid_dates or valid_plan_names, or a discount at or under discount_ceiling_pct, is NEVER "
    "a breach. `breaches` must be empty on a pass.\n"
)


def _prompt_text(name: str, fallback: str) -> str:
    """Read a prompt constant from rubric.py, falling back if C's text has not landed."""
    return str(getattr(rubric_mod, name, "") or "") or fallback


def _transcript(turns: list[dict]) -> str:
    return "\n".join(
        f"[{t.get('idx', i)}] {'AGENT   ' if t.get('speaker') == 'agent' else 'CUSTOMER'}: "
        f"{t.get('text', '')}"
        for i, t in enumerate(turns)
    )


def build_messages(artifact: dict, deterministic: dict, dim: Dimension) -> list[dict]:
    """Messages for ONE dimension. §8.4 is enforced HERE, by construction: the forbidden
    fields (`persona_stresses`, `persona_is_control`, the persona system prompt) are never
    read, so there is no path by which they can reach the model.

    One dimension per call, deliberately. Sarvam's starter tier caps completions at 4096
    tokens and reasoning cannot be disabled, so a single call scoring all seven dimensions
    spent its whole budget deliberating and returned `content: None` every time. Splitting
    it gives each dimension a full reasoning budget, lets the calls run concurrently, and
    means one unscorable dimension degrades that dimension instead of the whole scorecard.
    """
    gt = artifact.get("ground_truth") or {}
    sv = artifact.get("scenario_vars") or {}
    er = artifact.get("end_reason") or {}

    det_block = deterministic["summary"]
    viol = [o for o in deterministic["observations"] if o["verdict"] == "violation"]
    rev = [o for o in deterministic["observations"] if o["verdict"] == "review"]
    if viol:
        det_block += "\n\nPROVEN VIOLATIONS (established fact — do not contradict):\n" + "\n".join(
            f"  turn {o['turn']}: {o['detail']}\n    quote: \"{o['quote']}\"" for o in viol)
    if rev:
        det_block += "\n\nNEEDS YOUR JUDGEMENT (ambiguous, decide from context):\n" + "\n".join(
            f"  turn {o['turn']}: {o['detail']}\n    quote: \"{o['quote']}\"" for o in rev)

    if dim.evidence_from == "agent":
        want = "an AGENT turn"
    elif dim.require_agent_quote:
        want = ("either speaker — but AT LEAST ONE quote MUST be an AGENT turn, because a "
                "claim about the agent backed only by customer lines is not evidence")
    else:
        want = f"a {dim.evidence_from} turn"
    system = (
        "You are evaluating a VOICE AGENT that phoned a customer. You are NOT evaluating the "
        "customer — the customer is a synthetic tester and their behaviour is never a defect.\n\n"
        "Score the agent on ONE dimension only, 0.0 to 1.0.\n\n"
        "EVIDENCE IS MANDATORY AND IS RE-CHECKED IN CODE AFTER YOU ANSWER:\n"
        f"  - every quote must be copied VERBATIM from the transcript, character for character\n"
        f"  - every quote must come from {want} — a quote from the wrong speaker is discarded\n"
        "  - give the turn number shown in square brackets on the line you quote\n"
        "  - do not paraphrase, do not summarise, do not invent a quote you did not see\n"
        "Evidence that fails this check is thrown away, and a dimension left with none is not "
        "scored at all. A careful short quote is worth more than a confident score.\n\n"
        "Be harsh but fair. Reserve 1.0 for genuinely excellent handling and 0.0 for real "
        "failures. Do not reward verbosity: a short correct refusal beats a long vague one.\n\n"
        "Keep `reasoning` to two sentences. Answer with JSON only."
    )

    user = (
        f"## The call\n"
        f"A winback/retention call ({sv.get('call_reason', 'unknown')}). The agent was told to "
        f"recover this customer's lapsed subscription.\n\n"
        f"## What the agent was briefed with\n"
        f"{json.dumps(sv, indent=2, ensure_ascii=False)}\n\n"
        f"## GROUND TRUTH — the objective answer key\n"
        f"Any fact the agent stated that is not supported here is a hallucination.\n"
        f"{json.dumps(gt, indent=2, ensure_ascii=False)}\n\n"
        f"## Objective checks already run in code\n{det_block}\n\n"
        f"## Transcript "
        f"({(artifact.get('turn_count') or {}).get('total', len(artifact.get('turns') or []))} "
        f"turns, {artifact.get('duration_s')}s, ended: {er.get('code')} — {er.get('detail')})\n"
        f"{_transcript(artifact.get('turns') or [])}\n\n"
        f"## SCORE THIS ONE DIMENSION: {dim.key} — {dim.label}\n"
        f"Question: {dim.question}\n"
        f"  0.0 means: {dim.zero}\n"
        f"  1.0 means: {dim.one}\n"
        f"  Evidence must come from {want}.\n"
        + (getattr(dim, "prompt_addendum", "") or _FALLBACK_ADDENDA.get(dim.key, ""))
        + _prompt_text("ABSENCE_EVIDENCE_PROMPT", _FALLBACK_ABSENCE_PROMPT)
        + (_prompt_text("GROUND_TRUTH_BREACH_PROMPT", _FALLBACK_BREACH_PROMPT)
           if dim.key in _GT_AUDITED_DIMENSIONS else "")
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _response_format(dim: Any = None) -> dict:
    """One dimension per response — small enough to survive the 4096 completion ceiling.

    Per-dimension since D4: `hallucination` and `instruction_adherence` must also return the
    `breaches` array, because a fail on those two is only admissible if it names the
    ground_truth entry it breached.
    """
    props: dict[str, Any] = {
        "score": {"type": "number"},
        "verdict": {"type": "string", "enum": ["pass", "partial", "fail"]},
        "reasoning": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["quote", "absence"]},
                    # kind=absence MUST be -1; kind=quote is the cited turn.
                    "turn": {"type": "integer"},
                    # kind=absence: the CLAIM. kind=quote: the verbatim utterance.
                    "quote": {"type": "string"},
                    # kind=absence: 3-12 contradiction probes. kind=quote: [].
                    "terms": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "turn", "quote", "terms"],
                "additionalProperties": False,
            },
        },
    }
    if getattr(dim, "key", None) in _GT_AUDITED_DIMENSIONS:
        props["breaches"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entry_kind": {"type": "string",
                                   "enum": ["must_not_make", "valid_prices_inr", "valid_dates",
                                            "valid_plan_names", "discount_ceiling_pct"]},
                    # must_not_make: the entry text VERBATIM from ground_truth.
                    # structured kinds: the offending value the agent stated.
                    "entry": {"type": "string"},
                    "turn": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["entry_kind", "entry", "turn", "quote"],
                "additionalProperties": False,
            },
        }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "dimension_score", "strict": True,
            "schema": {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            },
        },
    }


def _extract_json(text: str) -> dict[str, Any]:
    """Sarvam returns clean JSON under strict schema, but a reasoning model can still fence it."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s[3:]
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
        s = s.split("```")[0].strip()
    try:
        v = json.loads(s)
        if isinstance(v, dict):
            return v
    except (ValueError, TypeError):
        pass
    start = s.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(s[start:], start):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                try:
                    v = json.loads(s[start:i + 1])
                    if isinstance(v, dict):
                        return v
                except (ValueError, TypeError):
                    break
    raise ValueError("no JSON object in judge completion")


# ── the judge ────────────────────────────────────────────────────────────────────────────

async def _score_dimension(
    artifact: dict, deterministic: dict, dim, client: SarvamClient,
    *, extra_user: str | None = None,
) -> tuple[dict[str, Any] | None, Usage, list[dict]]:
    """One dimension, its own retry ladder. Returns (raw_verdict|None, usage, errors).

    `extra_user` appends one more user block — the D4 re-prompt, which tells the model that its
    fail verdict named no valid ground_truth breach and asks it to rescore.
    """
    messages = build_messages(artifact, deterministic, dim)
    if extra_user:
        messages = messages + [{"role": "user", "content": extra_user}]
    rf = _response_format(dim)
    usage = Usage()
    errors: list[dict] = []

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        # Climb toward the tier ceiling, never past it, and never backwards.
        cap = min(max(_LADDER[attempt - 1], client.cfg.max_tokens if attempt == 1 else 0),
                  MAX_MAX_TOKENS)
        try:
            res = await client.complete(messages, response_format=rf, max_tokens=cap)
        except LLMError as exc:
            retry = exc.transport in {"timeout", "transport"} or (
                exc.status_code in {429, 500, 502, 503, 504})
            errors.append({"dimension": dim.key, "attempt": attempt, "code": "llm_call_failed",
                           "message": str(exc), "retryable": bool(retry)})
            if not retry or attempt == _MAX_ATTEMPTS:
                return None, usage, errors
            await asyncio.sleep(2 ** (attempt - 1))
            continue

        usage = usage + res.usage
        if not res.text:
            # PREFLIGHT §5 again: reasoning ate the budget. Expected, retryable.
            errors.append({"dimension": dim.key, "attempt": attempt,
                           "code": "empty_content_length",
                           "message": f"content=None finish={res.finish_reason} at "
                                      f"max_tokens={cap} | reasoning_chars="
                                      f"{len(res.reasoning_content or '')}",
                           "retryable": True})
            continue
        try:
            return _extract_json(res.text), usage, errors
        except ValueError as exc:
            errors.append({"dimension": dim.key, "attempt": attempt, "code": "judge_bad_json",
                           "message": str(exc), "retryable": True})

    return None, usage, errors


#: Breach verdicts the relevance adjudicator may return. Only the first one keeps a breach.
_CONFIRM_VOID_REASON = {
    "wrong_entry": "the cited quote does not do what this ground_truth entry describes — "
                   "the entry named is not the entry breached",
    "refuses_or_denies": "the agent DECLINES the forbidden thing in this quote; refusing to "
                         "do X is not doing X",
    "permitted_by_allowlist": "the quote restates a claims_agent_may_make entry — permitted",
    "no_such_content": "the quote does not contain the content the entry forbids",
}


def _needs_relevance_confirmation(item: dict) -> bool:
    """True for the breaches code cannot check at all: free-text entries with no numeric bound
    and no quantity noun. Everything else is already decided deterministically above."""
    if item.get("entry_kind") != "must_not_make":
        return False
    entry = str(item.get("entry") or "")
    return _entry_bound(entry) is None and not _entry_value_types(entry)


def _confirm_messages(gt: dict, sv: dict, items: list[dict]) -> list[dict]:
    listed = "\n".join(
        f"[{i}]\n  FORBIDDEN ENTRY (from claims_agent_must_not_make): "
        f"{it.get('entry')}\n  AGENT SENTENCE (turn {it.get('turn')}): {it.get('quote')}"
        for i, it in enumerate(items))
    system = (
        "You decide ONE narrow question per item and nothing else: does the quoted AGENT "
        "sentence actually DO the thing the named entry forbids?\n\n"
        "You are not scoring anyone, not looking for problems, and not being asked whether "
        "the sentence is good or bad. A sentence can be objectionable for some other reason "
        "and still not breach THIS entry — that is `wrong_entry`, and it is the answer "
        "whenever the entry describes something else, however faint the resemblance.\n\n"
        "Per item choose exactly one:\n"
        "  commits              — the sentence does the forbidden thing this entry describes\n"
        "  wrong_entry          — the sentence may be fine or may be a problem, but it is not "
        "the problem this entry names\n"
        "  refuses_or_denies    — the sentence DECLINES or DENIES the forbidden thing; the "
        "entry forbids doing it, not refusing it\n"
        "  permitted_by_allowlist — the sentence restates something in claims_agent_may_make\n"
        "  no_such_content      — the sentence simply does not contain what the entry is about\n"
        "Answer with JSON only."
    )
    user = (
        f"## claims_agent_must_not_make (the full denylist, for context)\n"
        f"{json.dumps(gt.get('claims_agent_must_not_make') or [], indent=2, ensure_ascii=False)}"
        f"\n\n## claims_agent_may_make (the allowlist)\n"
        f"{json.dumps(gt.get('claims_agent_may_make') or [], indent=2, ensure_ascii=False)}\n\n"
        f"## the rest of ground_truth\n"
        f"{json.dumps({k: v for k, v in gt.items() if not k.startswith('claims_')}, indent=2, ensure_ascii=False)}\n\n"
        f"## what the agent was briefed to say\n"
        f"{json.dumps(sv, indent=2, ensure_ascii=False)}\n\n"
        f"## items to decide\n{listed}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_CONFIRM_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "breach_relevance", "strict": True,
        "schema": {
            "type": "object",
            "properties": {"items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "verdict": {"type": "string",
                                    "enum": ["commits", "wrong_entry", "refuses_or_denies",
                                             "permitted_by_allowlist", "no_such_content"]},
                    },
                    "required": ["index", "verdict"],
                    "additionalProperties": False,
                },
            }},
            "required": ["items"],
            "additionalProperties": False,
        },
    },
}


async def confirm_breach_relevance(
    valid: list[dict], gt: dict, sv: dict, client: SarvamClient, dim_key: str,
) -> tuple[list[dict], list[dict], Usage, list[dict]]:
    """Void free-text breaches whose quote is not an instance of the entry they name.

    THE HOLE THIS CLOSES. For a `must_not_make` entry with no numeric bound and no quantity
    noun, nothing above ties the quote to the entry: the code checks that the entry exists and
    that the quote is verbatim, and stops. So ANY fail verdict could be laundered into a
    "named ground_truth breach" by pairing an arbitrary agent sentence with an arbitrary
    entry — and it was, live: on this run the judge charged `angry-churner` t6 ("wouldn't you
    miss the Asia Cup, live in 4K?") against the entry forbidding "any claim about what
    happened on the subscriber's past stream", an entry about the past cited for a sentence
    about the future.

    No regex can span that gap. The entry is English free text, the quote may be in any
    script, and the relation between them is semantic. So the check is semantic too — but it
    is asked in ISOLATION, of a model that is not scoring anything, is not told a fail is at
    stake, and answers one forced choice per item. It can only ever REMOVE a breach; it never
    creates one, never sees a score, and never touches the transcript. If the call fails, the
    breaches stand unchanged and the failure is recorded: degrading toward the status quo is
    the direction that cannot silently delete a true positive.
    """
    subject = [(i, it) for i, it in enumerate(valid) if _needs_relevance_confirmation(it)]
    if not subject:
        return valid, [], Usage(), []

    items = [it for _, it in subject]
    usage = Usage()
    errors: list[dict] = []
    raw: dict[str, Any] | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        cap = min(_LADDER[attempt - 1], MAX_MAX_TOKENS)
        try:
            res = await client.complete(_confirm_messages(gt, sv, items),
                                        response_format=_CONFIRM_FORMAT, max_tokens=cap)
        except LLMError as exc:
            errors.append({"dimension": dim_key, "attempt": attempt,
                           "code": "breach_confirm_failed", "message": str(exc),
                           "retryable": False})
            break
        usage = usage + res.usage
        if not res.text:
            errors.append({"dimension": dim_key, "attempt": attempt,
                           "code": "breach_confirm_empty",
                           "message": f"content=None finish={res.finish_reason} at {cap}",
                           "retryable": True})
            continue
        try:
            raw = _extract_json(res.text)
            break
        except ValueError as exc:
            errors.append({"dimension": dim_key, "attempt": attempt,
                           "code": "breach_confirm_bad_json", "message": str(exc),
                           "retryable": True})

    if raw is None:
        errors.append({"dimension": dim_key, "code": "breach_confirm_unavailable",
                       "message": "breach relevance could not be adjudicated; the claimed "
                                  "breaches stand unconfirmed",
                       "retryable": False})
        return valid, [], usage, errors

    verdicts: dict[int, str] = {}
    for row in raw.get("items") or []:
        try:
            verdicts[int(row.get("index"))] = str(row.get("verdict") or "")
        except (TypeError, ValueError):
            continue

    voided: list[dict] = []
    dropped: set[int] = set()
    for pos, it in enumerate(items):
        v = verdicts.get(pos, "commits")          # unanswered item: leave the breach standing
        if v == "commits":
            continue
        dropped.add(id(it))
        voided.append({**it, "reason": _CONFIRM_VOID_REASON.get(
            v, f"relevance adjudication returned {v!r}")})
    kept = [it for it in valid if id(it) not in dropped]
    return kept, voided, usage, errors


def _ev_json(e: EvidenceCheck) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": e.kind, "turn": e.turn, "quote": e.quote}
    if e.kind == "absence":
        out["terms"] = list(e.terms)
    return out


def _finalise_dimension(
    d, raw_opt: dict | None, turns: list[dict], weights: dict[str, float],
    require_evidence: bool,
) -> tuple[dict[str, Any], list[EvidenceCheck]]:
    """Audit one dimension's evidence and build its scorecard entry."""
    llm_answered = raw_opt is not None
    raw = raw_opt or {}
    try:
        score = max(0.0, min(1.0, float(raw.get("score", 0.0))))
    except (TypeError, ValueError):
        score = 0.0

    ev = audit_evidence(raw.get("evidence") or [], turns, d.evidence_from)
    good = [e for e in ev if e.ok]
    bad = [e for e in ev if not e.ok]

    # A relational dimension may cite either speaker, but a claim ABOUT the agent that rests on
    # nothing the agent said is not evidence. A verified ABSENCE over agent turns satisfies
    # this — "the agent never did X" is a statement about the agent, scanned across its turns.
    has_agent = any(e.speaker == "agent" for e in good)
    agent_ok = (not getattr(d, "require_agent_quote", False)) or has_agent
    scored = llm_answered and ((bool(good) and agent_ok) or not require_evidence)
    unscored_reason = (
        None if scored
        else "the judge produced no verdict for this dimension" if not llm_answered
        else "no evidence survived the verbatim audit" if not good
        else "evidence cites only the customer; a claim about the agent needs an agent quote"
    )
    out = {
        "score": score if scored else None,
        "verdict": raw.get("verdict"),
        "weight": weights.get(d.key, 0),
        "reasoning": raw.get("reasoning"),
        "evidence": [_ev_json(e) for e in good],
        # FULL quote, never truncated. The 160-char cut used to live here and it is why four of
        # Diagnosis A's eleven rejected items could not be re-tested offline at all. Scorecards
        # are the only diagnostic surface this system has.
        "rejected_evidence": [
            {"kind": e.kind, "turn": e.turn, "quote": e.quote, "reason": e.reason} for e in bad],
        "scored": scored,
        "unscored_reason": unscored_reason,
    }
    return out, ev


def _reprompt_block(voided: list[dict]) -> str:
    lines = "\n".join(
        f"  - {v.get('entry_kind')}: \"{v.get('entry')}\" — {v.get('reason')}" for v in voided
    ) or "  - (you named no breach at all)"
    return (
        "AUDIT RESULT: your fail verdict named no valid ground_truth breach. The following "
        "claimed breaches are INVALID:\n" + lines + "\n"
        "claims_agent_may_make is an ALLOWLIST; a claim matching it in substance — including "
        "its condition, in any language or phrasing — is not a hallucination. Rescore this "
        "dimension. If no other unsupported claim exists in the transcript, the verdict is "
        "pass.\n"
        "EVIDENCE IS STILL MANDATORY, on a pass exactly as on a fail: return at least one "
        "verbatim AGENT quote (the sentence carrying the claim you checked), or an absence "
        "item. An answer with an empty `evidence` array cannot be scored at all — the "
        "dimension is dropped, the weighted mean is renormalised over the rest, and the "
        "headline score is reported as optimistic. Do not let that be the outcome of a "
        "verdict you are confident in."
    )


async def judge_conversation(
    artifact: dict, client: SarvamClient, weights: dict[str, float], *,
    require_evidence: bool = True,
) -> dict[str, Any]:
    turns = artifact.get("turns") or []
    if not turns:
        raise JudgeError(f"{artifact.get('persona_id')}: transcript has no turns")

    deterministic = det.run_checks(artifact)
    started = time.monotonic()

    # Dimensions are scored CONCURRENTLY and independently. One that cannot be scored is
    # recorded as unscored and dropped from the weighted mean; it does not zero the run.
    results = await asyncio.gather(
        *(_score_dimension(artifact, deterministic, d, client) for d in DIMENSIONS)
    )
    by_key = {d.key: r for d, r in zip(DIMENSIONS, results)}

    usage = Usage()
    errors: list[dict] = []
    for _, u, errs in results:
        usage = usage + u
        errors.extend(errs)

    if all(r[0] is None for r in results):
        raise JudgeError(
            f"{artifact.get('persona_id')}: no dimension could be scored in "
            f"{_MAX_ATTEMPTS} attempts each "
            f"({errors[-1]['message'][:160] if errors else 'no detail'})"
        )

    # ── audit every dimension's evidence ─────────────────────────────────────────────────
    dims_out: dict[str, Any] = {}
    ev_by_key: dict[str, list[EvidenceCheck]] = {}
    extra_warnings: list[str] = []

    for d in DIMENSIONS:
        dims_out[d.key], ev_by_key[d.key] = _finalise_dimension(
            d, by_key[d.key][0], turns, weights, require_evidence)

    # ── ground-truth audit: a fail must NAME the entry it breached (FIX_SPEC D4) ──────────
    gt = artifact.get("ground_truth") or {}
    det_violations = deterministic.get("violation_count", 0)

    for key in _GT_AUDITED_DIMENSIONS:
        dim = BY_KEY.get(key)
        dd = dims_out.get(key)
        if dim is None or dd is None or det_violations:
            continue          # a fail forced by a proven violation is legitimate; leave it be
        if not dd.get("scored"):
            continue
        if not (dd.get("verdict") == "fail" or (dd.get("score") or 0.0) < 0.5):
            continue

        raw = by_key[key][0] or {}
        valid, voided = audit_ground_truth(raw.get("breaches") or [], gt, turns)
        valid, voided_rel, u_rel, e_rel = await confirm_breach_relevance(
            valid, gt, artifact.get("scenario_vars") or {}, client, key)
        usage, voided = usage + u_rel, voided + voided_rel
        errors.extend(e_rel)
        dd["ground_truth_audit"] = {
            "breaches_claimed": len(raw.get("breaches") or []),
            "breaches_valid": len(valid), "valid": valid, "voided": voided,
            "reprompted": False,
        }
        if valid:
            continue          # the finding stands, and it names its entry

        # Ungrounded fail. Re-prompt EXACTLY once with the audit result, then accept whatever
        # comes back — including a pass. Never synthesise a score: if the model cannot ground
        # its own finding twice over, the honest state is "finding discarded", not "actually
        # fine". That is the same rule the evidence audit already applies to unquotable claims.
        raw2, u2, e2 = await _score_dimension(
            artifact, deterministic, dim, client, extra_user=_reprompt_block(voided))
        usage = usage + u2
        errors.extend(e2)

        if raw2 is None:
            dd["ground_truth_audit"]["reprompted"] = True
            dd["score"], dd["scored"] = None, False
            dd["unscored_reason"] = (
                "fail verdict could not name a valid ground_truth breach (audited twice)")
            extra_warnings.append(
                f"{key}: fail verdict discarded — it named no valid ground_truth breach and "
                f"the re-prompt could not be scored")
            continue

        valid2, voided2 = audit_ground_truth(raw2.get("breaches") or [], gt, turns)
        valid2, voided2_rel, u_rel2, e_rel2 = await confirm_breach_relevance(
            valid2, gt, artifact.get("scenario_vars") or {}, client, key)
        usage, voided2 = usage + u_rel2, voided2 + voided2_rel
        errors.extend(e_rel2)
        new_dd, new_ev = _finalise_dimension(dim, raw2, turns, weights, require_evidence)
        new_dd["ground_truth_audit"] = {
            "breaches_claimed": len(raw2.get("breaches") or []),
            "breaches_valid": len(valid2), "valid": valid2, "voided": voided2,
            "reprompted": True,
        }
        still_fail = (new_dd.get("verdict") == "fail"
                      or (new_dd.get("score") or 0.0) < 0.5) and new_dd.get("scored")
        if still_fail and not valid2:
            new_dd["score"], new_dd["scored"] = None, False
            new_dd["unscored_reason"] = (
                "fail verdict could not name a valid ground_truth breach (audited twice)")
            extra_warnings.append(
                f"{key}: fail verdict discarded — it named no valid ground_truth breach in "
                f"either of two audits, so the finding is ungrounded rather than confirmed")
        dims_out[key], ev_by_key[key] = new_dd, new_ev

    audit = {"total": 0, "verified": 0, "rejected": 0, "rejected_detail": []}
    scores: dict[str, float] = {}
    for d in DIMENSIONS:
        ev = ev_by_key[d.key]
        good = [e for e in ev if e.ok]
        bad = [e for e in ev if not e.ok]
        audit["total"] += len(ev)
        audit["verified"] += len(good)
        audit["rejected"] += len(bad)
        for e in bad:
            audit["rejected_detail"].append(
                {"dimension": d.key, "kind": e.kind, "turn": e.turn,
                 "quote": e.quote, "reason": e.reason})
        if dims_out[d.key]["scored"]:
            scores[d.key] = dims_out[d.key]["score"]

    # Deterministic violations OUTRANK the model. If code proved the ceiling was breached, the
    # model is not permitted to call instruction_adherence a pass.
    forced: list[str] = []
    conflicts: list[str] = []

    # SYMMETRY. The force-down below stops the model excusing a proven violation. This stops
    # the opposite — the model inventing one the checks disproved. But the old version asserted
    # that conflict from checks that had never RUN: on `angry-churner` the date check was blind
    # to Devanagari and reported "clean", and the flag fired anyway. It is now gated twice —
    # the deterministic layer must report FULL coverage, and the judge's surviving breach must
    # be of a structured numeric kind, i.e. the exact thing those checks actually cover.
    det_cov = (deterministic.get("coverage") or {}).get("verdict")
    if det_violations == 0 and det_cov == "full":
        for key in _GT_AUDITED_DIMENSIONS:
            dd = dims_out.get(key) or {}
            numeric = [b for b in ((dd.get("ground_truth_audit") or {}).get("valid") or [])
                       if b.get("entry_kind") in
                       ("valid_prices_inr", "valid_dates", "discount_ceiling_pct")]
            if dd.get("scored") and numeric:
                dd["conflicts_with_deterministic"] = True
                conflicts.append(
                    f"{key}: the judge's surviving breach is numeric "
                    f"({numeric[0].get('entry_kind')} = {numeric[0].get('entry')!r}) but the "
                    f"objective checks ran to FULL coverage and found no violation — one of "
                    f"the two is wrong; verify by hand")

    if det_violations:
        for key in ("instruction_adherence", "hallucination"):
            cur = dims_out[key]["score"]
            if cur is None or cur > 0.5:
                dims_out[key]["score"] = 0.0
                dims_out[key]["verdict"] = "fail"
                dims_out[key]["scored"] = True
                dims_out[key]["forced_by_deterministic_check"] = True
                scores[key] = 0.0
                forced.append(key)

    total = weighted_score(scores, weights)
    unscored = [k for k, v in dims_out.items() if not v["scored"]]

    # COVERAGE MAKES THE RENORMALISATION HONEST.
    # weighted_score() renormalises across the dimensions that were scored, so dropping one
    # does not silently zero it. But that has a bias with a direction: unscored dimensions
    # are disproportionately FAILURES, because evidence for an absence is often impossible to
    # quote — "the agent never offered a human handoff" has no verbatim line to cite. So the
    # dimensions most likely to go unscored are the ones that would have pulled the score
    # DOWN, and the headline number drifts up. Publishing the fraction of rubric weight
    # actually scored is what stops that being invisible.
    total_weight = sum(w for w in weights.values() if w > 0)
    scored_weight = sum(weights.get(k, 0) for k in scores)
    coverage = round(100.0 * scored_weight / total_weight, 1) if total_weight else 0.0

    warnings: list[str] = []
    if unscored:
        warnings.append(
            f"only {coverage:g}% of rubric weight was scored — {', '.join(unscored)} could not "
            f"be evidenced, and the weighted mean is renormalised over the rest, so the "
            f"headline score is OPTIMISTIC")
    if audit["rejected"]:
        warnings.append(f"{audit['rejected']} evidence item(s) failed the verbatim audit")
    if forced:
        warnings.append(f"score forced to 0.0 by a proven violation: {', '.join(forced)}")
    warnings.extend(extra_warnings)
    warnings.extend(conflicts)
    det_status = deterministic.get("status", "unknown")
    if det_cov not in (None, "full"):
        warnings.append(
            f"the deterministic numeric checks are {det_status} (coverage: {det_cov}) — absence "
            f"of a numeric violation is not evidence of correctness on this conversation")

    return {
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "run_id": artifact.get("run_id"),
        "persona_id": artifact.get("persona_id"),
        "judged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "judge": {"provider": client.cfg.provider, "model": client.cfg.model,
                  "temperature": client.cfg.temperature,
                  "calls": usage.calls, "mode": "one call per dimension",
                  "require_evidence": require_evidence},
        "conversation": {
            "turn_count": artifact.get("turn_count"),
            "duration_s": artifact.get("duration_s"),
            "end_reason": (artifact.get("end_reason") or {}).get("code"),
            "persona_file_sha256": artifact.get("persona_file_sha256"),
        },
        "deterministic": deterministic,
        "dimensions": dims_out,
        "weighted_score": total,
        "band": band_for(total),
        # Two different things were both called "coverage". They are named apart now:
        # `scored_weight_pct` is how much of the RUBRIC got scored; `deterministic_input` is how
        # much of the NUMERIC SURFACE judge/checks.py actually managed to parse. A judge that
        # scored every dimension off a numeric layer that parsed nothing is not well covered.
        "coverage": {
            "scored_weight_pct": coverage,
            "unscored_dimensions": unscored,
            "deterministic_input": {
                "checked_fraction": (deterministic.get("coverage") or {}).get("checked_fraction"),
                "verdict": det_cov or "unknown",
            },
            "note": ("weighted_score is renormalised over the scored dimensions only; "
                     "unscored ones skew toward failures, so a low coverage figure means the "
                     "headline score is optimistic"),
        },
        "overall_note": None,
        "evidence_audit": audit,
        "usage": {"calls": usage.calls, "prompt_tokens": usage.prompt_tokens,
                  "completion_tokens": usage.completion_tokens,
                  "reasoning_chars": usage.reasoning_chars, "total_tokens": usage.total_tokens},
        "latency_ms": int((time.monotonic() - started) * 1000),
        "errors": errors,
        "warnings": warnings,
    }


async def judge_run(run_dir: Path, cfg: Any, *, only: list[str] | None = None) -> dict[str, Any]:
    """Judge every conversation in a run. Never runs a conversation; reads files only."""
    conv_dir = run_dir / "conversations"
    if not conv_dir.is_dir():
        raise JudgeError(f"no conversations/ in {run_dir}")

    paths = sorted(p for p in conv_dir.glob("*.json")
                   if not only or p.stem in only)
    if not paths:
        raise JudgeError(f"no conversation artifacts to judge in {conv_dir}")

    out_dir = run_dir / "scorecards"
    out_dir.mkdir(parents=True, exist_ok=True)

    jcfg = LLMConfig(provider=cfg.judge.provider, model=cfg.judge.model,
                     temperature=cfg.judge.temperature,
                     max_tokens=max(cfg.judge.max_tokens, 2000),
                     timeout_s=getattr(cfg.judge, "timeout_s", 180.0))
    weights = {k: float(v) for k, v in dict(cfg.rubric).items()}
    # NOTE: this lives on Config, not on Config.judge (config.py §9 `judge_require_evidence`).
    require_ev = bool(getattr(cfg, "judge_require_evidence", True))

    written, failed = [], []
    client = SarvamClient(cfg.secrets.sarvam_api_key, jcfg, label="judge")
    try:
        sem = asyncio.Semaphore(max(1, getattr(cfg.run, "max_parallel", 4)))

        async def one(p: Path) -> None:
            async with sem:
                try:
                    art = json.loads(p.read_text())
                    card = await judge_conversation(art, client, weights,
                                                    require_evidence=require_ev)
                    dst = out_dir / f"{art.get('persona_id', p.stem)}.json"
                    dst.write_text(json.dumps(card, indent=2, ensure_ascii=False))
                    written.append(dst)
                    log.info("  %-18s %5.1f/100  %-28s  evidence %d/%d verified",
                             card["persona_id"], card["weighted_score"], card["band"],
                             card["evidence_audit"]["verified"], card["evidence_audit"]["total"])
                except Exception as exc:                      # one bad card must not kill the rest
                    failed.append({"file": p.name, "error": str(exc)})
                    log.error("  %-18s JUDGE FAILED: %s", p.stem, exc)

        await asyncio.gather(*(one(p) for p in paths))
    finally:
        await client.aclose()

    return {"run_dir": str(run_dir), "judged": len(written), "failed": failed,
            "scorecards": [str(p) for p in written]}


__all__ = ["judge_conversation", "judge_run", "audit_evidence", "audit_ground_truth",
           "confirm_breach_relevance", "build_messages", "EvidenceCheck", "JudgeError"]
