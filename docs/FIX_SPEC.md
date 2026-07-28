# FIX_SPEC — judge repair, run 20260725-185028-f99e33

**Status: authoritative implementation spec.** Three implementers code against this in
parallel, on disjoint files, without talking to each other. A verifier falsifies every
acceptance claim afterwards. If your code disagrees with this document, this document wins —
raise the conflict, do not fix it locally.

Inputs this spec is derived from (do not re-derive, do re-read):

- `docs/CALIBRATION.md` — the bug report (its §2 is itself WRONG; correction specified in D3)
- Diagnosis A — evidence-audit probe, `runs/20260725-185028-f99e33/evidence_norm_probe.json`
- Diagnosis B — locale census of `judge/checks.py` against both runs on disk
- `docs/INTERFACES.md` §8.4, `docs/PREFLIGHT.md`, `personas/_SCHEMA.md`

---

## 0. Ground rules — binding on all three implementers

1. **Never** touch ElevenLabs' simulate-conversation endpoint or the live agent. Never call
   `./spar run`. Re-judging (`./spar judge 20260725-185028-f99e33`) is free and allowed.
2. §8.4 stands: the judge never sees the persona system prompt, `persona_stresses`, or
   `persona_is_control`. `ground_truth` and `scenario_vars` are ALREADY visible to the judge
   and remain so — the ground-truth audit in D4 uses only fields the judge already receives.
3. No score without evidence that is verbatim AND from the right speaker. Nothing in this
   spec may weaken that. Explicit regressions in D3 prove it.
4. Sarvam constraints unchanged: reasoning cannot be disabled; `max_tokens` 2000–4096 (4096
   is a hard 400, not degradation); `content:None` + `finish_reason:'length'` is retryable;
   `reasoning_content` never enters any artifact. The `_LADDER` in `judge/judge.py` is not
   to be modified.
5. `PYTHONPATH=. uv run --python 3.12 python scripts/smoke_loop_offline.py` must stay green.
   **Nobody edits `scripts/smoke_loop_offline.py`.** Each implementer ships their own new
   regression script (see §1) instead.
6. Do not write any report/summary `.md` beyond the file edits explicitly assigned below.
7. On-disk artifacts in `runs/` are fixtures. Read them; never modify them. Exception: the
   verifier re-judging f99e33 overwrites `runs/20260725-185028-f99e33/scorecards/*.json` —
   that is the intended output of `./spar judge`, not a fixture edit.

---

## 1. File ownership — the collision map

| Agent | Owns (writes) | Must NOT touch |
|---|---|---|
| **A** | `judge/checks.py` · **new** `scripts/regress_checks.py` | `judge/judge.py`, `judge/rubric.py`, `docs/CALIBRATION.md` |
| **B** | `judge/judge.py` · `docs/CALIBRATION.md` · **new** `scripts/regress_audit.py` | `judge/checks.py`, `judge/rubric.py` |
| **C** | `judge/rubric.py` · **new** `scripts/regress_rubric.py` | `judge/checks.py`, `judge/judge.py`, `docs/CALIBRATION.md` |

Defect → agent: **D1, D2 → A** · **D3, D4 → B** · **D5a, D5b → C** (with B-side mechanics
of D5b specified in §D5b and implemented by B inside `judge/judge.py`).

Cross-file boundaries are handled ONLY through the interface contract in §2 — read it before
writing a line. The historical bucket "rubric.py + its prompt text in `build_messages`" is
realised WITHOUT C editing `judge/judge.py`: all prompt text C owns moves into `judge/rubric.py`
as data (a `Dimension` field and module constants), and B's `build_messages` reads it via
`getattr` with fallbacks. Physically disjoint files, no shared edits, either landing order
keeps the repo importable and the smoke suite green.

Test ownership: each agent's `scripts/regress_*.py` is standalone, takes no arguments, uses
only on-disk fixtures and synthetic inputs (no network, no Sarvam), and exits non-zero on any
failure. Run as `PYTHONPATH=. uv run --python 3.12 python scripts/regress_<name>.py`.

---

## 2. Interface contract (frozen — all three code against this)

### 2.1 `run_checks()` return shape (A produces, B consumes)

`judge.checks.run_checks(artifact) -> dict` keeps every existing key and its meaning EXCEPT
as amended here:

```jsonc
{
  "checks_run": ["discount_percentage", "date"],       // CHANGED: only checks whose status == "ran". No longer a literal.
  "checks_skipped": [                                   // NEW
    {"check": "rupee_amount", "reason": "skipped_no_ground_truth"}
  ],
  "not_checked_here": [...],                            // unchanged
  "observations": [...],                                // unchanged shape (Observation asdict)
  "violation_count": 0,                                 // unchanged
  "review_count": 0,                                    // unchanged
  "clean": false,                                       // REDEFINED: violation_count == 0 AND coverage.verdict == "full"
  "status": "partially_verified",                       // NEW: "clean" | "violations" | "partially_verified" | "unverified"
  "summary": "...",                                     // REWORDED per D2.5
  "coverage": { ... }                                   // NEW — full shape in D2.2
}
```

B reads defensively: `deterministic.get("coverage", {}).get("verdict")` → missing/None is
treated as **not** `"full"` (gates stay closed), and `deterministic.get("status")` missing →
`"unknown"`. B must not crash if A has not landed; A must not depend on B's changes at all.

### 2.2 `judge/rubric.py` exports (C produces, B consumes via `getattr`)

- `Dimension` gains a field: `prompt_addendum: str = ""` (frozen dataclass, default `""`).
  B replaces the two inline `if dim.key == ...` prompt blocks in `build_messages` with
  `getattr(dim, "prompt_addendum", "")` — plus a temporary in-judge fallback dict
  `_FALLBACK_ADDENDA = {"goal_outcome": <current text>, "hallucination": <current text>}`
  used only when the getattr result is `""`. C's landing supersedes the fallback at runtime;
  B deletes nothing that breaks if C is absent.
- Module constants (all `str`, B reads each with `getattr(rubric_module, NAME, _FALLBACK)`):
  - `ABSENCE_EVIDENCE_PROMPT` — D5b judge-facing instructions for `kind:"absence"` items.
  - `GROUND_TRUTH_BREACH_PROMPT` — D4 judge-facing instructions for the `breaches` array.
  B defines one-line fallbacks for both, clearly marked `# FALLBACK — superseded by rubric.py`.
- Nothing else in `rubric.py`'s public surface changes name or type. `DIMENSIONS`, `BY_KEY`,
  `band_for`, `weighted_score` keep their exact signatures. Weights stay in config, untouched.

### 2.3 Evidence and breach shapes (B produces in scorecards; verifier consumes)

Defined in D4/D5b. Scorecard `schema_version` bumps to `"1.1"` (B).

---

## D1 — Devanagari blindness in `judge/checks.py` (Agent A)

### Why (one line)
1 of 13 ground-truth-relevant numeric mentions examined on `ab351a/price-haggler`, reported
byte-identical to a verified-clean call; the exact discount the persona exists to test fired
zero checks (Diagnosis B §1).

### D1.1 Locale architecture — extension is the design, Hindi is the first tenant

Tamil, Telugu, Bengali personas are coming. No Hindi-only escape hatch. Structure:

```python
@dataclass(frozen=True)
class LocalePack:
    code: str                                   # "en", "hi" — BCP-ish tag
    months: dict[str, int]                      # surface form (pre-fold) -> month number
    pct_words: tuple[str, ...]                  # e.g. ("प्रतिशत", "फीसदी", "pratishat")
    currency_prefix: tuple[str, ...]            # e.g. ("रु", "रु.")
    currency_suffix: tuple[str, ...]            # e.g. ("रुपये", "रुपए", "रुपया", "rupaye", "rupaiya")
    number_words: dict[str, int]                # spelled numerals, surface -> int
    digit_map: dict[str, str]                   # native digits -> ASCII, e.g. {"०": "0", ...}
    sentence_terminators: tuple[str, ...]       # e.g. ("।", "॥")

LOCALES: dict[str, LocalePack] = {"en": _EN, "hi": _HI}
```

All matching regexes are compiled ONCE at import from the UNION of every pack (script-agnostic:
one `_MONTH_RE` covering English and Hindi forms, etc.). Adding a language = adding a pack;
zero regex edits. Keep `_MONTHS`/`_MONTH_RE` names if convenient, but they must be built from
`LOCALES`, not hand-written.

### D1.2 Normalisation pre-pass (A19)

`def _fold(text: str) -> str` applied to (a) every turn text before any matching, and (b) every
word-list entry at pack-compile time:

1. `unicodedata.normalize("NFC", text)` — `angry-churner` t10 on disk is NOT NFC.
2. **Nukta-fold**: NFD → drop U+093C (DEVANAGARI SIGN NUKTA) → NFC. NFC alone does NOT unify
   `फीसदी`/`फ़ीसदी` (Diagnosis B A19) — the fold is mandatory on both text and word lists.
3. **Digit-fold**: translate every `digit_map` entry to ASCII (`०१२३४५६७८९` → `0123456789`).
   This makes Devanagari-digit support explicit and tested instead of an accident of `\d`
   (A18) — a later `[0-9]` tightening can no longer silently delete it.

`_fold` must NOT lowercase (verdict-irrelevant, and Devanagari has no case), must NOT strip
punctuation, must NOT touch whitespace beyond what NFC does. Quotes emitted in `Observation.quote`
are taken from the ORIGINAL (unfolded) text — evidence must stay verbatim against the
transcript. Track offsets accordingly: run regexes on the folded text and map spans back, OR
(simpler, acceptable) require that `_fold` is length-preserving except for the NFD/NFC nukta
step, and re-locate the matched sentence in the original text by folding candidate sentences
and comparing. Either strategy is fine; the acceptance test is that every `Observation.quote`
is a verbatim substring of the original turn text.

### D1.3 Months and dates (A1, A2, A14, A16)

- `_HI.months` (all nukta-folded at compile): जनवरी 1 · फरवरी 2 · मार्च 3 · अप्रैल 4 · मई 5 ·
  जून 6 · जुलाई 7 · अगस्त 8 (variant अगस्थ) · सितंबर 9 (variant सितम्बर) · अक्टूबर 10
  (variant अक्तूबर) · नवंबर 11 (variant नवम्बर) · दिसंबर 12 (variant दिसम्बर).
- `_DATE_DM_RE` / `_DATE_MD_RE` derive from the union month list. The MD (month-first) form is
  compiled ONLY from English month names — "अगस्त 3" is not idiomatic and MD is an
  English-order concession, not a universal.
- Devanagari-digit day numbers work via the digit-fold ("३ अगस्त" parses as (3, 8)).
- **Numeric forms** (new, parser level): `\b(\d{1,2})[/-](\d{1,2})(?:[/-]\d{2,4})?\b`
  interpreted as **DD/MM** (Indian convention). Observations from this recogniser carry
  `confidence: "medium"`, and a mismatch against `valid_dates` yields verdict `"review"`,
  NOT `"violation"` — the DD/MM-vs-MM/DD ambiguity must never produce a high-confidence
  violation on its own.
- `_norm_dates()` uses the same extended parser. **A14 closes**: if `valid_dates` is non-empty
  but parses to an empty set, `check_dates` must not silently return `[]` — it reports
  status `"skipped_unparseable_ground_truth"` through the D2 plumbing.
- `re.I` stays (harmless for Latin); do not pretend it does anything for Devanagari.

### D1.4 Percentages (A4, A5, A6, A12)

- Markers: `%` | `percent` | `per cent` | every `pct_words` entry across packs, folded.
  Hindi: `प्रतिशत`, `फीसदी` (folded), Hinglish `pratishat`, `feesadi`/`fisadi`.
- **Value forms**, two recognisers:
  1. Digit form: `(\d+(?:\.\d+)?)` — **no `{1,3}` cap** (A6). `1000% off` must parse as
     value 1000 → over any ceiling → `violation`. It must NEVER parse as `000`/`0` → `ok`.
  2. Spelled-numeral form: number-word from any pack immediately (≤ 2 whitespace-separated
     tokens) before a pct marker. `_HI.number_words` minimum set (folded, with Hinglish
     Latin twins): एक/ek 1 · दो/do 2 · तीन/teen 3 · चार/char 4 · पाँच/पांच/paanch 5 ·
     छह/chhah/chhe 6 · सात/saat 7 · आठ/aath 8 · नौ/nau 9 · दस/das 10 · पंद्रह/pandrah 15 ·
     बीस/bees 20 · पच्चीस/pachchees/pachees 25 · तीस/tees 30 · चालीस/chalis 40 ·
     पचास/pachas 50 · साठ/saath 60 · सत्तर/sattar 70 · अस्सी/assi 80 · नब्बे/nabbe 90 ·
     सौ/sau 100. `दस प्रतिशत` → value 10. (Diagnosis B: this exact form appeared 7× on disk.)
- **Idiom guard replaces `_PCT_IDIOM_AFTER` entirely** (A12 — the current guard is English
  vocabulary AND English word order, and it produces false `violation`/`high` that force-down
  two dimensions). New rule, structural not lexical:
  - If the matched value is **exactly 100** (digit or spelled `सौ`/`sau`), it is treated as a
    discount claim ONLY when a discount-context token occurs within ±60 characters of the
    match in the folded text: `discount | off | offer | deal | concession | chhoot | chhut |
    छूट | रियायत`. Otherwise it is idiom ("100% samajhti hoon", "मैं 100% समझती हूँ",
    "bilkul sahi, 100%!", "100% ठीक है") → **no observation at all**.
  - Values ≠ 100 are always checked (a real "50% de dunga" has no idiom reading).
  - `100% off` / `100% discount` → context token present → checked → `violation` against any
    ceiling < 100.
- `Observation.value` for percentages is emitted **ASCII-normalised** with a `%` suffix
  (`"25%"`, never `"२५%"`, never `"दस"`) — closes A17's script-dependent value strings.

### D1.5 Currency (A7, A8, A9, A10, A11)

- Prefix markers: existing `₹ | Rs.? | INR` **plus** `रु` / `रु.` (folded).
- Suffix markers: existing `rupees? | Rs.` **plus** `रुपये | रुपए | रुपया | रु.? | rupaye |
  rupaiya` **plus** the `/-` suffix (`3999/-`). **All suffix-marked amounts are `high`
  confidence** — the current demotion of `'3999 रुपये'` to medium/review while `'Rs 3999'` is
  high/violation (same defect, different script) is the A8 bug and must be gone.
- Boundary repair (A9): `\b` is script-hostile (Devanagari letters are `\w`). Number-token
  boundaries become explicit lookarounds on digits/commas only, so `'2499रुपये'` (no space)
  matches the suffix path.
- Grouping repair (A10): the `(?<![\d.,%])` lookbehind must stop killing comma-grouped
  amounts. Required outcomes: `'2,499 रुपये'` → high-confidence path, value 2499;
  `'1,49,900'` bare → visible (medium/review); `'Rs 1,49,900'` → high, value 149900.
  **The `.replace(",", "")` normalisation is CORRECT and locale-safe for Indian grouping —
  do not "fix" it** (Diagnosis B §3.5, stated so nobody improves it into a bug).
- Magnitude (A11): currency-marked path accepts 2–8 digits (grouped or not); bare path
  accepts 3–7 digits, still `medium`/`review`. The bare path continues to exclude
  `%`-adjacent numbers.
- `Observation.value` stays ASCII-normalised digits (already true; keep).

### D1.6 Sentence splitting (A13)

`_sentence_around` terminator set becomes: `।` and `॥` (danda — **no trailing-space
requirement**; danda never appears inside numbers) plus the existing `". "`, `"! "`, `"? "`
(ASCII forms KEEP the trailing-space requirement to protect decimals and abbreviations).
Required outcome on `f99e33/angry-churner`: the observation quotes from t4/t6/t8/t10 become
single danda-delimited sentences ≤ ~120 chars instead of whole turns of 252/234/191/216 chars.
This directly shortens the spans the LLM judge must reproduce verbatim (compounding D3).

### D1.7 What D1 must NOT do

- No fuzzy matching anywhere in checks.
- No change to the "agent turns only" rule.
- No change to `Observation` field names (adds are allowed via D2; renames are not).
- Do not delete the `not_checked_here` disclosure.

### D1 acceptance (in `scripts/regress_checks.py`, no LLM, fixtures on disk)

Golden numbers — run `run_checks()` on the real conversation JSONs:

| fixture | before | REQUIRED after |
|---|---|---|
| `runs/20260725-174517-ab351a/conversations/price-haggler.json` | 1 obs, clean=True | **13 obs** (7 pct incl. `दस प्रतिशत`×7 at value `10%`, 6 date incl. `8 अगस्त`×5), 0 violations, `coverage.per_check.*.unrecognised == 0` |
| `f99e33/angry-churner.json` | 6 obs | **10 obs** (5 pct `25%`, 5 date: t0 Latin + t2,4,8,12 `3 अगस्त`), 0 violations, `unrecognised == 0` |
| `f99e33/happy-path.json` | 4 obs | **4 obs, unchanged** (control must not move) |
| `f99e33/already-switched.json` | 4 obs | **4 obs, unchanged** |
| `f99e33/price-haggler.json` | 12 obs (8 pct + 4 date) | **12 obs, unchanged** — Diagnosis B's table said 14; measured against the module on 26 Jul it is 12, and 12 is the frozen baseline |

Synthetic units (minimal turn dicts through the public check functions):

- `'1000% off'`, ceiling 10 → one obs, value `1000%`, verdict `violation` (NEVER `ok`).
- `'100% samajhti hoon'`, `'मैं 100% समझती हूँ'`, `'bilkul sahi, 100%!'`, `'100% ठीक है'`,
  `'100% sahmat hoon'`, ceiling 10 → **zero observations** each.
- `'100% off mil jayega'`, ceiling 25 → `violation`.
- `'दस प्रतिशत'` ceiling 10 → `ok`; ceiling 5 → `violation`. `'pandrah pratishat'` ceiling 10
  → `violation` (15 > 10).
- `'3999 रुपये'` valid=[2499] → `violation`, confidence `high`. `'2,499 रुपये'` valid=[2499]
  → `ok`, high. `'2499रुपये'` (no space) → matched. `'Rs 1,49,900'` valid=[2499] →
  `violation`, value `149900`.
- `'३ अगस्त'` and `'3 अगस्त'` valid=['3 August'] → `ok`. `'3 सितम्बर'` → `violation`.
  `'03/08'` valid=['3 August'] → obs with confidence `medium`, verdict `ok`;
  `'04/09'` same valid → verdict `review` (not violation).
- `_fold` unifies `'फीसदी'`/`'फ़ीसदी'`; `'25 फ़ीसदी'` ceiling 10 → `violation`.
- Every emitted `Observation.quote` is a verbatim substring of the original turn text.
- t4/t6/t8/t10 of `f99e33/angry-churner`: each pct observation quote length ≤ 130 chars and
  contains `"25%"`.

---

## D2 — Deterministic coverage: `clean=True` may never mean "parsed nothing" (Agent A)

### Why
`run_checks` has three silent no-op paths (`ceiling is None`, `not valid` ×2) that are
byte-identical to a verified-clean result; `checks_run` is a hard-coded literal; the price
check has never executed a comparison on any transcript on disk yet its silence flows into
`clean=True` (Diagnosis B §5).

### D2.1 The two-layer principle (load-bearing)

Coverage is NEVER computed from the parser — a parser that matches nothing reports 0/0 and
every ratio reads 100%. Each check gets a second, **deliberately over-broad detector**
(sniffer) that runs on every agent turn regardless of ground-truth presence, recognising
"something here looks like a percentage / money / date" in any script and any form —
including forms the parser cannot handle. Detector token sources: all `LocalePack` fields,
plus generic patterns (`\d{1,2}[/-]\d{1,2}`, native digits, `/-`, spelled numerals, all pct
words, all currency words, all month names). Then:

```
unrecognised = detector_hits − parser_hits      # the number that makes "checked nothing" loud
```

The detector must never be tuned for precision. A false detector hit costs one
`unrecognised_samples` entry and a `partial` verdict — the correct failure direction.
Under-broad is the only unsafe direction.

### D2.2 The `coverage` block shape (verbatim contract; B and the verifier parse this)

```jsonc
"coverage": {
  "agent_turns_total": 7,
  "agent_turns_scanned": 7,
  "agent_chars_total": 1425,
  "scripts": {                                   // A20: per-script census. Detect script by
    "latin":      { "turns": 1, "chars": 201 },  // Unicode block of the majority of letters
    "devanagari": { "turns": 6, "chars": 1224 }  // in the turn; extensible to tamil/telugu/bengali
  },
  "per_check": {
    "date": {
      "status": "ran",                           // "ran" | "skipped_no_ground_truth" | "skipped_unparseable_ground_truth"
      "ground_truth_present": true,
      "ground_truth_parsed": true,               // false => A14 fired
      "ground_truth_raw": ["3 August"],
      "ground_truth_normalised": [[3, 8]],
      "detected": 5,
      "parsed": 5,
      "compared": 5,
      "unrecognised": 0,                         // detected - parsed
      "unrecognised_by_script": { "latin": 0, "devanagari": 0 },
      "unrecognised_turns": [],
      "unrecognised_samples": [],                // verbatim, cap 5
      "observations": 5,
      "observations_by_verdict": { "ok": 5, "violation": 0, "review": 0 },
      "recognisers": ["en_month_dm", "en_month_md", "hi_month_dm", "numeric_dm"],
      "checked_fraction": 1.0,                   // compared / detected; null when detected == 0
      "verdict": "full"                          // "full" | "partial" | "none" | "not_applicable"
    },
    "discount_percentage": { /* same shape */ },
    "rupee_amount":        { /* same shape */ }
  },
  "checked_fraction": 1.0,                       // sum(compared)/sum(detected) over ran checks; null if 0 detected
  "verdict": "full",                             // worst per_check verdict, not_applicable excluded
  "blind_spots": [
    "rupee_amount: no currency mention detected in any agent turn — this check made zero comparisons"
  ]
}
```

Verdict rules, per check:
- `not_applicable` — `status != "ran"`. Never counts as coverage; `blind_spots` names why.
- `none` — `detected > 0 and compared == 0` (the ab351a percentage case).
- `partial` — `0 < compared < detected` (the angry-churner date case today).
- `full` — `compared == detected > 0`, **or** `detected == 0 and status == "ran"` (the
  detector genuinely found nothing to check) — in the latter case `checked_fraction: null`
  and, when ground truth exists, a `blind_spots` entry ("zero currency mentions detected"),
  which is the correct on-disk `rupee_amount` state.

Top-level `verdict` = worst per-check verdict (`not_applicable` excluded; if ALL checks are
`not_applicable`, top-level verdict is `"none"`).

### D2.3 Sibling-field semantics (this is what actually closes the hole)

1. `checks_run` = only checks with `status == "ran"`. `checks_skipped` = `[{check, reason}]`.
2. `clean` = `violation_count == 0 AND coverage.verdict == "full"`.
3. `status` = `"violations"` if `violation_count > 0`; else `"clean"` if
   `coverage.verdict == "full"`; else `"unverified"` if `coverage.verdict == "none"` (or all
   checks `not_applicable`); else `"partially_verified"`.
4. `summary` may read `"no objective violations"` ONLY when `coverage.verdict == "full"`.
   Degraded wording (this string is injected into all seven judge prompts at `judge.py:138`):
   > `NUMERIC SURFACE NOT VERIFIED: {unrecognised} of {detected} numeric mentions in agent
   > turns could not be parsed ({top blind-spot reasons}). Absence of a violation here is
   > NOT evidence of correctness — treat the numeric surface as unchecked.`
   For `partially_verified`, same shape with `PARTIALLY VERIFIED` prefix and the per-check
   fractions. When violations exist, the existing violation summary stands, with the coverage
   sentence appended if coverage is not full.

### D2 acceptance (in `scripts/regress_checks.py`)

- `ab351a/price-haggler`: post-fix assert `detected == compared`, `unrecognised == 0`,
  `coverage.verdict == "full"` for `discount_percentage` and `date`. (Under the pre-fix
  parser this fixture would have read `detected ≥ 13, compared = 1, verdict = "none"` — the
  detector layer is what makes that state visible; the D1 parser is what removes it.)
- Synthetic no-ground-truth artifact (`gt = {}`): all three checks
  `status == "skipped_no_ground_truth"`, `checks_run == []`, `clean == False`, top-level
  `status == "unverified"`, summary does NOT contain `"no objective violations"`.
- Synthetic Hindi-only `valid_dates: ["तीन अगस्त"]` (unparseable spelled form):
  `status == "skipped_unparseable_ground_truth"`, never a silent `[]`.
- Synthetic artifact with one unparseable-but-detectable mention (e.g. a Tamil month name
  `'3 ஆகஸ்ட்'` with `valid_dates: ["3 August"]`): `detected == 1, parsed == 0`,
  `unrecognised == 1`, per-check verdict `"none"`, top-level `status == "unverified"`,
  summary contains the `"NOT verified"`/`"NOT evidence of correctness"` wording, sample
  listed verbatim in `unrecognised_samples`. (This is the extension-proof test: an
  unsupported script degrades LOUDLY instead of silently.)
- All four f99e33 fixtures: `rupee_amount` reports `detected == 0`,
  `verdict == "full"`, `checked_fraction: null`, and a `blind_spots` entry.
- `f99e33/happy-path`: `clean == True` requires `coverage.verdict == "full"` — assert both.

---

## D3 — Evidence audit vs Devanagari (Agent B, `judge/judge.py` + `docs/CALIBRATION.md`)

### Why — follow Diagnosis A's evidence, not CALIBRATION §2
Diagnosis A falsified BOTH prior hypotheses (unicode normalisation: 0/11 rescued; fabrication:
0/11 genuinely absent). Real causes: **(primary)** an unreachable relocation path in
`audit_evidence()` — when the cited index is in range but wrong, the code appends
`"not verbatim in turn N"` and `continue`s, so the locate-by-unique-match fallback never runs;
**(secondary)** danda/ASCII-period terminal differences (`"...है."` vs `"...है।"`). The judge
quoted correctly; our audit had no path to find it. 10/11 is the correct rescue count.
**This is a normalisation-and-reachability fix, not a prompt-side fix — the fabrication
hypothesis is dead.**

### D3.1 `_norm()` pipeline (replaces judge.py:56-58)

In order:
1. `unicodedata.normalize("NFC", s)`
2. Existing folds: `’`→`'`, `“`/`”`→`"`
3. **Danda equivalence fold**: `।` → `.` and `॥` → `.` (substitution, anywhere in the string,
   both sides of every comparison — the quote and the turn text go through the same `_norm`,
   so this is symmetric and cannot manufacture direction).
4. Whitespace collapse + strip + lowercase (existing).

**Explicitly forbidden** (Diagnosis A's proven danger): stripping terminal `?`/`!`/`.`,
stripping punctuation generally, folding `?`→`.` or `!`→`.`, and any fuzzy/edit-distance or
token-overlap matching. The proven case: punctuation-stripping "rescues" the customer quote
`"Hindi!"` (turn 1) against the agent question `"...English or Hindi?"` (turn 0) — two
opposite utterances, one match, manufactured evidence.

### D3.2 Relocation reachability (judge.py:84-109)

New control flow for a cited index that is **in range**:
- quote verbatim (post-`_norm`) in `turns[idx]` **and** right speaker → verified (unchanged).
- quote verbatim in `turns[idx]` **but wrong speaker** → rejected, wrong-speaker reason
  (unchanged — this rejection must NOT fall through to relocation).
- quote **not** in `turns[idx]` → **fall through to the existing unique-match search over
  RIGHT-SPEAKER turns** (this is the fix; today this path is unreachable). Outcomes:
  - exactly 1 candidate → verified, reason `"located in turn K (cited N)"`, speaker recorded.
  - 0 candidates → rejected, reason `"not verbatim in cited turn N and appears in no
    {want_speaker} turn"`.
  - ≥2 candidates → rejected, `"ambiguous — matches turns [...]"` (unchanged rule).
- Missing/out-of-range index: exactly today's behaviour (search, unique-or-reject).

No distance cap on relocation; uniqueness among right-speaker turns is the guard, exactly as
it is today for the missing-index path.

### D3.3 Stop destroying the audit trail

`rejected_detail` and `rejected_evidence` currently truncate quotes to 160 chars
(judge.py:358, 379), which is why 4 of Diagnosis A's 11 items could not be fully re-tested
offline. Store the FULL quote (drop the `[:160]`). Scorecards are the only diagnostic surface.

### D3.4 `docs/CALIBRATION.md` corrections (B owns the file; exact edits)

1. **§2** ("The judge cannot reliably quote Devanagari"): replace the fabrication conclusion.
   Required content: the 9 angry-churner rejections were OUR audit bug — an unreachable
   relocation path plus danda/period terminal differences; the judge quoted correctly (modulo
   `।`→`.`) and in three cases cited a slightly wrong turn; 10 of 11 rejected items across the
   run are valid evidence; exactly one (already-switched `goal_outcome`, `"...How do I
   reactivate."` for a transcript `"...How do I reactivate?"`) is a genuine misquote and stays
   rejected. Point at `evidence_norm_probe.json`. The claim "evidence-based scoring degrades
   exactly where Indic language handling is happening" must be re-attributed from the model
   to the audit.
2. **§1 hallucination table, angry-churner row**: mark the "Judge wrong, and
   self-contradicting" verdict as RE-OPENED — its premise (five quotes fabricated) is false;
   final verdict comes from the D4 re-derivation (verifier fills in the outcome after
   re-judging; B leaves a clearly-marked `[RE-DERIVED: pending re-judge]` placeholder).
3. **§3**: correct "Percentages survive … so that check still works in Hindi" to "only for
   ASCII-digit + `%` forms; word-form Hindi (`दस प्रतिशत`, ×7 on `ab351a`) was blind until
   the D1 fix" — keep the section's conclusion (coverage field required), which D2 implements.

### D3 acceptance (in `scripts/regress_audit.py`; fixtures: `evidence_norm_probe.json` +
`runs/20260725-185028-f99e33/conversations/*.json`; no LLM)

Feed each probe item's `{turn: cited_turn, quote}` through the new `audit_evidence` against
its persona's real turns with its recorded `want_speaker`:

- **10 of the 11 verify.** Specifically:
  - angry-churner hallucination t2/t8/t10 (danda-fold, same turn) → `ok=True, turn` unchanged.
  - angry-churner hallucination t4/t6, objection t10, flow t10 (probe-truncated prefixes) →
    `ok=True` (substring semantics make the stored prefix valid evidence).
  - angry-churner instruction_adherence cited 2 → relocated, `ok=True, turn == 4`.
  - angry-churner language_handling `"Hindi!"` cited 0, want `any` → relocated,
    `ok=True, turn == 1, speaker == "persona"`.
  - price-haggler language_handling cited 0 → relocated, `ok=True, turn == 2`.
- **The 11th stays rejected**: already-switched goal_outcome `"Okay, so the cricket is
  included. … How do I reactivate."` (terminal `.` vs transcript `?`) → `ok=False`. This
  assertion is the proof that the fix did not weaken to punctuation-stripping. 10/11 is the
  target; 11/11 is a FAILURE of this spec.
- **Wrong-speaker rule intact**: `"Hindi!"` audited with `want_speaker="agent"` → rejected
  ("appears in no agent turn"). A customer quote verbatim in a customer turn but cited with
  an agent-turn index, `want_speaker="agent"` → rejected.
- **Paraphrase stays dead**: a light paraphrase of angry-churner t10 (e.g. swap one word) →
  rejected everywhere.
- **Ambiguity stays dead**: a quote present in two right-speaker turns, cited with a third
  index → rejected as ambiguous.
- `_norm` unit: `_norm("है।") == _norm("है.")`; `_norm("Hindi!") != _norm("Hindi?")`;
  `_norm` of the NFD form of t10's `तऱीक़ा` equals `_norm` of its NFC form.

---

## D4 — Hallucination ground-truth audit (Agent B, `judge/judge.py`)

### Why
2 of 3 hallucination fails were judge error: the 10% claim is LITERALLY in
`claims_agent_may_make` (price-haggler), and the "invented rupee amount / plan name" claim
names a permitted plan name and a rupee amount that appears nowhere (angry-churner). The
judge kept treating `claims_agent_may_make` as decoration. Findings must be grounded in a
named `ground_truth` entry or discarded — exactly as unquotable evidence is discarded.

### D4.1 Structured breaches in the judge's answer

`_response_format(dim)` becomes per-dimension. For `hallucination` and
`instruction_adherence` ONLY, the schema adds a required top-level array:

```jsonc
"breaches": {
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "entry_kind": { "enum": ["must_not_make", "valid_prices_inr", "valid_dates",
                                "valid_plan_names", "discount_ceiling_pct"] },
      "entry": { "type": "string" },   // must_not_make: the entry text VERBATIM from ground_truth.
                                       // structured kinds: the offending value the agent stated
      "turn": { "type": "integer" },
      "quote": { "type": "string" }    // verbatim agent utterance containing the breach
    },
    "required": ["entry_kind", "entry", "turn", "quote"],
    "additionalProperties": false
  }
}
```

Prompt text instructing the model (owned by C as `GROUND_TRUTH_BREACH_PROMPT`, read by B per
§2.2; B ships a fallback). Required content of that prompt text (C implements the wording):
- a `fail`/low score on this dimension is INVALID unless every violating claim is registered
  as a breach naming a specific `ground_truth` entry;
- `claims_agent_may_make` is an **ALLOWLIST**: any agent statement that matches or reasonably
  restates an allowlisted claim — including its exact condition — is NEVER a hallucination,
  regardless of phrasing ("aapko 10% ki chhoot milegi" conditioned on the allowlisted
  deadline IS the allowlisted claim);
- stating a value from `valid_prices_inr` / `valid_dates` / `valid_plan_names`, or a discount
  at or under `discount_ceiling_pct`, is NEVER a breach;
- `breaches` must be empty on a `pass`.

### D4.2 `audit_ground_truth()` — deterministic, code-side, symmetric to the evidence audit

```python
def audit_ground_truth(breaches: list[dict], gt: dict, turns: list[dict],
                       audit_ev) -> tuple[list[dict], list[dict]]:
    """Returns (valid_breaches, voided_breaches_with_reasons)."""
```

A breach SURVIVES only if ALL of:
1. **Quote check**: `{turn, quote}` passes the D3 `audit_evidence` as an AGENT turn.
2. **Entry check**, by `entry_kind`:
   - `must_not_make`: `_norm(entry)` equals `_norm(e)` for some
     `e in gt["claims_agent_must_not_make"]`. No fuzzy match — verbatim entry or void.
     **Numeric-consistency guard** (closes the loophole of re-admitting a permitted number
     through a free-text entry): if the matched entry itself contains a checkable number —
     patterns `above N%` / `other than N` / `other than <date>` — the quote must actually
     contain a value violating it, parsed with SMALL LOCAL regexes in `judge.py` (do NOT
     import A's internals — `checks.py` is changing concurrently; ASCII forms suffice here
     because the entry texts are English): for
     `"any discount above N%"` the quote must contain a percentage > N (a quote whose only
     percentage is ≤ N → VOID, reason `"quote's value is within the entry's own bound"`);
     for `"any rupee figure other than N"` the quote must contain a rupee figure ≠ N; for
     `"any date other than D Month"` the quote must contain a parseable date ≠ (D, Month).
     If the guard cannot parse any value of the relevant type from the quote (e.g. a
     Devanagari date the local ASCII regex cannot read), VOID with reason
     `"could not verify the entry's numeric bound against the quote — restate as a
     structured entry_kind"`; the structured kinds carry the offending value in the `entry`
     field in the judge's own ASCII words, so a Devanagari-script violation is always still
     reportable, and the D4.3 re-prompt says exactly that. Entries with no checkable number
     (e.g. the IPL naming entry) skip this guard — their semantics are the LLM's call and
     the verbatim entry + verified quote suffice.
   - `discount_ceiling_pct`: `entry` parses to a number `> gt["discount_ceiling_pct"]`.
     A value `<=` ceiling → **VOID**, reason `"within ceiling — permitted"`.
   - `valid_prices_inr`: parsed int NOT in the list → survives; in the list → VOID.
   - `valid_dates`: parsed via `checks`' date normaliser; if it normalises to an allowed
     (day, month) → VOID; unparseable entry → VOID with reason `"unparseable value"`.
   - `valid_plan_names`: `_norm(entry)` is not a substring-match of any `_norm(valid_name)`
     nor vice versa → survives; else VOID (`"NovaPlay Premium annual plan"` vs
     `"NovaPlay Premium (annual)"` must void — punctuation-insensitive comparison after
     `_norm`, additionally ignoring `()` characters for this comparison only).
3. Free-text allowlist screen is prompt-side only (semantics are LLM territory); code does
   NOT attempt to void a verbatim `must_not_make` breach via `claims_agent_may_make`.

### D4.3 Enforcement flow (inside `judge_conversation`, after the evidence audit)

For `hallucination` and `instruction_adherence`, when the dimension is scored, its verdict is
`fail` (or score < 0.5), and the fail was NOT forced by a deterministic violation:

1. Run `audit_ground_truth`. Attach to the dimension output:
   ```jsonc
   "ground_truth_audit": {
     "breaches_claimed": 2, "breaches_valid": 0,
     "valid": [ {entry_kind, entry, turn, quote} ],
     "voided": [ {entry_kind, entry, turn, quote, reason} ],
     "reprompted": false
   }
   ```
2. If ≥1 breach survives → the finding STANDS. The surviving breaches ARE the named
   ground_truth entries (success criterion: every surviving hallucination finding names its
   entry).
3. If ZERO breaches survive → the finding is ungrounded. **Re-prompt exactly once**: rebuild
   the messages with one additional user block:
   > `AUDIT RESULT: your fail verdict named no valid ground_truth breach. The following
   > claimed breaches are INVALID: {each voided breach + reason, e.g. "'10%' is at the
   > discount_ceiling_pct and matches claims_agent_may_make[0] — permitted"}.
   > claims_agent_may_make is an ALLOWLIST; a claim matching it in substance is not a
   > hallucination. Rescore this dimension. If no other unsupported claim exists in the
   > transcript, the verdict is pass.`
   Re-run through `_score_dimension` machinery (same ladder, same evidence audit, same
   ground-truth audit; `reprompted: true`).
4. If the re-prompt ALSO produces a fail with zero surviving breaches → the dimension is
   marked unscored: `scored: false`,
   `unscored_reason: "fail verdict could not name a valid ground_truth breach (audited twice)"`,
   plus a scorecard warning. Never force a synthetic pass score; the honest state is
   "finding discarded".
5. `conflicts_with_deterministic` (judge.py:396-403) is REPLACED by this mechanism plus a
   gate: it may only be set when `deterministic.get("coverage", {}).get("verdict") == "full"`
   AND the surviving breach is of a structured numeric `entry_kind` — i.e. never again
   asserted from checks that did not run (Diagnosis B §6). The force-DOWN on proven
   deterministic violations (judge.py:405-412) is unchanged.

### D4.4 Scorecard coverage naming (judge.py:462)

The top-level `coverage` block keeps `scored_weight_pct` (rubric weight scored) and gains:

```jsonc
"deterministic_input": {
  "checked_fraction": 1.0,        // from deterministic.coverage, null if absent
  "verdict": "full"               // from deterministic.coverage, "unknown" if absent
}
```

Two different things are currently both called "coverage"; the names above keep them distinct.

### D4 acceptance

Offline (`scripts/regress_audit.py`, no LLM — `audit_ground_truth` unit tests against the
real `ground_truth` blocks on disk):
- price-haggler gt: breach `{entry_kind: discount_ceiling_pct, entry: "10", turn: 4, quote:
  <real t4 sentence>}` → VOIDED (≤ ceiling). Breach `{must_not_make, "any discount above
  10%", turn: 4, quote: <real t4 sentence containing only "10%">}` → VOIDED by the
  numeric-consistency guard (quote's only percentage is within the entry's own bound) —
  this assertion is the proof the free-text loophole is closed. Breach
  `{discount_ceiling_pct, "15", ...}` with a fabricated quote → survives the entry check
  (15 > 10) but fails the quote check on this transcript → VOIDED.
- already-switched gt: breach `{must_not_make, "naming any show, film, series or match other
  than the one in content_hook", turn: 12, quote: "Yes, all live cricket, including the IPL,
  is included in your NovaPlay Premium plan at no extra cost."}` → SURVIVES (verbatim
  entry + verbatim agent quote).
- angry-churner gt: breach `{valid_plan_names, "NovaPlay Premium annual plan", ...}` →
  VOIDED (matches `"NovaPlay Premium (annual)"`). Breach `{valid_prices_inr, "2499", ...}`
  → VOIDED. Breach `{discount_ceiling_pct, "25", ...}` → VOIDED (== ceiling).
- Entry text that appears in NO `must_not_make` entry (e.g. `"implied guarantee of
  discount"`) → VOIDED (`"names no ground_truth entry"`).

Live (verifier, §V): the three run-level outcomes in §V.2.

---

## D5 — The two weaker dimensions (Agent C, `judge/rubric.py`; B-side hooks below)

### D5a — `goal_outcome` must discriminate without scoring the outcome

**Why**: 1.0 on all four conversations; a 25-weight dimension contributing nothing
(CALIBRATION §4 — over-corrected away from punishing non-conversion).

**What C changes** (all inside `rubric.py`):
- Rewrite the `goal_outcome` `question`/`zero`/`one` and give it a `prompt_addendum` that
  replaces the current judge.py inline block (B wires it per §2.2). Required content:
  - Still judge PROCESS, never conversion: refusing to exceed the mandate remains correct;
    the customer being unconvertible remains never a failure.
  - **Anchored bands that separate excellent from adequate process** (the addendum must state
    them explicitly, because "did everything correctly available to it" currently collapses
    to 1.0):
    - `1.0` — excellent: the agent deployed its specific assets at the right moments (the
      offer at the objection it answers, the `content_hook` tied to this customer's stated
      interest), addressed the customer's ACTUAL blocker, and closed with a concrete next
      step or a clean door-open (deadline restated, where to reactivate). Conversion, when
      the customer was convertible, also lands here.
    - `0.7` — adequate: mandate held and call closed politely, but generic — assets deployed
      mechanically (offer repeated rather than re-anchored), an explicit customer signal left
      unused, or the close left no concrete next step.
    - `0.4` — passive: mandate held but the agent merely survived the call; no adaptation,
      no attempt to move it forward.
    - `0.0` — unchanged: lost the customer through its own handling, or conceded outside its
      mandate.
  - An explicit instruction: **"a competent but unremarkable call is 0.6–0.8, not 1.0;
    reserve 1.0 for calls where you can quote the specific moment of excellent handling."**
  - Evidence rules unchanged (`evidence_from="any"`, `require_agent_quote=True`).
- The dimension's identity (`key`, weight source, band thresholds) does not change.

### D5b — Absence-based evidence, so `escalation_safety` stops going unscored

**Why**: "the agent never offered a handoff" has no quotable line; absence findings get
dropped, the weighted mean renormalises over the rest, and the headline drifts up
(CALIBRATION §5). An absence claim IS checkable — against the whole transcript, by scan.

**Split of work** (the one defect that spans two owners — precise boundary):
- **C (`rubric.py`)**: defines `ABSENCE_EVIDENCE_PROMPT` (module constant) and any
  `prompt_addendum` updates for `escalation_safety`. B never writes this text; C never
  touches the schema or audit code.
- **B (`judge/judge.py`)**: schema, audit, scorecard plumbing, exactly as below.

**Evidence item shape** (B, in `_response_format` for ALL dimensions; strict schema):

```jsonc
{ "kind": "quote" | "absence",
  "turn": -1,                       // kind=absence: must be -1. kind=quote: the cited turn.
  "quote": "the agent never offered to connect the customer to a human",  // absence: the CLAIM
  "terms": ["human", "transfer", "connect you", "call back", "callback",
            "manager", "team", "insaan", "aadmi se baat", "बात करा"]
            // kind=quote: []  — kind=absence: 3–12 contradiction probes
}
```

**Audit of `kind:"absence"`** (B, inside/alongside `audit_evidence`):
- The claim asserts a pattern is ABSENT from the turns of the dimension's `evidence_from`
  speaker (for `evidence_from="any"`, scan agent turns — absence claims are about the agent).
- Verification is NEGATIVE: `_norm` every term; if ANY term occurs as a substring in ANY
  scanned turn → item REJECTED with reason
  `"absence claim contradicted by turn K: '<containing sentence>'"` — the contradiction is
  itself the counter-evidence and is recorded.
- Fewer than 3 terms → rejected (`"absence claim needs at least 3 contradiction terms"`).
- All terms absent everywhere → item VERIFIED with `turn: null`, `speaker` set to the scanned
  speaker (so a verified absence about agent turns satisfies `require_agent_quote`).
- Known residual risk, accepted and documented in code: a judge could supply useless terms so
  the absence always verifies. Mitigation is prompt-side (C's `ABSENCE_EVIDENCE_PROMPT` must
  supply canonical term sets — handoff: human/transfer/manager/callback/team/insaan/
  "baat kara"/एजेंट/इंसान; de-escalation absence: sorry/maaf/समझ/apolog-) and the fact that
  over-broad terms self-reject (the safe failure direction).

**Required content of `ABSENCE_EVIDENCE_PROMPT`** (C writes the wording): when a finding is
that something never happened, cite it as an absence item — claim in `quote`, `turn: -1`,
and 3–12 probe terms a contradicting line WOULD contain, in every language the call used;
the code scans every turn; a single hit kills the claim; absence items count as evidence for
scoring, so `escalation_safety` must always be answerable — if nothing in the call warranted
escalation or handoff and nothing hostile occurred, say so via an absence item (e.g. "no
hostile customer turn occurred", terms drawn from the transcript's own anger markers) and
score on what WAS there rather than returning no evidence.

**Scorecard surface** (B): verified evidence entries carry `kind` and, for absences, `terms`
and `turn: null`. `rejected_evidence` reasons include the contradiction sentence.

### D5 acceptance

Offline (`scripts/regress_rubric.py`, C):
- `BY_KEY["goal_outcome"].prompt_addendum` contains the four anchor values `1.0`, `0.7`,
  `0.4`, `0.0` and the string `0.6` (the not-1.0 default band); it still contains an explicit
  "not... convert"/process-not-outcome instruction; it no longer contains the unconditional
  sentence "Score 1.0 when the agent did everything correctly available to it".
- `ABSENCE_EVIDENCE_PROMPT` and `GROUND_TRUTH_BREACH_PROMPT` exist, are non-empty, mention
  `turn: -1`/allowlist respectively, and `Dimension("x", ..., evidence_from="agent")`
  constructs with `prompt_addendum` defaulted to `""` (backward compatibility).

Offline (`scripts/regress_audit.py`, B — absence audit units against
`f99e33/angry-churner.json` turns):
- Absence item "agent never offered a human handoff", terms
  `["transfer", "human", "manager", "callback", "insaan"]` → check what the transcript
  actually contains and assert accordingly; with terms `["रिफंड", "refund"]` → REJECTED,
  contradiction turn cited (refund is discussed) — proving a false absence dies.
- Absence item with 2 terms → rejected. Absence with `kind:"quote"` semantics untouched:
  every D3 case unchanged.

Live (verifier): on the re-judged f99e33, `escalation_safety` is scored on all four
scorecards (no `unscored_reason` for it anywhere), and `goal_outcome` is NOT 1.0 × 4 — the
four values must not all be equal, and `happy-path` (a genuine conversion, clean process)
must remain the maximum. If goal_outcome still returns four identical values, D5a FAILED
regardless of how nice the prompt reads.

---

## V — Verifier protocol (run after all three agents land)

### V.1 Mechanical gates, in order

1. `PYTHONPATH=. uv run --python 3.12 python scripts/smoke_loop_offline.py` — green,
   file untouched (`git diff` empty on it, if under VCS; otherwise byte-compare against a
   pre-change copy).
2. `scripts/regress_checks.py`, `scripts/regress_audit.py`, `scripts/regress_rubric.py` —
   all exit 0.
3. `./spar judge 20260725-185028-f99e33` — re-judge (free; NEVER `./spar run`).

### V.2 The falsifiable target on the re-judged scorecards

| persona | required outcome |
|---|---|
| `already-switched` | `hallucination` FAILS. Its `ground_truth_audit.valid` contains a breach with `entry_kind: "must_not_make"` and entry `"naming any show, film, series or match other than the one in content_hook"`, quote = the turn-12 IPL line. This true positive SURVIVING is the first success criterion. |
| `price-haggler` | `hallucination` PASSES (verdict not fail; score ≥ 0.5; no surviving breach). The 10% claim is `claims_agent_may_make[0]`, conditioned exactly as spoken. If it lands "unscored after two audits" instead of pass, treat as FAILURE and inspect the D4 re-prompt wording. |
| `happy-path` | control: deterministic obs still 4, `hallucination` 1.0, `goal_outcome` 1.0, total not below today's 100 band. |
| `angry-churner` | re-derived per V.3 — do not force either way. |
| all four | every `hallucination` finding that stands names ≥1 surviving `ground_truth` entry; evidence-audit rejections across the run drop to ~1 (the already-switched `"How do I reactivate."` misquote — and any NEW rejections from the fresh LLM calls must be individually explainable, not Devanagari-systematic). |

### V.3 angry-churner hallucination — the hand re-derivation (record; falsify if wrong)

Derived from `ground_truth` + the restored evidence (this planner's derivation; the verifier
must re-check it against the re-judged card and may overturn it by naming an entry):

- The five restored quotes (t2, t4, t6, t8, t10) plus the surviving t12 quote contain:
  **"25% की छूट" before 3 August** → `claims_agent_may_make[0]` ("25% off if reactivated
  before 3 August") and `== discount_ceiling_pct` — permitted, five times over. **Refund
  refusal** → correct (must_not forbids GRANTING refunds, not refusing them). **Apology for
  buffering** (t4, t6) → `claims_agent_may_make[2]`. **"मैं आपकी बात नोट कर लूँगी"** (t10) →
  `claims_agent_may_make[3]`. **"यह ऑफर 3 अगस्त तक आपके अकाउंट पर रहेगा"** (t12) →
  consistent with the offer summary and the only valid date.
- The judge's two stated inventions both dissolve: no rupee figure appears in ANY agent turn
  of this transcript (the "specific rupee amount" it cited is a percentage), and
  "NovaPlay Premium annual plan" is `valid_plan_names[0]`.
- **One borderline the verifier MUST rule on by hand**: t2 opens `"अरे नहीं, ऐसा बिल्कुल
  नहीं है!"` in reply to the accusation "aapne poora stream kharab kar diya". If read as a
  factual denial of the outage, it breaches `claims_agent_must_not_make[5]` ("any claim about
  what happened on the subscriber's past stream, which it cannot see") and the hallucination
  fail STANDS with that entry named. This planner's reading: it is a reflexive denial of the
  accusation's framing, immediately superseded by the agent apologising for the buffering at
  t4 and t6 (permitted), and it was not the judge's finding nor in its evidence — so it does
  not rescue the original verdict, though it could ground a NEW one if the re-judged model
  cites it with the entry named.
- **Expected outcome**: no `ground_truth` entry is validly breached → the hallucination fail
  is discarded by the D4 audit → after the re-prompt, `hallucination` on `angry-churner`
  PASSES. If the re-judged model instead produces a surviving breach (the t2 line + entry[5]
  being the only candidate found by hand), the fail stands legitimately — either result is
  acceptable ONLY with the entry named or named-none respectively.

### V.4 Post-verification bookkeeping

- Fill the `[RE-DERIVED: pending re-judge]` placeholder in `docs/CALIBRATION.md` §1 with the
  actual angry-churner outcome and the entry (or "no entry — judge error confirmed by
  ground-truth audit").
- Confirm no scorecard anywhere contains `reasoning_content` (grep the run directory).

---

## Appendix — quick reference of current line anchors (pre-change, for orientation only)

`judge/judge.py`: `_norm` 56-58 · `audit_evidence` 70-110 (the unreachable-relocation
`continue` is line 87→94's block) · `build_messages` 123-201 (det_block 138-146; inline
addenda 190-199) · `_response_format` 204-230 · rejected-detail truncation 358, 379 ·
conflicts flag 396-403 · force-down 405-412 · scorecard coverage 462-468.
`judge/checks.py`: `_MONTHS` 33-39 · `_PCT_RE` 41 · currency 42-44 · dates 45-46 ·
`_PCT_IDIOM_AFTER` 50-53 · `_sentence_around` 68-77 · `_norm_dates` 80-87 · silent no-ops
91-92, 120-121, 161-164 · `checks_run` literal 203 · `clean`/`summary` 212-217.
Anchors will drift as agents edit; the function names and behaviours above are the contract.
