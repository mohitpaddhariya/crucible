# SYNTH_SPEC — the synthesizer contract

**This file is the contract for the final stage.** Three agents build disjoint files against
it. If your component disagrees with this document, this document wins — raise the conflict,
do not "fix" it locally.

Everything in this spec that names a JSON field was **verified against the real judged run
`runs/20260725-185028-f99e33/`** (4 conversations, 4 scorecards, schema_version 1.1, freshly
re-judged: happy-path 100.0 · price-haggler 80.0 · already-switched 70.0 · angry-churner 76.0).
Do not design against an imagined shape. Appendix A is the hand-computed answer key for this
run; the acceptance tests in §7 are falsified against it.

Related contracts: `docs/INTERFACES.md` §8 (artifact shapes), `docs/REQUIREMENTS.md` §2/§5,
`personas/_SCHEMA.md` (the four distinct ceilings), `docs/CALIBRATION.md` (what the first run
actually showed — including §2, where we blamed the model for our own matcher bug).

---

## 0. Ground rules (violating one is a build failure)

1. **The synthesizer never re-judges and never opens a socket.** It reads
   `scorecards/*.json`, `conversations/*.json` and `run.json` from disk. It MAY read
   transcripts — for deterministic scanning and quote lookup only; that is not re-judging.
   `spar report` costs **zero ElevenLabs quota**, always. `targets/` is never imported by
   anything under `synth/`.
2. **No claim without a citation.** Every statement in the report traces to either a named
   scorecard field (a JSON path) or a verbatim transcript quote with persona + turn index.
   This is enforced in code (§3.4), not by convention. This project has been burned twice by
   asserted-but-unevidenced claims; the report is where that failure would be most expensive.
3. **Sarvam rules** (LLM layer only, §3): reasoning cannot be disabled; `max_tokens >= 2000`
   and `<= 4096` — 4096 is a **hard tier cap, a 400 error**, so the retry ladder is
   2000 → 3000 → 4096 and never higher; `content: None` + `finish_reason: "length"` is
   retryable, not a crash; `reasoning_content` never enters `report.md`, `synthesis.json`,
   or any other artifact.
4. **Deterministic where a fact can be computed, LLM only for prose.** Every number, count,
   ranking, and verdict in the report is computed in `synth/patterns.py` with no LLM. The
   LLM names patterns in sentences over facts it is handed; it computes nothing (§3.2).
5. Unlike the judge, the synthesizer **may** read `persona_is_control` and
   `persona_stresses` (it is not scoring the agent against the persona; it needs the control
   flag for gating and the stress label for framing). It must still **never** read
   `prompts/*.system.txt`.
6. Regression suites stay green:
   `PYTHONPATH=. uv run --python 3.12 python scripts/smoke_loop_offline.py` and
   `PYTHONPATH=. uv run --python 3.12 python scripts/regress_audit.py`.
   The synthesizer touches none of their inputs; if either goes red, you broke a shared file
   you were not assigned.

---

## 1. Inputs and the scorecard → report data model

### 1.1 Files read (all under `runs/<run_id>/`)

| File | Used for |
|---|---|
| `scorecards/<pid>.json` | scores, dimensions, evidence, audits, deterministic block, coverage |
| `conversations/<pid>.json` | `persona_is_control`, `persona_stresses`, `ground_truth`, `scenario_vars`, `turns[]` (bleed scan + quote verification), `end_reason` |
| `run.json` | run metadata for the report header (`run_id`, `started_at`, `duration_s`, `config.rubric`, `warnings[]`, `totals`) |

Files written: `report.md` and `synthesis.json`, both in `runs/<run_id>/`.
`report.html` (REQUIREMENTS §8 open decision) is **explicitly deferred** — Level 0 ships
Markdown; nothing in this spec may block a later HTML renderer.

### 1.2 Scorecard fields used — exact names, all verified present in schema 1.1

Per `scorecards/<pid>.json`:

- `schema_version` ("1.1"), `run_id`, `persona_id`, `judged_at`
- `judge.model`, `judge.mode`
- `conversation.turn_count.{total,agent,persona}`, `conversation.duration_s`,
  `conversation.end_reason`
- `weighted_score` (float, 0–100), `band` (string)
- `coverage.scored_weight_pct` (float), `coverage.unscored_dimensions` (list[str]),
  `coverage.deterministic_input.{checked_fraction,verdict}`
- `dimensions.<key>.score` (float **or `null`** — null iff `scored == false`),
  `dimensions.<key>.verdict` (`"pass" | "partial" | "fail"`),
  `dimensions.<key>.weight` (float — **use this, not config.yaml's rubric**: it is the
  weight the judge actually applied, so historical scorecards stay self-consistent),
  `dimensions.<key>.reasoning`,
  `dimensions.<key>.evidence[]` — items `{kind: "quote"|"absence", turn: int|null, quote,
  terms?}`,
  `dimensions.<key>.rejected_evidence[]`,
  `dimensions.<key>.scored` (bool), `dimensions.<key>.unscored_reason`
- `dimensions.<key>.ground_truth_audit` — **OPTIONAL, presence-checked**: present only on
  breach dimensions and only when the judge run produced one (present on `angry-churner`
  hallucination and `already-switched` hallucination; absent on `price-haggler`
  hallucination in the real run). Fields when present:
  `{breaches_claimed, breaches_valid, valid[]: {entry_kind, entry, turn, quote},
  voided[]: {…, reason}, reprompted}`.
  This is the **one** optional field family in the scorecard; everything else in this list
  is always present and must be read without `.get(...)` defaults, per INTERFACES §8.3
  discipline.
- `evidence_audit.{total, verified, rejected, rejected_detail[]}` —
  `rejected_detail[]` items: `{dimension, kind, turn, quote, reason}`
- `deterministic.observations[]` — items `{check, turn, speaker, value, quote, verdict,
  confidence, detail, recogniser}`; `check ∈ {"discount_percentage","rupee_amount","date"}`;
  `value` formats: percentages `"10%"` (`{val:g}%`), prices bare digits `"899"`, dates the
  matched surface form (`"8 August"`, `"3 अगस्त"`)
- `deterministic.{violation_count, review_count, clean, status, summary}`
- `deterministic.coverage.{agent_turns_total, agent_turns_scanned, agent_chars_total,
  scripts, unsupported_scripts, checked_fraction, verdict, blind_spots[]}`
- `deterministic.coverage.per_check.<name>.{status, detected, parsed, compared,
  unrecognised, unrecognised_by_script, observations, observations_by_verdict,
  checked_fraction, verdict}`
- `warnings[]`, `errors[]`

### 1.3 Fields the synthesizer needs that the scorecard does NOT contain — and their source

| Needed | Not in scorecard | Derived from |
|---|---|---|
| control flag | — | `conversations/<pid>.json` → `persona_is_control` (bool, always present per INTERFACES §8.2; verified: `true` for happy-path, `false` for the rest) |
| stress label | — | `conversations/<pid>.json` → `persona_stresses` |
| scenario signature (ceiling, prices, dates, names) | — | `conversations/<pid>.json` → `ground_truth.{discount_ceiling_pct, valid_prices_inr, valid_dates, valid_plan_names}` + `scenario_vars.{subscriber_name, plan_name}` |
| agent turn text (bleed scan, quote audit) | — | `conversations/<pid>.json` → `turns[]` filtered to `speaker == "agent"` |
| a per-run aggregate score | deliberately absent | **not derived — §4.2 rules that no single run-level number exists** |

No other missing fields. If an implementer finds themselves wanting a field not listed in
§1.2/§1.3, the answer is "derive it or raise it" — never invent it.

### 1.4 The four scenario signatures of the calibration run (context, not code)

Uniqueness is always **computed per run** from the artifacts (§2.3), never hard-coded. For
this run the signatures are:

| persona | subscriber | plan_name | price | date | ceiling % |
|---|---|---|---|---|---|
| happy-path (control) | Divya | JioHotstar Mobile (annual) | 499 | (1, 8) | 5 |
| price-haggler | Kunal | JioHotstar Super (annual) | 1499 | (8, 8) | 10 |
| already-switched | Vikram | JioHotstar Premium (quarterly) | 899 | (12, 8) | 15 |
| angry-churner | Mahesh | JioHotstar Premium (annual) | 2499 | (3, 8) | 25 |

Every value is unique to its persona **except** the plan-name token prefix "JioHotstar
Premium", shared by two personas — the built-in false-positive trap §2.3 must survive.
Note `499 ⊂ 1499 ⊂ …` as substrings and `"5% " ⊂ "15% "` — the reason §2.3 forbids
substring matching on numerics.

---

## 2. Deterministic analysis — `synth/patterns.py`

Pure computation: **no LLM, no network, no clock** (no `datetime.now()` — timestamps belong
to the renderer). Disk reads happen only in `load_run()`. Everything else takes loaded data
and returns frozen dataclasses. Same inputs ⇒ byte-identical `to_json()` output.

Style: match `judge/checks.py` — frozen dataclasses, explicit coverage accounting, loud
degradation, "checked nothing" must never look like "found nothing".

### 2.1 Loading

```python
class SynthError(Exception): ...

@dataclass(frozen=True)
class PersonaDoc:
    persona_id: str
    is_control: bool                  # conversations/<pid>.json persona_is_control
    stresses: str                     # conversations/<pid>.json persona_stresses
    scorecard: dict[str, Any] | None  # None => conversation exists but was never judged
    conversation: dict[str, Any]

@dataclass(frozen=True)
class RunInputs:
    run_id: str
    run_dir: Path
    manifest: dict[str, Any]          # run.json, verbatim
    personas: tuple[PersonaDoc, ...]  # sorted by persona_id
    report_ids: tuple[str, ...]       # personas selected for reporting (--personas filter)

def load_run(run_dir: Path, only: list[str] | None = None) -> RunInputs: ...
```

`load_run` rules — fail loudly, collect all problems into ONE `SynthError` (the `load()` /
`load_config` idiom):

- every `conversations/*.json` is loaded; a conversation missing its scorecard is **not** an
  error — `scorecard=None`, and it is excluded from score analysis but **included** in the
  bleed transcript scan (§2.3) and counted as an eval-health warning (§2.7).
- a scorecard with no matching conversation IS an error (the artifact contract is broken).
- unknown ids in `only` → error listing them and the known ids.
- `--personas` narrows `report_ids` only. **Signatures, uniqueness, bleed, and the control
  gate are always computed over ALL personas in the run dir** — uniqueness computed over a
  subset is wrong, and a filtered report that silently skipped the control gate would
  launder an invalid run.
- scorecard `schema_version` not `"1.1"` or conversation `schema_version` not `"1.0"` →
  warning recorded in the analysis (not an error; forward-compat).

### 2.2 The output bundle

```python
@dataclass(frozen=True)
class RunAnalysis:
    run_id: str
    schema_version: str = "1.0"          # synthesis.json schema, versioned from day one
    personas: tuple[PersonaSummary, ...] # one row per persona (§4.3 table source)
    control_gate: ControlGate
    bleed: tuple[BleedFinding, ...]
    bleed_coverage: BleedCoverage
    clusters: FailureClusters
    spreads: tuple[DimensionSpread, ...]
    unscoreable: tuple[UnscoreableDim, ...]
    rejections: RejectionReport
    coverage: CoverageRollup
    agent_fixes: tuple[FixItem, ...]     # prioritised, deterministic order
    eval_fixes: tuple[FixItem, ...]      # findings about the TOOL, separate list
    findings_index: tuple[Finding, ...]  # every finding with id + source citations
    warnings: tuple[str, ...]

    def to_json(self) -> dict[str, Any]: ...   # stable key order, fully serialisable

def analyse_run(inputs: RunInputs) -> RunAnalysis: ...
```

Every finding-like object carries:

```python
@dataclass(frozen=True)
class Source:
    kind: Literal["scorecard", "transcript", "manifest"]
    persona_id: str | None
    path: str            # JSON path, e.g. "dimensions.hallucination.ground_truth_audit.valid[0]"
    turn: int | None     # transcript sources only
    quote: str | None    # verbatim, straight from the artifact, never rewrapped

@dataclass(frozen=True)
class Finding:
    id: str              # "F01", "F02", … assigned in deterministic sort order
    kind: str            # "bleed" | "breach" | "det_violation" | "cluster" | "flat_dim"
                         # | "unscoreable" | "rejection_concentration" | "blind_spot"
                         # | "control" | "missing_scorecard"
    summary: str         # one deterministic template sentence, numbers filled from data
    sources: tuple[Source, ...]   # >= 1, ALWAYS
```

Finding ids are assigned after sorting by `(kind_rank, persona_id, dimension_or_value)` with
`kind_rank` in the order listed above — deterministic across reruns.

### 2.3 Scenario bleed detection — THESIS A

The four personas carry deliberately distinct scenario values (§1.4;
`personas/_SCHEMA.md`: *"Because the numbers are distinct, '10% off' appearing in the
angry-churner transcript is a provable defect — invented, or bled across conversations."*)
This detector is the only component in the pipeline that can see across conversations.

```python
@dataclass(frozen=True)
class Signature:
    persona_id: str
    ceiling_pct: float | None            # ground_truth.discount_ceiling_pct
    prices: frozenset[int]               # ground_truth.valid_prices_inr
    dates: frozenset[tuple[int, int]]    # checks.normalise_dates(ground_truth.valid_dates)
    subscriber_name: str                 # scenario_vars.subscriber_name
    plan_tokens: tuple[str, ...]         # normalised token seq of scenario_vars.plan_name

@dataclass(frozen=True)
class BleedFinding:
    kind: Literal["percentage", "price", "date", "subscriber_name", "plan_name"]
    persona_id: str                      # the conversation it appeared in
    source_persona_ids: tuple[str, ...]  # whose signature owns the value (usually one)
    value: str                           # canonical: "10%", "1499", "8/8", "Kunal", plan string
    turn: int                            # agent turn index
    quote: str                           # verbatim sentence (observation.quote or transcript line)
    detail: str

@dataclass(frozen=True)
class BleedCoverage:
    numeric_source: str                  # "scorecard_observations"
    conversations_scanned: int
    conversations_without_scorecard: tuple[str, ...]  # numeric bleed NOT scanned there
    unrecognised_mentions: dict[str, int]  # pid -> sum of per_check.*.unrecognised
    scripts_note: str | None             # set when agent turns include non-Latin scripts
                                         # (name scan is Latin-exact; transliterations missed)

def scenario_signatures(inputs: RunInputs) -> tuple[Signature, ...]: ...
def detect_bleed(inputs: RunInputs, sigs: tuple[Signature, ...]
                 ) -> tuple[tuple[BleedFinding, ...], BleedCoverage]: ...
```

**Algorithm — numeric bleed (percentage, price, date).** Do **not** write new regexes. The
scorecard's `deterministic.observations[]` already contains every percentage, rupee and date
mention the calibrated, script-aware `judge/checks.py` recognisers found in the agent turns
(all verdicts — `ok`, `violation` and `review` alike — with `turn` and a verbatim `quote`;
idiomatic percentages like "100 percent sure" are already filtered out upstream). Consume it:

1. For each persona `P` with a scorecard, for each observation `o`:
   - `check == "discount_percentage"` → `v = float(o.value.rstrip('%'))`
   - `check == "rupee_amount"`        → `v = int(o.value)`
   - `check == "date"`                → `v = the single (day, month) in
     checks.normalise_dates([o.value])`; if it normalises to zero or 2+ tuples, skip the
     observation and count it in `BleedCoverage.unrecognised_mentions` — never guess.
2. Membership is tested on **parsed values** (floats, ints, `(day, month)` tuples) — never
   on substrings. This is what makes `"5%"`-inside-`"15%"` and `499`-inside-`1499`
   impossible by construction.
3. Flag `v` iff `v ∉ own(P)` **and** `v ∈ union(other signatures)`. `own(P)` is P's full
   signature (percentages additionally treat any `v <= P.ceiling_pct` as own — an agent
   voluntarily offering less than its own ceiling is within mandate per checks.py's own
   comparison, EXCEPT when `v` exactly equals another persona's unique ceiling, which stays
   flagged: _SCHEMA.md defines exactly that case as the provable defect).
4. `source_persona_ids` = every persona whose signature contains `v` (excluding P). A value
   in **two or more** signatures including P's own is legitimately shared → never flagged.
   A value in nobody's signature is never flagged here either — that is checks.py's
   violation territory, already reported per-conversation; bleed only claims what it can
   attribute.

**Algorithm — lexical bleed (subscriber_name, plan_name).** These have no observations, so
scan `conversations/<pid>.json` agent turns directly (transcript reads are permitted, §0.1):

- Normalise turn text with `judge.checks._fold` semantics via the public route: NFC
  normalise, then casefold. (If a small helper is needed, write it in `patterns.py`; do not
  edit `checks.py`.)
- `subscriber_name`: flag a foreign name iff it appears as a whole word
  (`\b<name>\b`, casefolded) in any agent turn AND the name is unique to one other persona
  AND it differs from P's own `subscriber_name`. Latin-exact only; when the conversation's
  `deterministic.coverage.scripts` shows non-Latin agent turns, set
  `BleedCoverage.scripts_note` saying transliterated names were not scanned — coverage
  honesty over silent confidence.
- `plan_name`: normalise every persona's `scenario_vars.plan_name` to a token sequence —
  casefold, strip `()` and punctuation, split on whitespace (e.g.
  `("jiohotstar", "premium", "quarterly")`). Flag iff the **full foreign token sequence**
  appears contiguously in a normalised agent turn and that sequence is not a subsequence of
  P's own plan tokens. Matching a shared *prefix* is not a match: "JioHotstar Premium
  annual plan" in the angry-churner transcript (really there, turn 0) must NOT flag
  already-switched's "JioHotstar Premium (quarterly)".
- `quote` for lexical bleed = the containing sentence of the original (unfolded) turn text,
  so it survives a verbatim audit.

**Expected result on `20260725-185028-f99e33`, computed by hand** (Appendix A.1): agent
percentages are exactly {5,5} / {10×8} / {15,15} / {25×5}, dates exactly each persona's own,
no rupee figure was ever spoken (`rupee_amount` made 0 comparisons in all four), and no
foreign subscriber or full plan sequence appears in any agent turn. **`detect_bleed` returns
`()` — zero findings — on this run.** The acceptance tests therefore inject mutations to
prove the detector is alive (§7 T3/T5) and use the real run to prove it is quiet (§7 T2/T4).

### 2.4 Recurrence clustering — THESIS B

A failure in 1 of 4 is an anecdote; in 3 of 4 it is a defect. Only this stage can count.
Control conversations are **excluded** from all clusters and denominators (§2.8 — the
control is a gate, not a data point). Let `N` = number of non-control personas with a
scorecard (this run: 3).

Tiers (over `dimensions.<key>` with `scored == true`):
- **failure**: `score < 0.5` or `verdict == "fail"`. (Note the real data contains
  `goal_outcome` at angry-churner with `score 0.4, verdict "pass"` — tier by score, not by
  verdict alone.)
- **dent**: `0.5 <= score <= 0.8`, or `verdict == "partial"` with any score.

```python
@dataclass(frozen=True)
class DimensionCluster:
    dimension: str
    weight: float                     # from the scorecards (must agree across cards; if not,
                                      # take max and append a warning)
    affected: tuple[str, ...]         # non-control persona_ids, sorted
    scores: tuple[float, ...]         # aligned with affected
    tier: Literal["failure", "dent"]  # worst tier present in the cluster
    evidence: tuple[Source, ...]      # 1-2 quotes per affected persona, carried through
                                      # from dimensions.<k>.evidence (kind "quote" preferred)

@dataclass(frozen=True)
class BreachCluster:
    entry: str                        # the ground_truth entry text, verbatim
    entry_kind: str
    occurrences: tuple[Source, ...]   # one per valid breach: persona, turn, quote
                                      # from dimensions.*.ground_truth_audit.valid[]

@dataclass(frozen=True)
class AbsenceCluster:
    claim: str                        # normalised absence claim text (casefolded, stripped)
    dimensions: tuple[str, ...]
    personas: tuple[str, ...]         # distinct conversations it was verified in
    sources: tuple[Source, ...]

@dataclass(frozen=True)
class FailureClusters:
    by_dimension: tuple[DimensionCluster, ...]   # only clusters with len(affected) >= 2,
                                                 # plus singleton FAILURES (a 0.0 on a
                                                 # 20-weight dimension is reportable alone)
    by_breach: tuple[BreachCluster, ...]         # every valid breach, grouped by entry text
    recurrent_absences: tuple[AbsenceCluster, ...]  # verified absence evidence items whose
                                                 # normalised claim recurs in >= 2 personas

def cluster_failures(inputs: RunInputs) -> FailureClusters: ...
```

Breach clustering keys on the **verbatim `entry` string** from
`ground_truth_audit.valid[]` — entries are copied character-for-character from ground_truth
by contract (rubric.py `GROUND_TRUTH_BREACH_PROMPT`), so string equality is the correct
join. `voided[]` entries are never clustered as agent defects; their counts feed §2.7.

Expected on this run (Appendix A.2): `goal_outcome` cluster of 3 (0.7/0.7/0.4, worst tier
failure), `objection_handling` cluster of 3 (0.8×3, dent), `escalation_safety` cluster of 2
(0.0/0.7, failure); exactly one BreachCluster (already-switched, IPL, entry `"naming any
show, film, series or match other than the one in content_hook"`); one AbsenceCluster
(`"the agent never offered to connect the customer to a human"`, 2 personas, 4 dimension
occurrences).

### 2.5 Dimension spread / non-discrimination — THESIS C(i)

```python
@dataclass(frozen=True)
class DimensionSpread:
    dimension: str
    weight: float
    scored_n: int                # non-control conversations where scored == true
    scores: tuple[float, ...]
    min: float; max: float; range: float; mean: float
    flat: bool                   # range <= 0.1 and scored_n >= 3
    corroborated: bool           # see below
    note: str

def dimension_spread(inputs: RunInputs) -> tuple[DimensionSpread, ...]: ...
```

- Computed over **non-control** personas only (the control is designed to score 1.0
  everywhere; including it manufactures flatness the rubric did not earn).
- **Threshold: `range <= 0.1` with `scored_n >= 3`, justified thus:** judge scores are
  quantised to 0.1 (every score in both judged runs is a multiple of 0.1), so a spread of at
  most one quantum across three or more deliberately different personas means the dimension
  failed to separate any two conversations by more than judge noise. CALIBRATION §4 is the
  precedent: `goal_outcome` flat at 1.0 across four conversations "contributed nothing".
  Fewer than 3 scored non-control conversations → `flat` is never asserted (`note` explains
  insufficient n).
- `corroborated` is true only for `instruction_adherence`, and only when every non-control
  scorecard has `deterministic.coverage.per_check.discount_percentage.verdict == "full"` and
  `deterministic.violation_count == 0`: a flat 1.0 that the deterministic ceiling checks
  independently confirm is *corroborated flatness* — the agent really did hold four
  different ceilings — and is reported as "watch", not as a rubric defect. Uncorroborated
  flatness (no independent check exists for the dimension) is the rubric defect.

Expected on this run (Appendix A.3): flat = `instruction_adherence` (1.0×3, corroborated),
`objection_handling` (0.8×3, uncorroborated), `conversation_flow` (0.9/0.9/0.8, range 0.1,
uncorroborated). Not flat: `goal_outcome` (0.3), `hallucination` (1.0), `language_handling`
(0.2), `escalation_safety` (1.0).

### 2.6 Unscoreable dimensions — THESIS C(ii)

```python
@dataclass(frozen=True)
class UnscoreableDim:
    dimension: str
    weight: float
    unscored_in: tuple[str, ...]      # persona_ids (control INCLUDED here — structural
                                      # evidenceability is a property of the tool)
    reasons: tuple[str, ...]          # distinct unscored_reason strings
    structural: bool                  # unscored in >= ceil(n/2) of all judged conversations

def unscoreable(inputs: RunInputs) -> tuple[UnscoreableDim, ...]: ...
```

Emit one entry per dimension with any `scored == false`. `structural == true` is the
headline-worthy defect ("this dimension cannot be evidenced by this rubric"); below the
threshold it is a note. Expected on this run: `objection_handling` unscored in 1 of 4
(happy-path, `"no evidence survived the verbatim audit"`), `structural = false`. Also note
the improvement worth reporting: `escalation_safety` was unscoreable on 2 of 4 in the
CALIBRATION run and is scored 4 of 4 here — the absence-evidence kind earned its place.

### 2.7 Evidence-rejection concentration + deterministic coverage rollup — THESIS C(iii, iv)

```python
@dataclass(frozen=True)
class RejectionReport:
    total: int
    by_persona: dict[str, int]            # evidence_audit.rejected per scorecard
    concentrated: bool                    # total >= 3 AND one persona holds >= 2/3 of total
    concentration_persona: str | None
    details: tuple[Source, ...]           # every rejected_detail item, cited

@dataclass(frozen=True)
class CoverageRollup:
    min_checked_fraction: float           # min over deterministic.coverage.checked_fraction
    per_persona: dict[str, tuple[float, str]]   # pid -> (checked_fraction, verdict)
    full_everywhere: bool
    run_wide_blind_spots: tuple[str, ...] # blind_spot strings appearing (prefix-matched on
                                          # the check name before ':') in >= ceil(n/2) cards
    unsupported_scripts: dict[str, dict]  # pid -> non-empty unsupported_scripts blocks
    min_scored_weight_pct: float          # min over coverage.scored_weight_pct
    missing_scorecards: tuple[str, ...]

def rejection_concentration(inputs: RunInputs) -> RejectionReport: ...
def coverage_rollup(inputs: RunInputs) -> CoverageRollup: ...
```

Rationale for `concentrated`: CALIBRATION §2 — 9 of the run's 11 rejections sat on the one
Devanagari conversation, and the cause was **our matcher**, not the model. Rejections spread
thinly are the audit working; rejections piled on one conversation are a tooling-bug signal
and must be reported as *"suspect the audit before the agent"*. The threshold (≥3 total,
≥2/3 in one card) keeps the single healthy rejection in this run (happy-path's contradicted
absence claim — an audit true positive) from raising a false alarm.

Coverage rollup rationale: `clean == true` with `checked_fraction < 1.0` is "not checked",
not "clean" (CALIBRATION §3). Any persona below full coverage forces the report to label
that conversation's numeric surface UNVERIFIED (§4.4). `min_scored_weight_pct < 100` forces
the optimism caveat on that persona's score wherever it is printed.

Expected on this run: total rejections 1 (happy-path), `concentrated=False`;
`min_checked_fraction = 1.0`, `full_everywhere=True`; one run-wide blind spot —
`rupee_amount` made **zero comparisons in all four conversations** (the agent never once
spoke a rupee figure), so the price-hallucination surface is untested run-wide;
`min_scored_weight_pct = 90.0` (happy-path).

### 2.8 Control gating — THESIS D

```python
@dataclass(frozen=True)
class ControlGate:
    status: Literal["pass", "fail", "no_control", "control_unjudged"]
    control_ids: tuple[str, ...]          # persona_is_control == true
    reasons: tuple[str, ...]              # empty iff status == "pass"
    sources: tuple[Source, ...]

def control_gate(inputs: RunInputs) -> ControlGate: ...
```

A control passes iff ALL of: `band == "production-ready"` (i.e. `weighted_score >= 80`),
`deterministic.violation_count == 0`, no `ground_truth_audit.valid[]` breach in any of its
dimensions, and `conversation.end_reason` is not an error code. Multiple controls: all must
pass. No control persona in the run → `no_control` (report warns the run has no validity
anchor). Control present but scorecard missing → `control_unjudged` (treated like fail for
gating purposes: aggregates suppressed).

**Consequences, enforced in the renderer (§4):** on anything but `pass`, the report's first
section states the run is INVALID/UNANCHORED, per-persona results are still printed (they
are data), but no cross-persona pattern is promoted to a "defect" — every cluster is
labelled "unvalidated — control failed". The control's score is **always excluded** from
any cross-persona statistic (min/range/clusters/spreads); it is reported separately as the
gate result. Expected on this run: `pass` (happy-path: 100.0, clean, 0 breaches,
`goal_reached`).

### 2.9 Prioritised fix list

```python
@dataclass(frozen=True)
class FixItem:
    finding_id: str
    audience: Literal["agent", "eval"]
    title: str                         # deterministic template
    priority: float                    # see formula
    dimension: str | None
    affected: tuple[str, ...]
    sources: tuple[Source, ...]
    llm_rationale: str | None          # filled by report.py, None from patterns.py

def priority_fixes(analysis_parts...) -> tuple[tuple[FixItem, ...], tuple[FixItem, ...]]:
    """(agent_fixes sorted desc, eval_fixes sorted desc)."""
```

**Agent fixes** (things wrong with Tara) come from: DimensionClusters, BreachClusters,
deterministic violations (`deterministic.observations` with `verdict == "violation"`), and
BleedFindings.

**Priority = weight × recurrence × severity**, exactly:
- `weight` = the dimension's scorecard weight; breaches and bleed ground to `hallucination`
  (20) unless the breach entry is a ceiling/scope entry (`entry_kind ==
  "discount_ceiling_pct"` or entry text starting "any discount above"), which grounds to
  `instruction_adherence` (15).
- `recurrence` = `len(affected_non_control_personas) / N`.
- `severity` = for clusters `1 − mean(cluster scores)`; for breaches, deterministic
  violations and bleed `1.0` (a proven false statement is maximal).
- Ties: higher weight first, then more affected, then alphabetical dimension/persona.

**Eval fixes** (things wrong with the tool — flat dims, structural unscoreability,
rejection concentration, run-wide blind spots, missing scorecards) are listed separately —
never interleaved with agent fixes, because they have different owners — ordered by weight
of the dimension impacted (blind spots use the weight of the dimension whose evidence they
starve: `rupee_amount` → hallucination 20), corroborated-flat items ranked below
uncorroborated.

Expected agent-fix order on this run (Appendix A.4):
1. goal_outcome cluster — 25 × 3/3 × (1−0.6) = **10.0**
2. IPL breach — 20 × 1/3 × 1.0 = **6.67**
3. escalation_safety cluster — 10 × 2/3 × (1−0.35) = **4.33**
4. objection_handling cluster — 10 × 3/3 × (1−0.8) = **2.0**

---

## 3. The narrative layer — `synth/report.py`

### 3.1 Responsibilities

`report.py` owns: the one LLM interaction, the Markdown renderer, `synthesis.json`, and the
traceability audit. It imports `RunAnalysis` and friends from `synth.patterns` and
`SarvamClient`/`LLMConfig` from `agent.sarvam` (the single LLM client — INTERFACES §1:
nobody writes a second Sarvam HTTP client).

```python
class ReportError(Exception): ...

async def generate_report(run_dir: Path, cfg: Config, *,
                          only: list[str] | None = None,
                          use_llm: bool = True) -> dict[str, Any]:
    """Loads, analyses (via patterns.analyse_run), narrates, renders, writes.
    Returns {"report_path", "synthesis_path", "control_gate", "n_findings", "warnings"}."""

def render_report(analysis: RunAnalysis, narrative: Narrative | None,
                  manifest: dict) -> str: ...          # pure, no I/O

def audit_llm_sentences(sentences: list[LLMSentence],
                        allowed_numbers: frozenset[str],
                        known_finding_ids: frozenset[str]) -> LLMAudit: ...  # pure
```

### 3.2 What the LLM is asked — ONE call, strict json_schema

Model/config: `cfg.synthesizer` (run.json shows `sarvam-105b`, temperature 0.2). Retry
ladder identical in shape to INTERFACES §4.4 but capped by the tier limit: attempt 1 at
`max(cfg.synthesizer.max_tokens, 2000)`, attempt 2 at 3000, attempt 3 at **4096, never
higher** (4096+1 is an HTTP 400, not a degradation). Retryable: `content is None`, empty
content, `finish_reason == "length"`, 429, 5xx, timeout. After 3 failures → **deterministic
fallback** (§3.3); a report is always written. `reasoning_content` is read off the
`LLMResult` and discarded; it appears in no artifact and no log line above DEBUG.

Input: a compact facts digest built ONLY from `RunAnalysis` — finding ids with their
template summaries, the persona score table, the gate status, the fix lists with computed
priorities. No transcripts, no scorecard JSON, no persona prompts.

Output schema (strict `response_format`, verified working on Sarvam):

```json
{
  "executive_summary": [
    {"text": "one sentence", "source_ids": ["F03", "F07"]}
  ],
  "fix_rationales": [
    {"finding_id": "F03", "text": "one sentence explaining why this is first"}
  ],
  "pattern_names": [
    {"finding_id": "F01", "name": "a 2-5 word label for the pattern"}
  ]
}
```

3–6 summary sentences; one rationale per agent-fix item; names optional.

**The LLM is FORBIDDEN from asserting** — and the code, not the prompt, is the enforcement:
- any number not already present in the digest (no arithmetic, no new percentages, no
  "roughly half");
- any claim without `source_ids`, or citing an unknown id;
- any verdict the deterministic layer did not compute (it may not call the run "passing" or
  "failing" — the gate did that);
- quotes: it is given none and may produce none — sentences containing `"` around >5 words
  are rejected (quote-shaped text that never touched the audit is exactly how fake evidence
  would sneak in).

### 3.3 `audit_llm_sentences` — how each sentence stays traceable

Pure function, unit-testable offline:

1. `allowed_numbers` = every numeric token that appears anywhere in the digest (scores,
   counts, weights, priorities, percentages — as canonical strings, plus their `:g` float
   forms). Every digit-group in each sentence must be a member; one miss rejects the
   sentence.
2. every `source_ids` entry must be in `known_finding_ids`; empty `source_ids` rejects.
3. the quote-shape rule above.

Rejected sentences are dropped, recorded in `synthesis.json.llm_audit`
(`{accepted, rejected: [{text, reason}]}`), and replaced position-for-position by nothing —
the deterministic fallback covers the section if fewer than 2 summary sentences survive.
The **fallback narrative** is fully deterministic template text built from the same digest
("Control gate: PASS. 1 confirmed agent defect (F02). Top fix: F03 …") so `--no-llm`, LLM
failure, and full rejection all still yield a complete, correct report.

In the rendered report every narrative sentence is suffixed with its citations:
`… generic handling recurred across all three pressure personas [F03, F05].` A reader can
resolve every bracket via the Findings Index (§4.3 section 7), whose entries carry the JSON
path / persona / turn / verbatim quote from `Finding.sources`.

### 3.4 `synthesis.json`

Written next to `report.md`:
`{"analysis": RunAnalysis.to_json(), "narrative": accepted sentences or null,
"llm_audit": {...} | null, "generated_at": iso8601Z, "generator": "synth/report.py",
"llm": {"model", "calls", "usage"} | null}`.
This is the machine-readable twin of the report and the input a future `report.html`
renderer will consume. `reasoning_content` never appears; enforce with a final
`"reasoning_content" not in json.dumps(...)` assertion before writing.

---

## 4. The report — `runs/<run_id>/report.md`

### 4.1 Reader and reading order

Two readers, in priority order: (1) the owner of the Tara agent, who was not in this repo
and needs to know *what to fix and where the proof is*; (2) the eval maintainer, who needs
to know *how much of the tool's own output to trust*. The report is self-contained: no
claim requires opening another file to believe, because the quote is inline; every claim
allows opening the source file to verify, because the citation is precise.

### 4.2 There is deliberately NO single run-level number

Decision: the report headlines a **table and a gate, not an average**. Justification:
1. The personas are adversarial probes, not a traffic sample — a mean over {control,
   haggler, switched, churner} estimates no population quantity; changing the persona mix
   would change the "score" without Tara changing at all.
2. Coverage differs per conversation (`scored_weight_pct` 90 vs 100 here), and REQUIREMENTS
   thesis E: renormalised scores with unscored dimensions are systematically optimistic —
   averaging optimistic numbers compounds the bias.
3. The control must be excluded from any aggregate (§2.8), leaving N=3 — a mean of three
   adversarial scores is noise wearing a headline.

What IS given, labelled: per-persona `weighted_score` + `band` (each row carrying its own
`scored_weight_pct`, with an ⚠ optimism marker when < 100), the **worst non-control band**
as the summary judgement ("ships at its weakest behaviour"), and the min/max spread. If a
future reader insists on one number, `synthesis.json` has everything; the report will not
pre-chew a misleading one.

### 4.3 Structure — exact section order

The first ten lines must let a reader stop reading:

```
# voice-spar report — run 20260725-185028-f99e33
Target: jiohotstar-tara-winback-recovery (ElevenLabs) · 4 conversations · judged by sarvam-105b
Generated: <iso8601Z> by spar report

## Verdict
CONTROL GATE: PASS — happy-path scored 100.0, deterministically clean.
Worst non-control result: already-switched — 70.0, "ships with known gaps".
Confirmed agent defects: 1. Scenario bleed: none detected. Eval health: 3 flags.
Top fix: goal_outcome — generic, unadapted handling in 3 of 3 pressure personas (F03).
```

1. **Verdict block** (above; on gate fail: `CONTROL GATE: FAIL — <reasons>. RUN INVALID:
   per-persona data follows for diagnosis, but no cross-persona finding below is promoted
   to a defect.`)
2. **Scorecard table** — persona · stress · score · band · scored_weight_pct · det.
   coverage · end_reason. Control row marked `(control — excluded from aggregates)`.
3. **Confirmed defects** — bleed findings, valid breaches, deterministic violations. Each:
   one bold claim line, the verbatim quote, `(persona, turn N)`, the ground_truth entry or
   signature value breached, finding id.
4. **Recurring patterns** — DimensionClusters and AbsenceClusters with per-persona scores
   and one quote each; LLM pattern names appear here if they survived audit.
5. **Prioritised fix list — agent** — ordered by §2.9 priority, printed with the formula's
   inputs (`25w × 3/3 × 0.40 = 10.0`) so the ranking is auditable, plus the audited LLM
   rationale.
6. **Eval health (the eval grades itself)** — flat dimensions (corroborated vs not),
   unscoreable dimensions, rejection report, coverage rollup incl. run-wide blind spots,
   missing scorecards, schema warnings. Followed by **fix list — eval**.
7. **Findings index** — every finding id → kind, summary, and full citations
   (`scorecards/already-switched.json → dimensions.hallucination.ground_truth_audit.valid[0]`
   / `conversations/already-switched.json turn 12: "Yes, all live cricket, including the
   IPL, …"`).
8. **Run appendix** — durations, turn counts, usage totals, run.json warnings (the inert
   budget guard warning in this run belongs here, verbatim).

### 4.4 Rendering rules

- Quotes are copied byte-for-byte from artifacts — never re-wrapped, never ellipsised
  except with a trailing `…` AFTER a verbatim prefix of ≥ 60 chars (the CALIBRATION §2
  truncation lesson: store full, display prefixed).
- A conversation with `checked_fraction < 1.0` has every mention of its deterministic
  results suffixed `— numeric surface only PARTIALLY verified (fraction X)`; `clean` is
  never printed for it.
- Scores from cards with `scored_weight_pct < 100` always print as `100.0 (90% of rubric
  weight scored — optimistic)`.
- Devanagari quotes are printed as-is (UTF-8), never transliterated.
- Deterministic template text everywhere except the audited LLM sentences; with `--no-llm`
  the report contains zero LLM output and says so in the Generated line.

---

## 5. CLI — `runner/run.py` wiring only

Mirror `spar judge` exactly:

```
./spar report                      # newest run with a conversations/ dir
./spar report 20260725-185028-f99e33
./spar report --personas price-haggler,angry-churner   # narrows the REPORT, not the gate
./spar report --no-llm             # deterministic narrative only; zero Sarvam calls
```

In `build_parser()`:

```python
rep_cmd = sub.add_parser("report",
    help="synthesise all scorecards of a run into report.md (reads files only)")
rep_cmd.add_argument("run_id", nargs="?", default=None,
                     help="run id or path; defaults to the newest run")
rep_cmd.add_argument("--personas", default=None,
                     help="comma-separated persona ids to include in the report")
rep_cmd.add_argument("--no-llm", action="store_true",
                     help="skip the narrative LLM call; deterministic report only")
rep_cmd.add_argument("--config", default=None, type=Path)
rep_cmd.add_argument("--env", default=None, type=Path)
rep_cmd.add_argument("--verbose", "-v", action="store_true")
```

Handler in `main()`, placed after the `judge` block, same shape: lazy import
`from synth.report import ReportError, generate_report`; resolve via the existing
`_resolve_run_dir` (unchanged); if `run_dir/"scorecards"` has no `*.json`, print
`no scorecards in <run_dir> — run 'spar judge' first` to stderr and return 1;
`summary = asyncio.run(generate_report(run_dir, cfg, only=only, use_llm=not args.no_llm))`;
log the report path, gate status and finding count; return 0 on success, 1 on `ReportError`,
130 on KeyboardInterrupt. **No ElevenLabs client is constructed anywhere on this path.**

The `spar` bash entrypoint needs no change (`exec … -m runner.run "$@"` already forwards).

---

## 6. File assignment — three agents, disjoint files

| Agent | Owns (exclusively) | May import | Must not touch |
|---|---|---|---|
| **P (patterns)** | `synth/patterns.py`, `synth/__init__.py`, `scripts/regress_synth.py` | `judge.checks` (read-only: `normalise_dates`, `LOCALES`; copy `_fold` semantics locally if needed — do **not** edit checks.py), `judge.rubric` (`DIMENSIONS`, `BREACH_DIMENSIONS`), stdlib | `synth/report.py`, `runner/run.py`, anything in `judge/` or `agent/` |
| **R (report)** | `synth/report.py` | `synth.patterns` (exactly the §2 signatures), `agent.sarvam.SarvamClient`, `config.Config` | `synth/patterns.py`, `runner/run.py`, `judge/*` |
| **C (cli)** | `runner/run.py` (the `report` sub-parser + handler ONLY — additive edits; no existing line of `run`/`judge`/`config` handling changes), `spar` help comment (optional, one line) | `synth.report.generate_report` (lazy, inside the handler) | `synth/*`, `judge/*` |

`synth/__init__.py` (Agent P) is exactly:

```python
"""synth — turns a run's scorecards into one report. Reads files, never sockets."""
from synth.patterns import RunAnalysis, SynthError, analyse_run, load_run
__all__ = ["RunAnalysis", "SynthError", "analyse_run", "load_run"]
```

(No import of `synth.report` in `__init__` — it would drag `agent.sarvam`/httpx into every
patterns-only consumer and create a P↔R build-order coupling.)

Cross-boundary seams, stated once: R calls `load_run` + `analyse_run` and consumes
`RunAnalysis` exactly as specified in §2.2; C calls `generate_report` exactly as specified
in §3.1. Any signature change is a spec change, raised here, not negotiated in code review.

---

## 7. Acceptance tests — `scripts/regress_synth.py` (Agent P; offline; runnable today)

Runner: `PYTHONPATH=. uv run --python 3.12 python scripts/regress_synth.py`, exit 0/1,
same reporting idiom as `regress_audit.py`. `RUN = runs/20260725-185028-f99e33`. Mutation
tests deep-copy loaded dicts or copy the run dir into a tempdir — the real run dir is never
modified. Expected values below are hand-computed in Appendix A; a verifier can falsify
every one against the four scorecards and four transcripts.

- **T1 control gate.** Real run → `status == "pass"`, `control_ids == ("happy-path",)`,
  `reasons == ()`. Mutate a copy: happy-path `weighted_score = 40.0`, `band = "do not
  ship"` → `status == "fail"`, ≥1 reason, and `render_report` output's first 10 lines
  contain `CONTROL GATE: FAIL` and `RUN INVALID`.
- **T2 bleed is quiet on clean data.** Real run → `detect_bleed(...)[0] == ()`. (Expected
  result computed by hand: agent turns contain only own-signature values — Appendix A.1.)
- **T3 bleed no-op canary — MUST FAIL if the detector is a no-op.** Copy; append to
  angry-churner scorecard `deterministic.observations` the item `{check:
  "discount_percentage", turn: 2, speaker: "agent", value: "10%", quote: "<any>", verdict:
  "ok", confidence: "high", detail: "", recogniser: "digit_pct"}` → exactly ONE
  `BleedFinding`: `kind=="percentage"`, `persona_id=="angry-churner"`,
  `source_persona_ids==("price-haggler",)`, `value=="10%"`, `turn==2`. A detector returning
  `()` unconditionally passes T2 and fails T3.
- **T4 false-positive traps (both from §1.4).**
  (a) numeric: inject observation `value: "15%"` into a copy of already-switched's card
  (own ceiling 15) → zero findings; inject `value: "5%"` into a copy of price-haggler's
  card → ONE finding sourced to happy-path (5 < own ceiling 10, but 5 is happy-path's
  unique number — flagged by rule §2.3.3).
  (b) lexical: the REAL angry-churner transcript (turn 0 contains "JioHotstar Premium
  annual plan") yields zero `plan_name` findings against already-switched's "JioHotstar
  Premium (quarterly)". A substring matcher on "JioHotstar Premium" fails this.
- **T5 name bleed.** Copy angry-churner conversation; append `" Kunal ko bhi yahi offer
  mila tha."` to the turn-2 agent text → ONE `subscriber_name` finding, source
  `("price-haggler",)`. Real run → zero name findings.
- **T6 flatness.** Real run, non-control: flat set is exactly
  `{instruction_adherence (range 0.0, corroborated=True), objection_handling (0.0,
  corroborated=False), conversation_flow (0.1, corroborated=False)}`; `goal_outcome`
  (range 0.3), `hallucination` (1.0), `language_handling` (0.2), `escalation_safety` (1.0)
  are not flat.
- **T7 recurrence.** Real run: `by_dimension` contains goal_outcome
  {price-haggler, already-switched, angry-churner} scores (0.7, 0.7, 0.4) worst-tier
  failure; escalation_safety {price-haggler, angry-churner} (0.0, 0.7); objection_handling
  ×3 (0.8, 0.8, 0.8) dent. `by_breach` has exactly ONE cluster: entry `"naming any show,
  film, series or match other than the one in content_hook"`, one occurrence
  (already-switched, turn 12, quote containing `"including the IPL"`). Voided breaches
  (turns 8 and 10) appear in NO cluster. `recurrent_absences` has exactly one: claim
  matching `"never offered to connect the customer to a human"`, personas
  {price-haggler, angry-churner}, 4 sources.
- **T8 rejection report.** Real run: `total==1`, `by_persona=={"happy-path": 1, …0s}`,
  `concentrated==False`. Mutate: set angry-churner `evidence_audit.rejected=9` with 9
  detail rows → `concentrated==True`, `concentration_persona=="angry-churner"`.
- **T9 coverage rollup.** Real run: `min_checked_fraction==1.0`, `full_everywhere==True`,
  `run_wide_blind_spots` contains the `rupee_amount` zero-comparisons entry (present 4/4),
  `min_scored_weight_pct==90.0`.
- **T10 fix ordering.** Real run agent fixes in order: goal_outcome cluster (10.0), IPL
  breach (≈6.67), escalation cluster (≈4.33), objection cluster (2.0) — priorities within
  ±0.01.
- **T11 narrative audit (pure, no network).** `audit_llm_sentences` rejects a sentence
  containing `37%` when 37 is not in `allowed_numbers`; rejects `source_ids: ["F99"]`
  when unknown; rejects a sentence containing an 8-word quoted span; accepts a clean
  cited sentence. (Runs only if `synth.report` imports cleanly, so suite P stays green
  before R lands; print SKIP otherwise.)
- **T12 report end-to-end, no LLM.** `asyncio.run(generate_report(RUN_copy, cfg,
  use_llm=False))` (config loaded from `config.example.yaml` semantics with dummy env
  vars, or `Config` built directly — zero network either way): `report.md` +
  `synthesis.json` written; first 10 lines contain `CONTROL GATE: PASS`; every
  `kind=="transcript"` source's quote is a verbatim substring of the cited turn's text in
  the cited conversation file; the strings `reasoning_content` and `simulate-conversation`
  appear nowhere in either output; running twice yields identical `report.md` except the
  `Generated:` line. SKIP with a loud line if `synth.report` is absent.
- **T13 CLI smoke (integration; SKIP until Agent C lands).** `uv run --python 3.12 python
  -m runner.run report <copied-run> --no-llm` exits 0 and prints the report path; with a
  bogus run id exits 1; `--personas price-haggler` produces a report whose table has one
  non-control row while the Verdict block still reports the control gate.

Green criteria for the build overall: T1–T12 pass, T13 passes after integration, and the
two pre-existing suites (`smoke_loop_offline.py`, `regress_audit.py`) still pass untouched.

---

## Appendix A — hand-computed answer key for run `20260725-185028-f99e33`

Derived by hand from the four scorecards and four transcripts on 26 July 2026. If an
implementation disagrees with this table, one of the two is wrong — check here first.

### A.1 Bleed inputs

Agent-turn numeric surface (from `deterministic.observations`, all verdict `ok`):

| persona | percentages | dates | rupee mentions |
|---|---|---|---|
| happy-path | 5% ×2 | (1,8) ×2 | 0 |
| price-haggler | 10% ×8 | (8,8) ×4 | 0 |
| already-switched | 15% ×2 | (12,8) ×2 | 0 |
| angry-churner | 25% ×5 | (3,8) ×5 (1 Latin + 4 Devanagari) | 0 |

Foreign names in agent turns: none (each transcript contains only its own subscriber).
Foreign full plan-token sequences: none ("JioHotstar Premium annual plan" in angry-churner
is its OWN plan; the shared prefix with already-switched is the T4b trap).
**detect_bleed ⇒ 0 findings. unrecognised mentions: 0 across all cards.**

### A.2 Cluster inputs (non-control scores)

| dimension | haggler | switched | churner | cluster |
|---|---|---|---|---|
| goal_outcome (25) | 0.7 | 0.7 | 0.4 | 3-strong, worst tier failure (0.4 < 0.5) |
| hallucination (20) | 1.0 | **0.0 fail** | 1.0 | singleton failure → reportable alone |
| instruction_adherence (15) | 1.0 | 1.0 | 1.0 | none |
| language_handling (15) | 1.0 | 1.0 | 0.8 | 1 dent → no cluster |
| objection_handling (10) | 0.8 | 0.8 | 0.8 | 3-strong dent |
| escalation_safety (10) | **0.0 fail** | 1.0 | 0.7 | 2-strong, failure |
| conversation_flow (5) | 0.9 | 0.9 | 0.8 | 1 dent → no cluster |

Valid breaches run-wide: 1 (already-switched · hallucination · turn 12 · IPL). Voided: 2
(same card, turns 8, 10). happy-path (control): goal 1.0, hall 1.0, instr 1.0, lang 1.0,
objection UNSCORED, escalation 1.0, flow 1.0 — excluded from the table above.

### A.3 Spreads (non-control, n=3)

ranges: goal 0.3 · hall 1.0 · instr **0.0 flat, corroborated** · lang 0.2 · objection
**0.0 flat** · escalation 1.0 · flow **0.1 flat**.

### A.4 Fix priorities

goal_outcome 25×1.0×0.4=10.0 → hallucination/IPL 20×⅓×1.0=6.6̄7 → escalation
10×⅔×0.65=4.3̄3 → objection 10×1.0×0.2=2.0. Eval fixes (unranked pool): objection_handling
flat + unscored-on-control; conversation_flow flat; rupee_amount run-wide blind spot;
instruction_adherence corroborated-flat (watch only).

### A.5 Gate and coverage

Gate: PASS (happy-path 100.0 / production-ready / det clean / 0 breaches / goal_reached).
Rejections: 1 total (happy-path objection_handling absence claim, contradicted by turn 4)
— the audit working, not a tooling signal. Coverage: checked_fraction 1.0 ×4;
scored_weight_pct 100/100/100/90; rupee_amount compared 0 in 4/4 cards.
