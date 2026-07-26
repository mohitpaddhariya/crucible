"""judge/checks.py — objective checks against `ground_truth`. No LLM, no network, no cost.

WHY THIS EXISTS
    The most damaging failures a winback agent can have — exceeding its discount ceiling,
    quoting a price it invented, promising a date that does not exist — are *numeric*. A
    number either is or is not in `ground_truth`. Asking a language model to adjudicate that
    is slower, costs money, and is less reliable than a regex.

    So the numeric surface is checked here, deterministically, and the result is handed to
    the LLM judge as established fact it may not contradict. The model is left to do what
    only it can: read tone, code-switching, de-escalation, and whether an objection was
    actually addressed.

SCRIPT-AGNOSTIC BY CONSTRUCTION (FIX_SPEC D1)
    Every surface form this module knows about lives in a `LocalePack`. The regexes are
    compiled ONCE, at import, from the UNION of every pack. Adding Tamil/Telugu/Bengali is
    adding a pack — not editing a regex. Hindi is simply the first tenant after English.

    All matching runs on a *folded* copy of the turn (NFC, nukta-stripped, native digits
    mapped to ASCII); spans are mapped back so every `Observation.quote` stays a verbatim
    substring of the original transcript.

COVERAGE IS NOT COMPUTED FROM THE PARSER (FIX_SPEC D2)
    A parser that matches nothing would otherwise report 0/0 and read as 100% coverage —
    which is exactly how `clean=True` came to mean "parsed nothing". So each check also has
    a second, deliberately over-broad *detector* that runs on every agent turn regardless of
    ground truth. `unrecognised = detected - parsed` is the number that makes a blind spot
    loud. The detector is never tuned for precision: a false detector hit costs one sample
    and a `partial` verdict, which is the safe failure direction. Under-broad is the only
    unsafe direction.

HONEST LIMITS — these are documented, not hidden:
  * Only AGENT turns are checked. The customer inventing a price is persona behaviour, not
    an agent defect, and conflating the two is the fastest way to a wrong score.
  * Plan names and the free-text `claims_agent_must_not_make` entries are NOT checked here.
    They need paraphrase matching, which is exactly what a regex is bad at. LLM territory.
  * Bare integers are reported at `medium` confidence. "1349" is almost certainly a computed
    price; "2026" almost certainly is not. The judge adjudicates the ambiguous ones.
  * Numeric `DD/MM` dates are reported at `medium` confidence and can only ever produce a
    `review`: the DD/MM-vs-MM/DD ambiguity must never yield a high-confidence violation.
  * A `violation` verdict from this module is a fact. A `review` verdict is a candidate.

A FACT IS ONLY A FACT WHEN THE TEXT IS VERBATIM (LEVEL1_SPEC §3.3)
    At Level 1 the transcript can contain text no model ever emitted: it can contain text a
    speech recogniser *heard*. Measured, twice, on the same line: we synthesised "Mere dost
    ko toh thirty percent off mila tha" and the ASR returned "ये 20% तो 30% off माइला दा" —
    a 20% nobody said. A number like that reaching this module as though it were verbatim
    would mint a provable ceiling violation out of a recogniser error, in the strongest
    voice the tool has.

    So every turn's `meta.text_provenance` is read, and a `violation` on a turn whose text is
    not verbatim is degraded to `review` — the verdict this module already defines as "a
    candidate". No new verdict type, no scorecard schema change.

    Absence is NOT silently trusted. A Level 0 artifact has no provenance vocabulary at all,
    and there absence correctly means verbatim; a Level 1 / audio-mode artifact that omits
    the key on a turn is a BUG in whatever wrote it, and the safe reading of a bug is "not
    provably verbatim". The two cases are distinguished from the artifact itself, not from a
    `.get()` default — see `_provenance_context`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Literal

Verdict = Literal["ok", "violation", "review"]
Confidence = Literal["high", "medium"]

_NUKTA = "़"          # DEVANAGARI SIGN NUKTA — NFC alone does NOT unify फीसदी/फ़ीसदी


# ---------------------------------------------------------------------------------------
# Locale packs — the only place a language's surface forms are written down.
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class LocalePack:
    code: str                                   # "en", "hi" — BCP-ish tag
    months: dict[str, int]                      # surface form (pre-fold) -> month number
    pct_words: tuple[str, ...]                  # percentage markers
    currency_prefix: tuple[str, ...]            # markers that PRECEDE the amount
    currency_suffix: tuple[str, ...]            # markers that FOLLOW the amount
    number_words: dict[str, int]                # spelled numerals, surface -> int
    digit_map: dict[str, str]                   # native digits -> ASCII
    sentence_terminators: tuple[str, ...]       # searched literally in the ORIGINAL text
    #: Month surfaces that are also ordinary words in this language ("may I ask...").
    #: They only count as a *date detection* when a digit sits next to them. This is the one
    #: place the detector is allowed to be less than maximally broad, and it is data, not a
    #: hard-coded English escape hatch: a new pack declares its own homographs (usually none).
    ambiguous_months: tuple[str, ...] = ()


_EN = LocalePack(
    code="en",
    months={
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
        "nov": 11, "november": 11, "dec": 12, "december": 12,
    },
    pct_words=("percent", "per cent"),
    currency_prefix=("₹", "Rs.", "Rs", "INR"),
    currency_suffix=("rupees", "rupee", "Rs.", "Rs", "/-"),
    number_words={},
    digit_map={},
    sentence_terminators=(". ", "! ", "? "),
    ambiguous_months=("may", "march", "mar"),
)

_HI = LocalePack(
    code="hi",
    months={
        "जनवरी": 1, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6, "जुलाई": 7,
        "अगस्त": 8, "अगस्थ": 8,
        "सितंबर": 9, "सितम्बर": 9,
        "अक्टूबर": 10, "अक्तूबर": 10,
        "नवंबर": 11, "नवम्बर": 11,
        "दिसंबर": 12, "दिसम्बर": 12,
    },
    pct_words=("प्रतिशत", "फीसदी", "फ़ीसदी", "pratishat", "feesadi", "fisadi"),
    # Hindi puts the unit on either side ("रु. 899" and "रुपये 899" are both said), so the
    # long forms are declared in both directions; the span dedupe stops double counting.
    currency_prefix=("रु", "रु.", "रुपये", "रुपए", "रुपया"),
    currency_suffix=("रुपये", "रुपए", "रुपया", "रु", "रु.", "rupaye", "rupaiya"),
    number_words={
        "एक": 1, "ek": 1, "दो": 2, "do": 2, "तीन": 3, "teen": 3, "चार": 4, "char": 4,
        "पाँच": 5, "पांच": 5, "paanch": 5, "छह": 6, "chhah": 6, "chhe": 6,
        "सात": 7, "saat": 7, "आठ": 8, "aath": 8, "नौ": 9, "nau": 9, "दस": 10, "das": 10,
        "पंद्रह": 15, "pandrah": 15, "बीस": 20, "bees": 20,
        "पच्चीस": 25, "pachchees": 25, "pachees": 25, "तीस": 30, "tees": 30,
        "चालीस": 40, "chalis": 40, "पचास": 50, "pachas": 50, "साठ": 60, "saath": 60,
        "सत्तर": 70, "sattar": 70, "अस्सी": 80, "assi": 80, "नब्बे": 90, "nabbe": 90,
        "सौ": 100, "sau": 100,
    },
    digit_map={"०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
               "५": "5", "६": "6", "७": "7", "८": "8", "९": "9"},
    # The danda never appears inside a number, so unlike ". " it needs no trailing space.
    sentence_terminators=("।", "॥"),
)

LOCALES: dict[str, LocalePack] = {"en": _EN, "hi": _HI}


# ---------------------------------------------------------------------------------------
# Normalisation pre-pass (D1.2) — folded text for matching, original text for evidence.
# ---------------------------------------------------------------------------------------

_DIGIT_TRANS = str.maketrans({d: a for p in LOCALES.values() for d, a in p.digit_map.items()})


def _fold(text: str) -> str:
    """NFC + nukta-fold + native-digit-fold. Never lowercases, never touches punctuation.

    The digit fold makes Devanagari-digit support explicit and testable instead of an
    accident of `\\d` matching them — a later `[0-9]` tightening cannot silently delete it.
    """
    t = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text))
    t = unicodedata.normalize("NFC", t.replace(_NUKTA, ""))
    return t.translate(_DIGIT_TRANS)


def _fold_with_map(text: str) -> tuple[str, list[int], list[int]]:
    """`(folded, starts, ends)` — `starts[i]`/`ends[i]` bracket the ORIGINAL characters that
    produced folded character `i`.

    The fold is applied per canonical cluster (a base character plus its combining marks),
    which is exactly the unit NFC composes over, so folding cluster-by-cluster equals folding
    the whole string while keeping an exact offset map.
    """
    parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    n, i = len(text), 0
    while i < n:
        j = i + 1
        while j < n and (unicodedata.combining(text[j]) != 0
                         or unicodedata.category(text[j]) in ("Mn", "Mc")):
            j += 1
        f = _fold(text[i:j])
        parts.append(f)
        starts.extend([i] * len(f))
        ends.extend([j] * len(f))
        i = j
    return "".join(parts), starts, ends


def _orig_span(starts: list[int], ends: list[int], fs: int, fe: int) -> tuple[int, int]:
    if not starts:
        return (0, 0)
    fs = max(0, min(fs, len(starts) - 1))
    if fe <= fs:
        return (starts[fs], starts[fs])
    fe = min(fe, len(ends))
    return (starts[fs], ends[fe - 1])


# ---------------------------------------------------------------------------------------
# Regex construction — from the union of the packs, never hand-written per language.
# ---------------------------------------------------------------------------------------

def _word_alt(words: Iterable[str]) -> str:
    """Alternation over folded surface forms, longest-first, with `\\w` guards on the ASCII
    ends only. `\\b` is script-hostile — Devanagari letters are `\\w`, so a guard that helps
    "percent" would silently change what "प्रतिशत" means."""
    parts: list[str] = []
    for w in sorted({_fold(w) for w in words if w}, key=len, reverse=True):
        e = re.escape(w)
        if w[0].isascii() and w[0].isalnum():
            e = r"(?<!\w)" + e
        if w[-1].isascii() and w[-1].isalnum():
            e = e + r"(?!\w)"
        parts.append(e)
    return "|".join(parts)


_MONTHS: dict[str, int] = {}
_MONTH_LOCALE: dict[str, str] = {}
for _p in LOCALES.values():
    for _surface, _num in _p.months.items():
        _MONTHS[_fold(_surface).lower()] = _num
        _MONTH_LOCALE[_fold(_surface).lower()] = _p.code

_NUMBER_WORDS: dict[str, int] = {}
_NUMWORD_LOCALE: dict[str, str] = {}
for _p in LOCALES.values():
    for _surface, _num in _p.number_words.items():
        _NUMBER_WORDS[_fold(_surface).lower()] = _num
        _NUMWORD_LOCALE[_fold(_surface).lower()] = _p.code

_AMBIGUOUS_MONTHS = {_fold(w).lower() for p in LOCALES.values() for w in p.ambiguous_months}
_TERMINATORS = tuple({t for p in LOCALES.values() for t in p.sentence_terminators})

_MONTH_RE = _word_alt(_MONTHS)
_MONTH_EN_RE = _word_alt(_EN.months)
_NUMWORD_RE = _word_alt(_NUMBER_WORDS)
_PCT_MARKER_RE = _word_alt(["%"] + [w for p in LOCALES.values() for w in p.pct_words])
_CUR_PREFIX_RE = _word_alt([w for p in LOCALES.values() for w in p.currency_prefix])
_CUR_SUFFIX_RE = _word_alt([w for p in LOCALES.values() for w in p.currency_suffix])

# -- percentages (D1.4) -----------------------------------------------------------------
# No {1,3} cap: "1000% off" must parse as 1000 and violate, never as "000" -> 0 -> ok.
_PCT_DIGIT = re.compile(rf"(?<![\d.,])(\d+(?:\.\d+)?)\s*(?:{_PCT_MARKER_RE})", re.I)
# A spelled numeral within two whitespace-separated tokens of a marker. The intervening
# token may not contain digits, so "do 10 percent" cannot be read as "do ... percent" = 2%.
_PCT_WORD = re.compile(
    rf"(?<!\w)({_NUMWORD_RE})(?!\w)(?:\s+[^\s\d]+)?\s*(?:{_PCT_MARKER_RE})", re.I)
_PCT_MARKER = re.compile(_PCT_MARKER_RE, re.I)
# Structural, not lexical (A12): 100 is a discount claim only next to a discount word.
_DISCOUNT_CTX = re.compile(
    r"discount|off|offer|deal|concession|chhoot|chhut|छूट|रियायत", re.I)
_IDIOM_WINDOW = 60

# -- currency (D1.5) --------------------------------------------------------------------
# Explicit digit/comma lookarounds instead of \b: '2499रुपये' (no space) must still match.
_CUR_PREFIXED = re.compile(rf"(?:{_CUR_PREFIX_RE})\s*(\d[\d,]*)(?![\d,])", re.I)
_CUR_SUFFIXED = re.compile(rf"(?<![\d.,])(\d[\d,]*)\s*(?:{_CUR_SUFFIX_RE})", re.I)
_PCT_TAIL = rf"(?![\d.,]*\s*(?:{_PCT_MARKER_RE}))"
# Indian grouping ('1,49,900') must stay visible; the .replace(",", "") normalisation
# downstream is correct and locale-safe — do not "fix" it into a bug.
_BARE_INT = re.compile(rf"(?<![\d.,%])(\d{{1,3}}(?:,\d{{2,3}})+|\d{{3,7}})(?![\d,]){_PCT_TAIL}",
                       re.I)
_CUR_MARKER = re.compile(rf"(?:{_CUR_PREFIX_RE})|(?:{_CUR_SUFFIX_RE})", re.I)

# -- dates (D1.3) -----------------------------------------------------------------------
_DATE_DM = re.compile(rf"(?<![\d.,])(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})(?!\w)", re.I)
# Month-first is an English word-order concession, not a universal: "अगस्त 3" is not idiomatic.
# `(?![\d,])(?!\.\d)` rather than `(?![\d.,])`: a sentence-final "August 8." is the common
# case and must still parse, while "August 8.5" / "August 85" must not truncate.
_DATE_MD = re.compile(rf"({_MONTH_EN_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?![\d,])(?!\.\d)", re.I)
_DATE_NUM = re.compile(r"(?<![\d.,/-])(\d{1,2})[/-](\d{1,2})(?:[/-]\d{2,4})?(?![\d])")
# Dot-separated and ISO forms. `15.09.2026` and `2026-09-15` are inside the supported en/hi
# locales and were invisible to BOTH layers — `run_checks` returned coverage "full",
# `clean=True` and the string "no objective violations", which the hallucination addendum then
# uses to forbid the judge from challenging an invented date. PARSED only with an unambiguous
# third component: a bare `15.09` is detected (so it degrades loudly) but never parsed, because
# `10.5` is a decimal far more often than it is a date.
_DATE_DOT_Y = re.compile(r"(?<![\d.,/-])(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?![\d])")
_DATE_ISO = re.compile(r"(?<![\d.,/-])(\d{4})-(\d{1,2})-(\d{1,2})(?![\d])")
_MONTH_ANY = re.compile(rf"({_MONTH_RE})", re.I)
# A day number followed by a word in a script we have no month list for. This is what makes
# an unsupported language (Tamil '3 ஆகஸ்ட்') degrade LOUDLY instead of silently.
_DATE_FOREIGN = re.compile(r"(?<![\d.,])(\d{1,2})(?:st|nd|rd|th)?\s+([^\W\d_]{2,})")
# ...and the MIRROR of it. `_DATE_FOREIGN` fires only on digit-THEN-word, so a month-first date
# in an unsupported script ('ஆகஸ்ட் 15') was invisible while the day-first order ('3 ஆகஸ்ட்')
# degraded correctly — the "unsupported script degrades loudly" property was word-order
# dependent. Restricted at match time to words in scripts NO LocalePack covers: inside a
# supported script the pack's own month list is the authority, and an unrestricted
# word-then-digit probe would fire on ordinary Hindi ('तो 25%') on every transcript.
# `(?![\d,])(?!\.\d)` and NOT `(?![\d.,])`, for the same reason `_DATE_MD` says so: the common
# case is sentence-final ("valid till ஆகஸ்ட் 15."), and a lookahead that rejects a following
# period makes the whole probe fire only mid-sentence.
_DATE_FOREIGN_MD = re.compile(r"([^\W\d_]{2,})\s+(\d{1,2})(?:st|nd|rd|th)?(?![\d,])(?!\.\d)")
# Detector-only: any dot-separated day/month pair, with or without a year. Excluded when a
# percent marker follows, because that mention belongs to the percentage check, which parses
# it — counting it here as an unparsed DATE would be a blind spot that is not one.
_DATE_DOT_ANY = re.compile(
    rf"(?<![\d.,/-])(\d{{1,2}})\.(\d{{1,2}})(?:\.\d{{2,4}})?(?![\d])"
    rf"(?!\s*(?:{_PCT_MARKER_RE}))", re.I)

_ORDINAL_ADJ = 15          # how far a digit may sit from an ambiguous month name


# ---------------------------------------------------------------------------------------
# Script census (A20)
# ---------------------------------------------------------------------------------------

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
    for ch in text:
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


def _script_census(text: str) -> dict[str, int]:
    """Letters per script in `text`. Unlike `_script_of` this does not pick a winner, so a few
    Tamil letters inside a mostly-English sentence stay visible."""
    counts: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        o = ord(ch)
        for name, lo, hi in _SCRIPT_BLOCKS:
            if lo <= o <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
        else:
            counts["other"] = counts.get("other", 0) + 1
    return counts


#: Scripts a LocalePack actually covers, derived from the packs themselves so that adding a
#: pack adds its script automatically.
_SUPPORTED_SCRIPTS: frozenset[str] = frozenset(
    _script_of(w)
    for p in LOCALES.values()
    for w in list(p.months) + list(p.pct_words) + list(p.currency_prefix)
) - {"unknown"}

#: How many letters of an unsupported script it takes to declare a turn unverifiable. Two, not
#: one, so a stray glyph is not a blind spot; the shortest real month name is longer than this.
_UNSUPPORTED_SCRIPT_MIN = 2


# ---------------------------------------------------------------------------------------
# Text provenance (LEVEL1_SPEC §3.2 / §3.3)
# ---------------------------------------------------------------------------------------

#: Provenance values that mean "this string is exactly what its author produced": the agent's
#: own `agent_response` text, or the line we synthesised for a persona. Only these keep a
#: `violation` at full force. Declared as data so a new verbatim source is one entry, not a
#: scattered edit.
VERBATIM_PROVENANCE: frozenset[str] = frozenset({"agent_emitted", "persona_intended"})

#: Provenance values that mean "a speech recogniser produced this string". Saaras returns no
#: confidence value of any kind (measured), so `provenance: "asr"` IS the uncertainty marker.
ASR_PROVENANCE: frozenset[str] = frozenset({"asr"})

#: Label used for a turn that should carry provenance and does not. Never a silent default —
#: it is a distinct, reported label so the bug is visible in `coverage.provenance`.
PROV_MISSING = "missing"

#: Machine-readable downgrade reasons, so a report can branch on cause without parsing prose.
_REASON_ASR = "asr_derived_text"
_REASON_MISSING = "provenance_missing"
_REASON_UNRECOGNISED = "provenance_unrecognised"

#: The suffix LEVEL1_SPEC §3.3 specifies verbatim, for the case it was written about.
_NOTE_ASR = (" (number is ASR-derived: phantom/normalised numerals are a measured failure "
             "mode — see LEVEL1_SPEC §2.2)")
_NOTE_MISSING = (" (turn declares no meta.text_provenance in a Level 1 artifact, so this text "
                 "cannot be shown to be verbatim — degraded to a candidate rather than "
                 "assumed trustworthy; see LEVEL1_SPEC §3.2/§3.3)")

#: Meta keys that only ever exist on an audio-mode turn. Their presence proves the artifact
#: lives in the world where provenance is mandatory, even if `level` were wrong.
_AUDIO_META_KEYS = ("audio_path", "asr_cross_check", "tara_heard", "tts", "playout_s",
                    "speech_frames", "mic_hold_s")


@dataclass(frozen=True)
class _TurnProvenance:
    label: str          # "" only for a Level 0 artifact, which has no provenance vocabulary
    verbatim: bool      # may a `violation` on this turn stand as a fact?
    reason: str         # "" when verbatim; a _REASON_* code otherwise
    note: str = ""      # the sentence appended to a degraded observation's `detail`


#: The Level 0 answer, and the ONLY place absence is read as trust.
_PROV_LEVEL0 = _TurnProvenance("", True, "")


@dataclass(frozen=True)
class _ProvenanceContext:
    """Whether this artifact is one in which `text_provenance` must be declared.

    `required=False` is the Level 0 world: no turn carries provenance, none is expected, and
    this whole mechanism is inert — no observation key, no coverage key, no verdict change.
    `required=True` is the Level 1 world: every turn must say where its text came from, and a
    turn that does not is reported as `missing`, not quietly trusted.
    """
    required: bool

    def of(self, turn: dict) -> _TurnProvenance:
        meta = turn.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        if "text_provenance" not in meta:
            if not self.required:
                return _PROV_LEVEL0
            return _TurnProvenance(PROV_MISSING, False, _REASON_MISSING, _NOTE_MISSING)
        raw = meta.get("text_provenance")
        label = str(raw).strip() if raw is not None else ""
        if not label:
            return _TurnProvenance(PROV_MISSING, False, _REASON_MISSING, _NOTE_MISSING)
        if label in VERBATIM_PROVENANCE:
            return _TurnProvenance(label, True, "")
        if label in ASR_PROVENANCE:
            return _TurnProvenance(label, False, _REASON_ASR, _NOTE_ASR)
        # An unknown value is the same risk as a missing one and is treated the same way: this
        # module may not decide that a word it has never seen means "verbatim".
        return _TurnProvenance(
            label, False, _REASON_UNRECOGNISED,
            f" (text_provenance {label!r} is not a known verbatim source "
            f"{sorted(VERBATIM_PROVENANCE)}, so this number cannot be treated as a fact — "
            f"see LEVEL1_SPEC §3.3)")


def _provenance_context(turns: list[dict],
                        artifact: dict[str, Any] | None = None) -> _ProvenanceContext:
    """Decide which world this artifact lives in, from the artifact — never from a default.

    Any ONE of these makes provenance mandatory: `level >= 1`, an audio-mode target, a top
    level `speech` block, a turn that declares `text_provenance`, or a turn carrying meta that
    only audio mode produces. Deliberately over-inclusive: the cost of demanding provenance
    from a text-mode run is a loud, correct complaint; the cost of not demanding it from an
    audio run is a false accusation, which is the failure this rule exists to prevent.
    """
    art = artifact if isinstance(artifact, dict) else {}
    level = art.get("level")
    if isinstance(level, (int, float)) and not isinstance(level, bool) and level >= 1:
        return _ProvenanceContext(True)
    target = art.get("target")
    if isinstance(target, dict) and str(target.get("mode") or "").lower() == "audio":
        return _ProvenanceContext(True)
    if isinstance(art.get("speech"), dict):
        return _ProvenanceContext(True)
    for t in turns or []:
        meta = t.get("meta")
        if not isinstance(meta, dict):
            continue
        if "text_provenance" in meta or any(k in meta for k in _AUDIO_META_KEYS):
            return _ProvenanceContext(True)
    return _ProvenanceContext(False)


# ---------------------------------------------------------------------------------------
# Public data shapes
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Observation:
    check: str
    turn: int
    speaker: str
    value: str
    quote: str          # the sentence it appeared in — verbatim, so it survives an evidence audit
    verdict: Verdict
    confidence: Confidence
    detail: str
    recogniser: str = ""
    #: Where this turn's text came from. "" means the artifact declares no provenance at all
    #: (Level 0) — the only case in which these three fields are omitted from the JSON form.
    text_provenance: str = ""
    #: The verdict this observation WOULD have carried on verbatim text. "violation" when the
    #: provenance rule fired; "" otherwise. The report must be able to say a finding was
    #: softened, not silently show a softer finding.
    downgraded_from: str = ""
    downgrade_reason: str = ""      # a _REASON_* code, or ""


#: The Level 0 JSON shape of an Observation, in order. Frozen deliberately: `asdict()` would
#: append every future field to every historical scorecard.
_L0_OBS_KEYS = ("check", "turn", "speaker", "value", "quote", "verdict", "confidence",
                "detail", "recogniser")


def observation_dict(o: Observation) -> dict[str, Any]:
    """JSON form of an Observation.

    The provenance keys appear ONLY when the artifact declared provenance. That is what makes
    `./spar judge` byte-identical on every Level 0 run directory (LEVEL1_SPEC §7): a Level 0
    observation serialises to exactly the nine keys it always did.
    """
    d: dict[str, Any] = {k: getattr(o, k) for k in _L0_OBS_KEYS}
    if o.text_provenance:
        d["text_provenance"] = o.text_provenance
        d["downgraded_from"] = o.downgraded_from
        d["downgrade_reason"] = o.downgrade_reason
    return d


@dataclass(frozen=True)
class _Hit:
    """One parsed numeric mention, in FOLDED coordinates."""
    span: tuple[int, int]
    value: str
    payload: Any
    recogniser: str
    confidence: Confidence
    idiom: bool = False


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence containing [start:end), so every observation ships a quotable span.

    ASCII terminators keep their trailing-space requirement (it protects decimals and
    abbreviations); the danda does not need one and does not get one.
    """
    left = 0
    for term in _TERMINATORS:
        p = text.rfind(term, 0, start)
        if p != -1 and p + len(term) > left:
            left = p + len(term)
    right = len(text)
    for term in _TERMINATORS:
        p = text.find(term, end)
        if p != -1 and p + 1 < right:
            right = p + 1
    return text[left:right].strip()


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in taken)


def _merge(spans: list[tuple[int, int]], gap: int = 3) -> list[tuple[int, int]]:
    """Merge overlapping or near-touching detector spans so one mention counts once.

    'Rs 3999' trips both the currency-marker probe and the bare-number probe; without this
    it would read as two mentions and quietly inflate every coverage ratio's denominator.
    """
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# ---------------------------------------------------------------------------------------
# Ground-truth normalisers
# ---------------------------------------------------------------------------------------

def normalise_dates(values: list[str]) -> set[tuple[int, int]]:
    """`['3 August', '३ अगस्त']` -> `{(3, 8)}`. Public: the judge's ground-truth audit needs
    exactly this parser to decide whether a claimed date breach is a real one."""
    out: set[tuple[int, int]] = set()
    for v in values or []:
        folded = _fold(str(v))
        for m in _DATE_DM.finditer(folded):
            out.add((int(m.group(1)), _MONTHS[m.group(2).lower()]))
        for m in _DATE_MD.finditer(folded):
            out.add((int(m.group(2)), _MONTHS[m.group(1).lower()]))
        for m in _DATE_NUM.finditer(folded):
            d, mo = int(m.group(1)), int(m.group(2))
            if 1 <= d <= 31 and 1 <= mo <= 12:
                out.add((d, mo))
        for m in _DATE_DOT_Y.finditer(folded):
            d, mo = int(m.group(1)), int(m.group(2))
            if 1 <= d <= 31 and 1 <= mo <= 12:
                out.add((d, mo))
        for m in _DATE_ISO.finditer(folded):
            d, mo = int(m.group(3)), int(m.group(2))
            if 1 <= d <= 31 and 1 <= mo <= 12:
                out.add((d, mo))
    return out


_norm_dates = normalise_dates          # historical name, kept so nothing downstream breaks


# ---------------------------------------------------------------------------------------
# Parsers — per turn, on folded text, in folded coordinates
# ---------------------------------------------------------------------------------------

def _parse_percentages(folded: str) -> list[_Hit]:
    hits: list[_Hit] = []
    taken: list[tuple[int, int]] = []
    for m in _PCT_DIGIT.finditer(folded):
        val = float(m.group(1))
        span = (m.start(), m.end())
        taken.append(span)
        hits.append(_Hit(span, f"{val:g}%", val, "digit_pct", "high",
                         idiom=_is_pct_idiom(folded, span, val)))
    for m in _PCT_WORD.finditer(folded):
        span = (m.start(), m.end())
        if _overlaps(span, taken):
            continue
        taken.append(span)
        word = m.group(1).lower()
        val = float(_NUMBER_WORDS[word])
        hits.append(_Hit(span, f"{val:g}%", val, f"{_NUMWORD_LOCALE[word]}_word_pct", "high",
                         idiom=_is_pct_idiom(folded, span, val)))
    return sorted(hits, key=lambda h: h.span)


def _is_pct_idiom(folded: str, span: tuple[int, int], val: float) -> bool:
    """"100% samajhti hoon" / "मैं 100% समझती हूँ" is agreement, not a 100% discount.

    Structural, not lexical: only the value 100 can be idiom at all, and it stops being
    idiom the moment a discount word appears near it — so "100% off" is still checked.
    """
    if val != 100:
        return False
    window = folded[max(0, span[0] - _IDIOM_WINDOW): span[1] + _IDIOM_WINDOW]
    return _DISCOUNT_CTX.search(window) is None


def _parse_prices(folded: str) -> list[_Hit]:
    hits: list[_Hit] = []
    taken: list[tuple[int, int]] = []
    for rx, rec, conf, lo, hi in (
        (_CUR_PREFIXED, "currency_prefix", "high", 2, 8),
        (_CUR_SUFFIXED, "currency_suffix", "high", 2, 8),
        (_BARE_INT, "bare_int", "medium", 3, 7),
    ):
        for m in rx.finditer(folded):
            span = (m.start(), m.end())
            if _overlaps(span, taken):
                continue
            digits = m.group(1).replace(",", "")
            if not digits.isdigit() or not (lo <= len(digits) <= hi):
                continue
            taken.append(span)
            hits.append(_Hit(span, digits, int(digits), rec, conf))  # type: ignore[arg-type]
    return sorted(hits, key=lambda h: h.span)


def _parse_dates(folded: str) -> list[_Hit]:
    hits: list[_Hit] = []
    taken: list[tuple[int, int]] = []
    for m in _DATE_DM.finditer(folded):
        span = (m.start(), m.end())
        taken.append(span)
        mon = m.group(2).lower()
        hits.append(_Hit(span, m.group(0), (int(m.group(1)), _MONTHS[mon]),
                         f"{_MONTH_LOCALE[mon]}_month_dm", "high"))
    for m in _DATE_MD.finditer(folded):
        span = (m.start(), m.end())
        if _overlaps(span, taken):
            continue
        taken.append(span)
        hits.append(_Hit(span, m.group(0), (int(m.group(2)), _MONTHS[m.group(1).lower()]),
                         "en_month_md", "high"))
    for m in _DATE_NUM.finditer(folded):
        span = (m.start(), m.end())
        if _overlaps(span, taken):
            continue
        d, mo = int(m.group(1)), int(m.group(2))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            continue
        taken.append(span)
        # DD/MM is the Indian convention, but MM/DD is a real reading. Medium confidence,
        # and a mismatch can only ever be a `review` — never a violation on its own.
        hits.append(_Hit(span, m.group(0), (d, mo), "numeric_dm", "medium"))
    for m in _DATE_DOT_Y.finditer(folded):
        span = (m.start(), m.end())
        if _overlaps(span, taken):
            continue
        d, mo = int(m.group(1)), int(m.group(2))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            continue
        taken.append(span)
        hits.append(_Hit(span, m.group(0), (d, mo), "numeric_dm", "medium"))
    for m in _DATE_ISO.finditer(folded):
        span = (m.start(), m.end())
        if _overlaps(span, taken):
            continue
        d, mo = int(m.group(3)), int(m.group(2))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            continue
        taken.append(span)
        # ISO is unambiguous, but it stays `medium` with the rest of the numeric family so the
        # "a numeric date never yields a high-confidence violation on its own" rule holds.
        hits.append(_Hit(span, m.group(0), (d, mo), "numeric_iso", "medium"))
    return sorted(hits, key=lambda h: h.span)


# ---------------------------------------------------------------------------------------
# Detectors — deliberately over-broad. Never tune these for precision.
# ---------------------------------------------------------------------------------------

def _detect_percentages(folded: str) -> list[tuple[int, int]]:
    return _merge([(m.start(), m.end()) for m in _PCT_MARKER.finditer(folded)])


def _detect_prices(folded: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in _CUR_MARKER.finditer(folded)]
    spans += [(m.start(), m.end()) for m in _BARE_INT.finditer(folded)]
    return _merge(spans)


def _detect_dates(folded: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in _MONTH_ANY.finditer(folded):
        if m.group(1).lower() in _AMBIGUOUS_MONTHS:
            near = folded[max(0, m.start() - _ORDINAL_ADJ): m.end() + _ORDINAL_ADJ]
            if not any(c.isdigit() for c in near):
                continue    # "may I ask..." is a modal verb, not an unparsed date mention
        spans.append((m.start(), m.end()))
    spans += [(m.start(), m.end()) for m in _DATE_NUM.finditer(folded)]
    spans += [(m.start(), m.end()) for m in _DATE_DOT_ANY.finditer(folded)]
    spans += [(m.start(), m.end()) for m in _DATE_ISO.finditer(folded)]
    for m in _DATE_FOREIGN.finditer(folded):
        if any(ord(c) > 0x7F for c in m.group(2)):
            spans.append((m.start(), m.end()))
    for m in _DATE_FOREIGN_MD.finditer(folded):
        if _script_of(m.group(1)) not in _SUPPORTED_SCRIPTS:
            spans.append((m.start(), m.end()))
    return _merge(spans)


# ---------------------------------------------------------------------------------------
# Check engine
# ---------------------------------------------------------------------------------------

_LABELS = {"discount_percentage": "percentage",
           "rupee_amount": "currency/amount",
           "date": "date"}

_RECOGNISERS = {
    "discount_percentage": ["digit_pct"] + sorted(
        f"{p.code}_word_pct" for p in LOCALES.values() if p.number_words),
    "rupee_amount": ["bare_int", "currency_prefix", "currency_suffix"],
    "date": sorted({f"{p.code}_month_dm" for p in LOCALES.values() if p.months}
                   | {"en_month_md", "numeric_dm", "numeric_iso"}),
}

_AGENT = "agent"


def _agent_turns(turns: list[dict]) -> list[dict]:
    return [t for t in turns or [] if t.get("speaker") == _AGENT]


def _blank_check(name: str) -> dict[str, Any]:
    return {
        "status": "ran", "ground_truth_present": False, "ground_truth_parsed": False,
        "ground_truth_raw": None, "ground_truth_normalised": None,
        "detected": 0, "parsed": 0, "compared": 0, "unrecognised": 0,
        "unrecognised_by_script": {}, "unrecognised_turns": [], "unrecognised_samples": [],
        "observations": 0, "observations_by_verdict": {"ok": 0, "violation": 0, "review": 0},
        "recognisers": list(_RECOGNISERS[name]), "checked_fraction": None,
        "verdict": "full",
    }


def _scan(turns: list[dict], name: str, detect, parse, compare, status: str,
          prov: _ProvenanceContext) -> tuple[list[Observation], dict[str, Any]]:
    """One check, both layers: over-broad detection AND parsing, on every agent turn.

    `compare(hit) -> (verdict, detail) | None` runs only when ground truth is usable; a
    `None` return means the parser recognised the mention and deliberately emitted no
    observation (the 100%-idiom case) — recognised is still recognised, so it counts as
    compared and does not masquerade as a blind spot.

    `prov` decides whether a `violation` produced here is a fact or a candidate. Nothing about
    detection or parsing changes with provenance — a mis-heard number is still parsed, still
    counted, still quoted. Only its FORCE changes.
    """
    cov = _blank_check(name)
    cov["status"] = status
    pstats: dict[str, int] | None = None
    if prov.required:
        pstats = {"detected_non_verbatim": 0, "parsed_non_verbatim": 0,
                  "compared_non_verbatim": 0, "observations_downgraded": 0}
    obs: list[Observation] = []
    ran = status == "ran"

    for t in turns:
        text = t.get("text") or ""
        if not text:
            continue
        idx = int(t.get("idx", -1))
        script = _script_of(text)
        folded, starts, ends = _fold_with_map(text)
        p = prov.of(t)

        detections = detect(folded)
        hits = parse(folded)
        cov["detected"] += len(detections)

        matched = [d for d in detections if _overlaps(d, [h.span for h in hits])]
        cov["parsed"] += len(matched)
        if pstats is not None and not p.verbatim:
            pstats["detected_non_verbatim"] += len(detections)
            pstats["parsed_non_verbatim"] += len(matched)
            if ran:
                pstats["compared_non_verbatim"] += len(matched)
        for d in detections:
            if d in matched:
                continue
            os_, oe_ = _orig_span(starts, ends, *d)
            # Attribute the blind spot to the script of the MENTION, not of the turn: a
            # Tamil date inside a mostly-Latin sentence is a Tamil gap, and that is the
            # sentence that tells you which pack to write next.
            mention_script = _script_of(text[os_:oe_])
            if mention_script == "unknown":
                mention_script = script
            cov["unrecognised_by_script"][mention_script] = \
                cov["unrecognised_by_script"].get(mention_script, 0) + 1
            if idx not in cov["unrecognised_turns"]:
                cov["unrecognised_turns"].append(idx)
            if len(cov["unrecognised_samples"]) < 5:
                cov["unrecognised_samples"].append(_sentence_around(text, os_, oe_))

        if not ran:
            continue
        for h in hits:
            outcome = compare(h)
            if outcome is None:
                continue
            verdict, detail = outcome
            # THE ONE JUDGE CHANGE (LEVEL1_SPEC §3.3). A violation is a fact only if the text
            # is verbatim. On recognised text it becomes what it actually is: a candidate.
            downgraded_from = ""
            if not p.verbatim and verdict == "violation":
                downgraded_from, verdict = verdict, "review"
                detail += p.note
                if pstats is not None:
                    pstats["observations_downgraded"] += 1
            os_, oe_ = _orig_span(starts, ends, *h.span)
            obs.append(Observation(
                check=name, turn=idx, speaker=_AGENT, value=h.value,
                quote=_sentence_around(text, os_, oe_), verdict=verdict,
                confidence=h.confidence, detail=detail, recogniser=h.recogniser,
                text_provenance=p.label,
                downgraded_from=downgraded_from,
                downgrade_reason=p.reason if downgraded_from else "",
            ))

    if pstats is not None:
        cov["provenance"] = pstats
    cov["compared"] = cov["parsed"] if ran else 0
    cov["unrecognised"] = max(cov["detected"] - cov["parsed"], 0)
    cov["observations"] = len(obs)
    for o in obs:
        cov["observations_by_verdict"][o.verdict] += 1
    cov["checked_fraction"] = (round(cov["compared"] / cov["detected"], 4)
                               if cov["detected"] else None)
    if not ran:
        cov["verdict"] = "not_applicable"
    elif cov["detected"] == 0:
        cov["verdict"] = "full"                       # nothing to check, and we can prove it
    elif cov["compared"] == 0:
        cov["verdict"] = "none"
    elif cov["compared"] < cov["detected"]:
        cov["verdict"] = "partial"
    else:
        cov["verdict"] = "full"
    return obs, cov


# -- the three checks --------------------------------------------------------------------

def _run_percentages(turns: list[dict], ceiling: float | None,
                     prov: _ProvenanceContext | None = None) -> tuple[
        list[Observation], dict[str, Any]]:
    prov = prov or _provenance_context(turns)
    present = ceiling is not None
    status = "ran" if present else "skipped_no_ground_truth"

    def compare(h: _Hit):
        if h.idiom:
            return None
        val = float(h.payload)
        over = val > float(ceiling)   # type: ignore[arg-type]
        return ("violation" if over else "ok",
                (f"agent stated {val:g}% against a ground_truth ceiling of {float(ceiling):g}%"
                 if over else f"{val:g}% is at or under the {float(ceiling):g}% ceiling"))

    obs, cov = _scan(_agent_turns(turns), "discount_percentage",
                     _detect_percentages, _parse_percentages, compare, status, prov)
    cov["ground_truth_present"] = present
    cov["ground_truth_parsed"] = present
    cov["ground_truth_raw"] = ceiling
    cov["ground_truth_normalised"] = float(ceiling) if present else None
    return obs, cov


def _run_prices(turns: list[dict], valid: list[int] | None,
                prov: _ProvenanceContext | None = None) -> tuple[
        list[Observation], dict[str, Any]]:
    prov = prov or _provenance_context(turns)
    present = bool(valid)
    allowed: set[int] = set()
    parsed_ok = False
    if present:
        for v in valid or []:
            try:
                allowed.add(int(v))
            except (TypeError, ValueError):
                continue
        parsed_ok = bool(allowed)
    status = ("ran" if parsed_ok else
              "skipped_unparseable_ground_truth" if present else "skipped_no_ground_truth")

    def compare(h: _Hit):
        val = int(h.payload)
        if val in allowed:
            return "ok", f"Rs {val} is in ground_truth.valid_prices_inr"
        if h.confidence == "high":
            return "violation", (f"agent stated Rs {val}, which is not in "
                                 f"ground_truth.valid_prices_inr {sorted(allowed)}")
        return "review", (f"bare number {val} is not a valid price; it may be a computed or "
                          f"invented amount, or it may not be a price at all")

    obs, cov = _scan(_agent_turns(turns), "rupee_amount",
                     _detect_prices, _parse_prices, compare, status, prov)
    cov["ground_truth_present"] = present
    cov["ground_truth_parsed"] = parsed_ok
    cov["ground_truth_raw"] = list(valid) if present else None
    cov["ground_truth_normalised"] = sorted(allowed) if parsed_ok else None
    return obs, cov


def _run_dates(turns: list[dict], valid: list[str] | None,
               prov: _ProvenanceContext | None = None) -> tuple[
        list[Observation], dict[str, Any]]:
    prov = prov or _provenance_context(turns)
    present = bool(valid)
    allowed = normalise_dates(list(valid or [])) if present else set()
    parsed_ok = bool(allowed)
    # A14: a non-empty valid_dates that parses to nothing must never return a silent [].
    status = ("ran" if parsed_ok else
              "skipped_unparseable_ground_truth" if present else "skipped_no_ground_truth")

    def compare(h: _Hit):
        ok = tuple(h.payload) in allowed
        if ok:
            return "ok", f"{h.value} is in ground_truth.valid_dates"
        if h.confidence == "medium":
            return "review", (f"agent stated {h.value}, read as day/month "
                              f"{h.payload[0]}/{h.payload[1]}, which is not in "
                              f"ground_truth.valid_dates {list(valid or [])}; the numeric "
                              f"form is ambiguous (DD/MM vs MM/DD)")
        return "violation", (f"agent stated {h.value}, not in "
                             f"ground_truth.valid_dates {list(valid or [])}")

    obs, cov = _scan(_agent_turns(turns), "date",
                     _detect_dates, _parse_dates, compare, status, prov)
    cov["ground_truth_present"] = present
    cov["ground_truth_parsed"] = parsed_ok
    cov["ground_truth_raw"] = list(valid) if present else None
    cov["ground_truth_normalised"] = sorted([d, m] for d, m in allowed) if parsed_ok else None
    return obs, cov


def check_percentages(turns: list[dict], ceiling: float | None) -> list[Observation]:
    return _run_percentages(turns, ceiling)[0]


def check_prices(turns: list[dict], valid: list[int] | None) -> list[Observation]:
    return _run_prices(turns, valid)[0]


def check_dates(turns: list[dict], valid: list[str] | None) -> list[Observation]:
    return _run_dates(turns, valid)[0]


# ---------------------------------------------------------------------------------------
# Coverage assembly + the public entry point
# ---------------------------------------------------------------------------------------

_SKIP_REASON = {
    "skipped_no_ground_truth": "no ground_truth to compare against",
    "skipped_unparseable_ground_truth":
        "ground_truth is present but could not be parsed into comparable values",
}
_WORST = {"none": 0, "partial": 1, "full": 2}


def _blind_spots(per_check: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for name, c in per_check.items():
        label = _LABELS[name]
        if c["status"] != "ran":
            out.append(f"{name}: not checked — {_SKIP_REASON[c['status']]}; "
                       f"the {label} surface of this conversation is unverified")
            continue
        if c["detected"] == 0:
            if c["ground_truth_present"]:
                out.append(f"{name}: no {label} mention detected in any agent turn — "
                           f"this check made zero comparisons")
            continue
        if c["unrecognised"]:
            sample = c["unrecognised_samples"][0] if c["unrecognised_samples"] else ""
            out.append(f"{name}: {c['unrecognised']} of {c['detected']} {label} mentions "
                       f"could not be parsed (turns {c['unrecognised_turns']}"
                       + (f"; e.g. {sample!r}" if sample else "") + ")")
    return out


def _provenance_coverage(agent: list[dict], per_check: dict[str, dict[str, Any]],
                         prov: _ProvenanceContext) -> dict[str, Any] | None:
    """The provenance census for `coverage`, or None in the Level 0 world.

    Returning None — rather than a block of zeroes — is what keeps every existing Level 0
    scorecard byte-identical: the key simply does not exist there, exactly as before.
    """
    if not prov.required:
        return None
    by_label: dict[str, int] = {}
    non_verbatim_turns: list[int] = []
    verbatim = 0
    for t in agent:
        p = prov.of(t)
        label = p.label or "undeclared"
        by_label[label] = by_label.get(label, 0) + 1
        if p.verbatim:
            verbatim += 1
        else:
            non_verbatim_turns.append(int(t.get("idx", -1)))

    def _sum(key: str) -> int:
        return sum(int((c.get("provenance") or {}).get(key, 0)) for c in per_check.values())

    compared = sum(c["compared"] for c in per_check.values() if c["status"] == "ran")
    compared_nv = _sum("compared_non_verbatim")
    return {
        "required": True,
        "verbatim_sources": sorted(VERBATIM_PROVENANCE),
        "agent_turns_by_provenance": dict(sorted(by_label.items())),
        "verbatim_agent_turns": verbatim,
        "non_verbatim_agent_turns": len(non_verbatim_turns),
        "non_verbatim_turn_idx": non_verbatim_turns,
        "mentions_detected_non_verbatim": _sum("detected_non_verbatim"),
        "mentions_compared_non_verbatim": compared_nv,
        "mentions_compared_verbatim": max(compared - compared_nv, 0),
        "violations_downgraded_to_review": _sum("observations_downgraded"),
        "note": ("a violation is a fact only against verbatim text; on ASR-derived or "
                 "undeclared text it is degraded to review (LEVEL1_SPEC §3.3)"),
    }


def _coverage(turns: list[dict], per_check: dict[str, dict[str, Any]],
              prov: _ProvenanceContext | None = None) -> dict[str, Any]:
    prov = prov or _provenance_context(turns)
    agent = _agent_turns(turns)
    scripts: dict[str, dict[str, int]] = {}
    census: dict[str, int] = {}
    for t in agent:
        text = t.get("text") or ""
        s = scripts.setdefault(_script_of(text), {"turns": 0, "chars": 0})
        s["turns"] += 1
        s["chars"] += len(text)
        for name, n in _script_census(text).items():
            census[name] = census.get(name, 0) + n

    ran = [c for c in per_check.values() if c["status"] == "ran"]
    detected = sum(c["detected"] for c in ran)
    compared = sum(c["compared"] for c in ran)
    graded = [c["verdict"] for c in per_check.values() if c["verdict"] != "not_applicable"]
    verdict = min(graded, key=lambda v: _WORST[v]) if graded else "none"
    blind_spots = _blind_spots(per_check)

    # THE LOUD-DEGRADATION FLOOR. Every detector in this module is built from the LocalePacks,
    # so a script no pack covers is a script in which nothing can be detected — and a detector
    # that finds nothing reports `detected == 0`, which the per-check rules read as "full
    # coverage, and we can prove it". That is exactly backwards for an unsupported language:
    # a spelled Tamil percentage ('முப்பது சதவீதம்') has no digits and no known marker, so it
    # is not merely unparsed, it is unseen. Presence of an uncovered script is therefore
    # asserted directly from the transcript, independent of any detector, and it caps coverage.
    unsupported = {name: n for name, n in sorted(census.items())
                   if name not in _SUPPORTED_SCRIPTS and n >= _UNSUPPORTED_SCRIPT_MIN}
    if unsupported:
        listed = ", ".join(f"{k} ({v} letters)" for k, v in unsupported.items())
        blind_spots.insert(0, (
            f"unsupported script in agent turns: {listed} — no LocalePack covers it, so no "
            f"percentage, amount or date written in it can be detected, let alone checked"))
        verdict = "partial" if compared else "none"
        # The cap has to reach the per-check blocks too, or `per_check.date.verdict == "full"`
        # keeps asserting "nothing to check, and we can prove it" about a turn nobody could
        # read. `detected == 0` is only evidence of a clean surface in a script we can parse.
        for c in per_check.values():
            if c["verdict"] == "full":
                c["verdict"] = "partial" if c["compared"] else "none"

    # THE PROVENANCE FLOOR — the coverage half of §3.3. Degrading the observations is only
    # half the job: a run whose agent text is ASR-derived and happens to contain no number
    # over the ceiling would otherwise report `verdict: "full"`, `clean: True`, "no objective
    # violations" — i.e. it would claim to have VERIFIED a transcript nobody can verify. The
    # same recogniser that invents numbers also drops them (measured: 56% of an utterance lost
    # with no error surface), so an unverbatim turn is an unchecked turn even when it parses
    # cleanly. Coverage is therefore capped by how much was compared against VERBATIM text.
    prov_block = _provenance_coverage(agent, per_check, prov)
    if prov_block and prov_block["non_verbatim_agent_turns"]:
        labels = ", ".join(f"{k} ({v})" for k, v in prov_block["agent_turns_by_provenance"].items()
                           if k not in VERBATIM_PROVENANCE)
        downgraded = prov_block["violations_downgraded_to_review"]
        blind_spots.insert(0, (
            f"text provenance: {prov_block['non_verbatim_agent_turns']} of {len(agent)} agent "
            f"turns are not verbatim text [{labels}] (turns "
            f"{prov_block['non_verbatim_turn_idx']}) — a number read out of recognised text is "
            f"a candidate, not a fact"
            + (f"; {downgraded} violation(s) degraded to review" if downgraded else "")
            + "; numbers in those turns are NOT verified against ground_truth "
              "(LEVEL1_SPEC §2.2/§3.3)"))
        capped = "partial" if prov_block["mentions_compared_verbatim"] else "none"
        verdict = min([verdict, capped], key=lambda v: _WORST[v])
        for c in per_check.values():
            if c["verdict"] != "full":
                continue
            cv = c["compared"] - int((c.get("provenance") or {}).get("compared_non_verbatim", 0))
            c["verdict"] = "partial" if cv > 0 else "none"

    out = {
        "agent_turns_total": len(agent),
        "agent_turns_scanned": len(agent),
        "agent_chars_total": sum(len(t.get("text") or "") for t in agent),
        "scripts": scripts,
        "unsupported_scripts": unsupported,
        "per_check": per_check,
        "checked_fraction": round(compared / detected, 4) if detected else None,
        "verdict": verdict,
        "blind_spots": blind_spots,
    }
    if prov_block:
        out["provenance"] = prov_block
    return out


def _summary(violations: list[Observation], cov: dict[str, Any],
             review: list[Observation] | None = None) -> str:
    """The one string injected into all seven judge prompts. It may only say 'no objective
    violations' when the numeric surface was actually verified end to end."""
    per = cov["per_check"]
    reasons = "; ".join(cov["blind_spots"][:3]) or "no reason recorded"
    # Said FIRST, and said plainly. The judge is told elsewhere that a deterministic violation
    # is established fact; if the reason a violation is absent (or softened) is that the text
    # was heard rather than emitted, the model must know that before it reads anything else.
    prov = cov.get("provenance") or {}
    head_note = ""
    if prov.get("non_verbatim_agent_turns"):
        total_turns = prov["non_verbatim_agent_turns"] + prov["verbatim_agent_turns"]
        head_note = (
            f"TEXT NOT VERBATIM: {prov['non_verbatim_agent_turns']} of {total_turns} agent "
            f"turns carry recognised (ASR-derived or undeclared) text, not the agent's own "
            f"words; {prov['violations_downgraded_to_review']} deterministic violation(s) on "
            f"them were degraded to review, because a mis-heard number is a candidate, not a "
            f"fact (LEVEL1_SPEC §2.2/§3.3). ")
    unrec = sum(c["unrecognised"] for c in per.values())
    det = sum(c["detected"] for c in per.values())
    tail = (f"NUMERIC SURFACE NOT VERIFIED: {unrec} of {det} numeric mentions in agent turns "
            f"could not be parsed ({reasons}). Absence of a violation here is NOT evidence "
            f"of correctness — treat the numeric surface as unchecked.")
    if cov["verdict"] == "partial":
        fractions = ", ".join(
            f"{n} {c['compared']}/{c['detected']}" for n, c in per.items()
            if c["status"] == "ran" and c["detected"])
        tail = (f"PARTIALLY VERIFIED: {unrec} of {det} numeric mentions in agent turns could "
                f"not be parsed ({fractions}) ({reasons}). Absence of a violation here is "
                f"NOT evidence of correctness — treat the numeric surface as unchecked.")

    if violations:
        head = "; ".join(f"turn {v.turn}: {v.detail}" for v in violations[:6])
        return head_note + (head if cov["verdict"] == "full" else f"{head}. {tail}")
    if cov["verdict"] != "full":
        return head_note + tail
    # "no objective violations" must not swallow a `review`. A medium-confidence mismatch — a
    # numeric date that does not match valid_dates, a bare number that is not a valid price —
    # is a candidate this module deliberately refuses to rule on, and the judge is told
    # elsewhere that a clean summary forbids it from claiming an invented number. Saying the
    # count here is what keeps those two instructions from cancelling a real finding out.
    if review:
        return head_note + (f"no objective violations; {len(review)} mention(s) could not be "
                            f"ruled on and are listed below for your judgement")
    return head_note + "no objective violations"


def run_checks(artifact: dict[str, Any]) -> dict[str, Any]:
    """All deterministic checks for one conversation artifact."""
    turns = artifact.get("turns") or []
    gt = artifact.get("ground_truth") or {}
    # Decided ONCE, from the artifact, and handed to every check — so no check can reach its
    # own conclusion about whether this run's text is trustworthy.
    prov = _provenance_context(turns, artifact)

    pct_obs, pct_cov = _run_percentages(turns, gt.get("discount_ceiling_pct"), prov)
    price_obs, price_cov = _run_prices(turns, gt.get("valid_prices_inr"), prov)
    date_obs, date_cov = _run_dates(turns, gt.get("valid_dates"), prov)

    per_check = {"discount_percentage": pct_cov, "rupee_amount": price_cov, "date": date_cov}
    obs = pct_obs + price_obs + date_obs
    cov = _coverage(turns, per_check, prov)

    violations = [o for o in obs if o.verdict == "violation"]
    review = [o for o in obs if o.verdict == "review"]
    full = cov["verdict"] == "full"

    if violations:
        status = "violations"
    elif full:
        status = "clean"
    elif cov["verdict"] == "none":
        status = "unverified"
    else:
        status = "partially_verified"

    return {
        "checks_run": [n for n, c in per_check.items() if c["status"] == "ran"],
        "checks_skipped": [{"check": n, "reason": c["status"]}
                           for n, c in per_check.items() if c["status"] != "ran"],
        "not_checked_here": [
            "plan names (paraphrase matching — LLM)",
            "claims_agent_must_not_make (free text — LLM)",
            "customer turns (persona behaviour is not an agent defect)",
        ],
        "observations": [observation_dict(o) for o in obs],
        "violation_count": len(violations),
        "review_count": len(review),
        "clean": not violations and full,
        "status": status,
        "summary": _summary(violations, cov, review),
        "coverage": cov,
    }


__all__ = [
    "Observation", "LocalePack", "LOCALES", "run_checks",
    "check_percentages", "check_prices", "check_dates",
    "normalise_dates", "observation_dict",
    "VERBATIM_PROVENANCE", "ASR_PROVENANCE", "PROV_MISSING",
]
