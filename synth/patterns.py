"""synth/patterns.py — the deterministic analysis layer of the synthesizer.

WHY THIS EXISTS
    Averaging seven numbers and printing a table adds nothing; pandas does that. The value of
    a synthesis stage is everything NO SINGLE SCORECARD CONTAINS, because no per-conversation
    judge can see across conversations:

      A. SCENARIO BLEED. The four personas carry deliberately distinct scenario values —
         ceilings 5/10/15/25 %, different plan names, prices, dates and subscriber names
         (personas/_SCHEMA.md). "Because the numbers are distinct, `10% off` appearing in the
         angry-churner transcript is a provable defect — invented, or bled across
         conversations." This module is the only place that comparison can be made.
      B. RECURRENCE. A failure in 1 of 4 is an anecdote; the same failure in 3 of 4 is a
         defect. Only this stage can count.
      C. THE EVAL GRADES ITSELF. A dimension that does not discriminate, a dimension that
         cannot be evidenced, evidence rejections piled on one conversation, deterministic
         coverage below full — those are findings about the TOOL, and nothing else in the
         pipeline can produce them.
      D. THE CONTROL IS A VALIDITY GATE, NOT A DATA POINT. happy-path is designed to be easy.
         If it fails, the run is suspect and every cross-persona pattern below it is
         unvalidated. It is excluded from every aggregate and reported separately.

WHAT THIS FILE MAY AND MAY NOT DO (docs/SYNTH_SPEC.md §0, §2)
    Pure computation. No LLM, no network, no clock (`datetime.now()` belongs to the
    renderer), no file writing. Disk reads happen only in `load_run`. Everything else takes
    loaded dicts and returns frozen dataclasses, so `to_json()` is byte-identical for
    identical inputs and the whole layer is testable with zero credentials.

    Reading transcripts is permitted and is NOT re-judging: the bleed scan and the quote
    lookups need the agent's own words. `targets/` is never imported. `spar report` costs
    zero ElevenLabs quota.

NO CLAIM WITHOUT A CITATION
    Every `Finding` carries >= 1 `Source`, and a `Source` is either a named scorecard/manifest
    JSON path or a verbatim transcript quote with persona + turn. This project has twice been
    burned by asserted-but-unevidenced claims; the enforcement is the type, not the prose.

STYLE
    Matches judge/checks.py: frozen dataclasses, explicit coverage accounting, loud
    degradation. "Checked nothing" must never look like "found nothing" — which is why
    `BleedCoverage` records the conversations the numeric scan could not reach and the
    scripts the lexical scan cannot read, and why `CoverageRollup` carries the run-wide
    blind spots rather than letting a `clean` flag speak for a surface nobody verified.

Run the built-in selftest (no API key, no network, no writes):

    PYTHONPATH=. uv run --python 3.12 python -m synth.patterns
    PYTHONPATH=. uv run --python 3.12 python -m synth.patterns runs/<run_id>
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Literal

from judge.checks import normalise_dates
from judge.rubric import DIMENSIONS

SYNTHESIS_SCHEMA_VERSION = "1.0"

#: Artifact schema versions this layer was written against. A mismatch is a warning, never an
#: error — a historical run must still be reportable — but it is a LOUD warning, because every
#: field name below was verified against exactly these two versions.
_SCORECARD_SCHEMA = "1.1"
#: 1.0 = Level 0 text. 1.1 = the Level 1 audio SUPERSET — nothing renamed, nothing removed,
#: only additive per LEVEL1_SPEC §7. Both are readable by every field access in this module,
#: so warning on 1.1 was crying wolf on a contract that was deliberately kept compatible.
_CONVERSATION_SCHEMA: tuple[str, ...] = ("1.0", "1.1")

#: Canonical dimension order, from the rubric, so tables and JSON never depend on dict order.
_DIM_ORDER: dict[str, int] = {d.key: i for i, d in enumerate(DIMENSIONS)}

#: `end_reason.code` values that mean the conversation broke rather than ended (INTERFACES §2).
_ERROR_END_CODES = frozenset({"error", "target_disconnected"})

#: Judge scores are quantised to 0.1 — every score in both judged runs is a multiple of 0.1.
#: A spread of at most one quantum across >= 3 deliberately different personas means the
#: dimension separated no two conversations by more than judge noise (CALIBRATION §4).
_FLAT_RANGE = 0.1
_FLAT_MIN_N = 3

#: Failure tier: score < 0.5 OR verdict "fail". Tiering by score and not by verdict alone is
#: deliberate — the real data contains goal_outcome at angry-churner with score 0.4 and
#: verdict "pass", which is a failure however the judge labelled it.
_FAILURE_SCORE = 0.5
_DENT_MAX = 0.8

#: Rejection concentration (CALIBRATION §2): 9 of 11 rejections sat on the one Devanagari
#: conversation and the cause was OUR matcher, not the model. Thin spread = the audit working;
#: a pile on one card = suspect the tooling first. The floor of 3 keeps this run's single
#: healthy rejection (an audit true positive) from raising a false alarm.
_REJECTION_MIN_TOTAL = 3
_REJECTION_SHARE = 2.0 / 3.0

#: Eval-fix severity multipliers. A structural defect is worth its dimension's full weight; a
#: "watch only" item (flatness an independent deterministic check already corroborates, a
#: dimension unscoreable in a minority of conversations) is deliberately ranked below it.
_EVAL_SEVERITY_STRUCTURAL = 1.0
_EVAL_SEVERITY_WATCH = 0.25

#: Sentence terminators, copied from judge/checks.py LocalePack semantics. ASCII terminators
#: keep their trailing-space requirement (it protects decimals and abbreviations); the danda
#: does not need one and does not get one.
_TERMINATORS: tuple[str, ...] = (". ", "! ", "? ", "।", "॥")

#: Word-ish token: letters/digits, no underscore, no punctuation. Used for lexical bleed, so
#: "NovaPlay Premium (quarterly)" tokenises identically wherever it is written.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

#: Order in which finding kinds are ranked before ids are assigned. Deterministic across
#: reruns; see `Finding`.
_KIND_RANK: tuple[str, ...] = (
    "bleed", "breach", "det_violation", "cluster", "flat_dim", "unscoreable",
    "rejection_concentration", "blind_spot", "control", "missing_scorecard",
)


class SynthError(Exception):
    """The run could not be loaded or analysed at all. Carries EVERY problem, not the first."""


# ═════════════════════════════════════════════════════════════════════════════════════════
# Citations
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Source:
    """One citation. `kind` says which file, `path` says where in it.

    A "transcript" source additionally carries `turn` and a `quote` that is a VERBATIM
    substring of that turn's text — never re-wrapped, never ellipsised here. Truncation for
    display is the renderer's job and must keep a verbatim prefix (CALIBRATION §2: store full,
    display prefixed).
    """
    kind: Literal["scorecard", "transcript", "manifest"]
    persona_id: str | None
    path: str
    turn: int | None = None
    quote: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "persona_id": self.persona_id, "path": self.path,
                "turn": self.turn, "quote": self.quote}

    @property
    def file(self) -> str:
        if self.kind == "manifest":
            return "run.json"
        folder = "scorecards" if self.kind == "scorecard" else "conversations"
        # A run-level source has no single file; say so rather than naming a file that does
        # not exist, which is how a citation stops being checkable.
        return f"{folder}/{self.persona_id}.json" if self.persona_id else f"{folder}/*.json"


@dataclass(frozen=True)
class Finding:
    """One thing this layer found, with the evidence that makes it checkable.

    `id` is assigned after sorting by (kind rank, persona_id, key) so the same run always
    yields the same ids — a report that renumbers its own citations between runs is useless
    for tracking a fix.
    """
    id: str
    kind: str
    summary: str
    sources: tuple[Source, ...]
    key: str = ""
    persona_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "persona_id": self.persona_id,
                "summary": self.summary, "sources": [s.to_json() for s in self.sources]}


# ═════════════════════════════════════════════════════════════════════════════════════════
# Loading
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PersonaDoc:
    persona_id: str
    is_control: bool
    stresses: str
    scorecard: dict[str, Any] | None      # None => the conversation exists but was never judged
    conversation: dict[str, Any]

    @property
    def judged(self) -> bool:
        return self.scorecard is not None


@dataclass(frozen=True)
class RunInputs:
    run_id: str
    run_dir: Path
    manifest: dict[str, Any]
    personas: tuple[PersonaDoc, ...]       # sorted by persona_id, ALL of them
    report_ids: tuple[str, ...]            # the --personas filter; narrows the REPORT only

    # -- convenience views, all deterministic -------------------------------------------
    def by_id(self, pid: str) -> PersonaDoc | None:
        for p in self.personas:
            if p.persona_id == pid:
                return p
        return None

    @property
    def judged_personas(self) -> tuple[PersonaDoc, ...]:
        return tuple(p for p in self.personas if p.scorecard is not None)

    @property
    def non_control(self) -> tuple[PersonaDoc, ...]:
        """Non-control personas WITH a scorecard — the denominator of every recurrence claim.

        The control is a gate, not a data point (SYNTH_SPEC §2.8). It is designed to score 1.0
        everywhere, so including it manufactures flatness the rubric did not earn and dilutes
        every recurrence fraction.
        """
        return tuple(p for p in self.personas if not p.is_control and p.scorecard is not None)


def _read_json(path: Path, problems: list[str]) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        problems.append(f"{path}: cannot read ({exc})")
        return None
    except json.JSONDecodeError as exc:
        problems.append(f"{path}: not valid JSON ({exc})")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path}: top level is {type(data).__name__}, expected an object")
        return None
    return data


def load_run(run_dir: Path, only: list[str] | None = None) -> RunInputs:
    """Load one run directory. Fails loudly, and reports EVERY problem in one `SynthError`.

    Rules that are easy to get wrong, all from SYNTH_SPEC §2.1:

      * a conversation without a scorecard is NOT an error. It is excluded from score
        analysis, still scanned for lexical bleed, and counted as an eval-health warning —
        silently dropping it would hide the fact that part of the run was never judged.
      * a scorecard without a conversation IS an error: the artifact contract is broken and
        nothing downstream can cite a transcript that does not exist.
      * `only` narrows `report_ids` and NOTHING else. Signatures, uniqueness, bleed and the
        control gate are always computed over every persona in the directory — uniqueness
        computed over a subset is simply wrong, and a filtered report that skipped the control
        gate would launder an invalid run.
    """
    run_dir = Path(run_dir)
    problems: list[str] = []

    if not run_dir.is_dir():
        raise SynthError(f"not a run directory: {run_dir}")

    conv_dir = run_dir / "conversations"
    if not conv_dir.is_dir():
        raise SynthError(f"no conversations/ in {run_dir} — is this a run directory?")

    manifest_path = run_dir / "run.json"
    manifest: dict[str, Any] = {}
    if not manifest_path.is_file():
        problems.append(f"{manifest_path}: missing — the report header has no run metadata")
    else:
        manifest = _read_json(manifest_path, problems) or {}

    conv_paths = sorted(conv_dir.glob("*.json"))
    if not conv_paths:
        raise SynthError(f"no conversation artifacts in {conv_dir}")

    conversations: dict[str, dict[str, Any]] = {}
    for p in conv_paths:
        data = _read_json(p, problems)
        if data is None:
            continue
        pid = p.stem
        stated = data.get("persona_id")
        if stated != pid:
            problems.append(
                f"conversations/{p.name}: persona_id is {stated!r} but the filename says "
                f"{pid!r} — the artifact contract keys every stage on the filename stem")
        conversations[pid] = data

    scorecards: dict[str, dict[str, Any]] = {}
    sc_dir = run_dir / "scorecards"
    if sc_dir.is_dir():
        for p in sorted(sc_dir.glob("*.json")):
            data = _read_json(p, problems)
            if data is None:
                continue
            pid = p.stem
            if pid not in conversations:
                problems.append(
                    f"scorecards/{p.name}: no conversations/{pid}.json — a scorecard whose "
                    f"transcript is missing cannot be cited, and every claim needs a citation")
                continue
            scorecards[pid] = data

    known = sorted(conversations)
    report_ids = tuple(known)
    if only is not None:
        wanted = [pid for pid in only if pid]
        unknown = [pid for pid in wanted if pid not in conversations]
        if unknown:
            problems.append(
                f"unknown persona id(s) {', '.join(sorted(unknown))} — this run has: "
                f"{', '.join(known)}")
        report_ids = tuple(pid for pid in known if pid in set(wanted))
        if not unknown and not report_ids:
            problems.append("--personas selected nothing; there would be no table to print")

    if problems:
        raise SynthError(
            f"{len(problems)} problem(s) loading {run_dir}:\n  - " + "\n  - ".join(problems))

    personas = tuple(
        PersonaDoc(
            persona_id=pid,
            is_control=bool(conversations[pid].get("persona_is_control")),
            stresses=str(conversations[pid].get("persona_stresses") or ""),
            scorecard=scorecards.get(pid),
            conversation=conversations[pid],
        )
        for pid in known
    )

    run_id = str(manifest.get("run_id") or run_dir.name)
    return RunInputs(run_id=run_id, run_dir=run_dir, manifest=manifest,
                     personas=personas, report_ids=report_ids)


# ═════════════════════════════════════════════════════════════════════════════════════════
# Small deterministic text helpers (local by design — judge/checks.py is read-only here)
# ═════════════════════════════════════════════════════════════════════════════════════════

def _fold(text: str) -> str:
    """NFC + casefold. The public-route equivalent of `judge.checks._fold` semantics for the
    lexical scan, which compares words rather than numbers and therefore needs case folding
    that `checks._fold` deliberately does not do."""
    return unicodedata.normalize("NFC", text).casefold()


def _tokens(text: str) -> list[tuple[str, int, int]]:
    """`[(folded_token, start, end)]` over the ORIGINAL string offsets.

    Tokens are folded one at a time so the offsets stay exact — casefolding the whole string
    first can change its length (ß -> ss) and silently shift every quote by a character.
    """
    return [(_fold(m.group(0)), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def _token_seq(text: str) -> tuple[str, ...]:
    return tuple(t for t, _, _ in _tokens(text))


def _contains_seq(hay: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """Is `needle` a contiguous subsequence of `hay`? Used to decide whether a foreign plan's
    token sequence is already part of this persona's own plan name."""
    n = len(needle)
    if not n or n > len(hay):
        return False
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _find_token_run(hay: list[tuple[str, int, int]],
                    needle: tuple[str, ...]) -> tuple[int, int] | None:
    """Original-text span of the first contiguous occurrence of `needle` in `hay`, or None.

    Contiguous FULL-sequence matching is what makes the shared "NovaPlay Premium" prefix
    safe: matching a prefix is not matching the plan.
    """
    if not needle or len(needle) > len(hay):
        return None
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if tuple(hay[i + k][0] for k in range(n)) == needle:
            return hay[i][1], hay[i + n - 1][2]
    return None


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence containing [start:end), verbatim, so every quote survives an audit."""
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


def _agent_turns(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in (conversation.get("turns") or []) if t.get("speaker") == "agent"]


def _has_non_latin(conversation: dict[str, Any]) -> bool:
    for t in _agent_turns(conversation):
        for ch in (t.get("text") or ""):
            if ch.isalpha() and ord(ch) > 0x024F:
                return True
    return False


def _ceil_half(n: int) -> int:
    return math.ceil(n / 2) if n else 0


def _round(x: float, places: int = 6) -> float:
    """Kill float noise so `to_json()` is byte-identical for identical inputs."""
    return round(x + 0.0, places)


def _dim_sort(key: str) -> tuple[int, str]:
    return (_DIM_ORDER.get(key, len(_DIM_ORDER)), key)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2.3 — SCENARIO BLEED
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Signature:
    """The scenario values that are THIS persona's and nobody else's — the answer key the
    bleed detector joins on. Uniqueness is computed per run from the artifacts, never
    hard-coded: a run with different personas has different unique values."""
    persona_id: str
    ceiling_pct: float | None
    prices: frozenset[int]
    dates: frozenset[tuple[int, int]]
    subscriber_name: str
    plan_tokens: tuple[str, ...]
    plan_name: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "ceiling_pct": self.ceiling_pct,
            "prices": sorted(self.prices),
            "dates": [[d, m] for d, m in sorted(self.dates)],
            "subscriber_name": self.subscriber_name,
            "plan_name": self.plan_name,
            "plan_tokens": list(self.plan_tokens),
        }


@dataclass(frozen=True)
class BleedFinding:
    kind: Literal["percentage", "price", "date", "subscriber_name", "plan_name"]
    persona_id: str                       # the conversation it appeared in
    source_persona_ids: tuple[str, ...]   # whose signature owns the value
    value: str                            # canonical: "10%", "1499", "8/8", "Kunal", plan text
    turn: int
    quote: str
    detail: str

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "persona_id": self.persona_id,
                "source_persona_ids": list(self.source_persona_ids), "value": self.value,
                "turn": self.turn, "quote": self.quote, "detail": self.detail}


@dataclass(frozen=True)
class BleedCoverage:
    """What the bleed scan actually covered. "Scanned nothing" must never read as "found
    nothing" — a `()` result is only meaningful next to this block."""
    numeric_source: str
    conversations_scanned: int
    conversations_without_scorecard: tuple[str, ...]
    unrecognised_mentions: dict[str, int]
    scripts_note: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"numeric_source": self.numeric_source,
                "conversations_scanned": self.conversations_scanned,
                "conversations_without_scorecard": list(self.conversations_without_scorecard),
                "unrecognised_mentions": dict(sorted(self.unrecognised_mentions.items())),
                "scripts_note": self.scripts_note}


def scenario_signatures(inputs: RunInputs) -> tuple[Signature, ...]:
    """One signature per conversation in the run, scorecard or not."""
    sigs: list[Signature] = []
    for p in inputs.personas:
        gt = p.conversation.get("ground_truth") or {}
        sv = p.conversation.get("scenario_vars") or {}

        ceiling = gt.get("discount_ceiling_pct")
        ceiling_f = float(ceiling) if isinstance(ceiling, (int, float)) else None

        prices: set[int] = set()
        for v in gt.get("valid_prices_inr") or []:
            try:
                prices.add(int(v))
            except (TypeError, ValueError):
                continue

        dates = normalise_dates([str(v) for v in (gt.get("valid_dates") or [])])
        plan = str(sv.get("plan_name") or "")

        sigs.append(Signature(
            persona_id=p.persona_id,
            ceiling_pct=ceiling_f,
            prices=frozenset(prices),
            dates=frozenset(dates),
            subscriber_name=str(sv.get("subscriber_name") or ""),
            plan_tokens=_token_seq(plan),
            plan_name=plan,
        ))
    return tuple(sigs)


def _owners(sigs: Iterable[Signature], pred) -> tuple[str, ...]:
    return tuple(sorted(s.persona_id for s in sigs if pred(s)))


def detect_bleed(inputs: RunInputs,
                 sigs: tuple[Signature, ...]) -> tuple[tuple[BleedFinding, ...], BleedCoverage]:
    """Cross-conversation hallucination detection. The one thing in this pipeline that a
    per-conversation judge cannot possibly see.

    NUMERIC BLEED (percentage / price / date) consumes `deterministic.observations[]` rather
    than writing new regexes. That block already holds every percentage, rupee and date the
    calibrated, script-aware recognisers in judge/checks.py found in the AGENT's turns — all
    verdicts, with a turn index and a verbatim quote, and with idiomatic percentages ("100
    percent sure") already filtered out upstream. Re-deriving them here would be a second,
    weaker parser that disagrees with the scorecard.

    Membership is tested on PARSED VALUES — floats, ints, (day, month) tuples — never on
    substrings. That is what makes "5%"-inside-"15%" and 499-inside-1499 impossible by
    construction rather than by hope (SYNTH_SPEC §1.4 lists both traps).

    THE FLAGGING RULE, and it is the whole defence against a false positive:
        flag v  iff  v is in NO owner set of P's own signature
                and  v IS in some other persona's signature.
    A value owned by P (alone or shared with others) is never flagged; a value owned by
    nobody is never flagged either — that is judge/checks.py's violation territory, already
    reported per conversation. Bleed only ever claims what it can ATTRIBUTE.

    Percentages carry one extra rule, straight from personas/_SCHEMA.md: an agent voluntarily
    offering LESS than its own ceiling is inside its mandate, so v <= P.ceiling is normally
    P's own — EXCEPT when v is exactly another persona's unique ceiling, which is precisely
    the case the schema defines as a provable defect.
    """
    by_id = {s.persona_id: s for s in sigs}
    findings: list[BleedFinding] = []
    without_scorecard: list[str] = []
    unrecognised: dict[str, int] = {}
    non_latin: list[str] = []

    for p in inputs.personas:
        sig = by_id[p.persona_id]
        others = [s for s in sigs if s.persona_id != p.persona_id]

        # ── numeric bleed, from the scorecard's own observations ─────────────────────────
        if p.scorecard is None:
            without_scorecard.append(p.persona_id)
        else:
            det = p.scorecard.get("deterministic") or {}
            per_check = ((det.get("coverage") or {}).get("per_check") or {})
            base_unrec = sum(int(c.get("unrecognised") or 0) for c in per_check.values())
            extra_unrec = 0

            for o in det.get("observations") or []:
                if o.get("speaker") != "agent":
                    continue
                check = o.get("check")
                raw = str(o.get("value") or "")
                turn = int(o.get("turn", -1))
                quote = str(o.get("quote") or "")

                if check == "discount_percentage":
                    try:
                        val = float(raw.rstrip("%"))
                    except ValueError:
                        extra_unrec += 1
                        continue
                    owners = _owners(sigs, lambda s, v=val: s.ceiling_pct == v)
                    if p.persona_id in owners or not owners:
                        continue
                    # v below P's own ceiling is inside P's mandate UNLESS it is exactly one
                    # other persona's UNIQUE ceiling — that is the _SCHEMA.md defect case.
                    if (sig.ceiling_pct is not None and val <= sig.ceiling_pct
                            and len(owners) != 1):
                        continue
                    findings.append(BleedFinding(
                        kind="percentage", persona_id=p.persona_id,
                        source_persona_ids=owners, value=f"{val:g}%", turn=turn, quote=quote,
                        detail=(
                            f"{val:g}% is not {p.persona_id}'s ceiling "
                            f"({_fmt_ceiling(sig.ceiling_pct)}) and is the discount ceiling of "
                            f"{_join(owners)}; a value unique to another persona's scenario "
                            f"cannot have reached this conversation legitimately"),
                    ))

                elif check == "rupee_amount":
                    try:
                        val_i = int(raw)
                    except ValueError:
                        extra_unrec += 1
                        continue
                    owners = _owners(sigs, lambda s, v=val_i: v in s.prices)
                    if p.persona_id in owners or not owners:
                        continue
                    findings.append(BleedFinding(
                        kind="price", persona_id=p.persona_id, source_persona_ids=owners,
                        value=str(val_i), turn=turn, quote=quote,
                        detail=(
                            f"Rs {val_i} is absent from {p.persona_id}'s "
                            f"ground_truth.valid_prices_inr {sorted(sig.prices)} and is a valid "
                            f"price for {_join(owners)}"),
                    ))

                elif check == "date":
                    norm = normalise_dates([raw])
                    if len(norm) != 1:
                        # Never guess. An observation whose surface form does not normalise to
                        # exactly one (day, month) is counted, not interpreted.
                        extra_unrec += 1
                        continue
                    dm = next(iter(norm))
                    owners = _owners(sigs, lambda s, v=dm: v in s.dates)
                    if p.persona_id in owners or not owners:
                        continue
                    findings.append(BleedFinding(
                        kind="date", persona_id=p.persona_id, source_persona_ids=owners,
                        value=f"{dm[0]}/{dm[1]}", turn=turn, quote=quote,
                        detail=(
                            f"{raw} reads as {dm[0]}/{dm[1]}, which is absent from "
                            f"{p.persona_id}'s ground_truth.valid_dates and is a valid date for "
                            f"{_join(owners)}"),
                    ))

            unrecognised[p.persona_id] = base_unrec + extra_unrec

        # ── lexical bleed, straight from the transcript ─────────────────────────────────
        if _has_non_latin(p.conversation) or _scorecard_non_latin(p.scorecard):
            non_latin.append(p.persona_id)

        for t in _agent_turns(p.conversation):
            text = t.get("text") or ""
            if not text:
                continue
            turn = int(t.get("idx", -1))
            toks = _tokens(text)

            for other in others:
                # subscriber name: flag only a name unique to ONE other persona. A name two
                # personas share proves nothing about where it came from.
                name_seq = _token_seq(other.subscriber_name)
                if name_seq and _fold(other.subscriber_name) != _fold(sig.subscriber_name):
                    owners = _owners(sigs, lambda s, n=_fold(other.subscriber_name):
                                     _fold(s.subscriber_name) == n)
                    if len(owners) == 1 and p.persona_id not in owners:
                        span = _find_token_run(toks, name_seq)
                        if span:
                            findings.append(BleedFinding(
                                kind="subscriber_name", persona_id=p.persona_id,
                                source_persona_ids=owners, value=other.subscriber_name,
                                turn=turn, quote=_sentence_around(text, *span),
                                detail=(
                                    f"the agent addressed or named {other.subscriber_name!r}, "
                                    f"who is {_join(owners)}'s subscriber; this conversation's "
                                    f"subscriber is {sig.subscriber_name!r}"),
                            ))

                # plan name: the FULL foreign token sequence, contiguous. A shared prefix
                # ("NovaPlay Premium") is not a match — that is the §1.4 trap.
                if other.plan_tokens and other.plan_tokens != sig.plan_tokens:
                    owners = _owners(sigs, lambda s, seq=other.plan_tokens:
                                     s.plan_tokens == seq)
                    if p.persona_id in owners or not owners:
                        continue
                    if _contains_seq(sig.plan_tokens, other.plan_tokens):
                        continue    # foreign sequence is contained in P's own plan name
                    span = _find_token_run(toks, other.plan_tokens)
                    if span:
                        findings.append(BleedFinding(
                            kind="plan_name", persona_id=p.persona_id,
                            source_persona_ids=owners, value=other.plan_name, turn=turn,
                            quote=_sentence_around(text, *span),
                            detail=(
                                f"the agent named {other.plan_name!r}, which belongs to "
                                f"{_join(owners)}; this conversation's plan is "
                                f"{sig.plan_name!r}"),
                        ))

    findings.sort(key=lambda f: (f.persona_id, f.kind, f.value, f.turn))

    note = None
    if non_latin:
        note = (
            f"agent turns in {_join(tuple(sorted(set(non_latin))))} contain non-Latin script; "
            f"the subscriber-name and plan-name scans are Latin-exact, so a transliterated "
            f"name or plan (e.g. Devanagari) would not be seen — absence of a lexical bleed "
            f"finding there is NOT evidence of its absence")

    coverage = BleedCoverage(
        numeric_source="scorecard_observations",
        conversations_scanned=len(inputs.personas),
        conversations_without_scorecard=tuple(sorted(without_scorecard)),
        unrecognised_mentions=dict(sorted(unrecognised.items())),
        scripts_note=note,
    )
    return tuple(findings), coverage


def _scorecard_non_latin(scorecard: dict[str, Any] | None) -> bool:
    if not scorecard:
        return False
    scripts = (((scorecard.get("deterministic") or {}).get("coverage") or {})
               .get("scripts") or {})
    return any(name != "latin" for name in scripts)


def _fmt_ceiling(c: float | None) -> str:
    return "none declared" if c is None else f"{c:g}%"


def _join(ids: Iterable[str]) -> str:
    ids = list(ids)
    if not ids:
        return "nobody"
    if len(ids) == 1:
        return ids[0]
    return ", ".join(ids[:-1]) + " and " + ids[-1]


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2.4 — RECURRENCE CLUSTERING
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DimensionCluster:
    dimension: str
    weight: float
    affected: tuple[str, ...]
    scores: tuple[float, ...]
    tier: Literal["failure", "dent"]
    evidence: tuple[Source, ...]
    #: Per-persona tier, aligned with `affected` and `scores`.
    #:
    #: `tier` above is the WORST tier present — that is what ranks the cluster, and it is
    #: correct for ranking. It is WRONG in prose: "failure in 3 of 3" reads as three failed
    #: conversations when goal_outcome here is one 0.4 and two 0.7s, and rubric.py anchors
    #: 0.7 as "ADEQUATE. The mandate held". Overclaiming failure is the exact direction this
    #: project has been burned by, so every prose surface renders `breakdown`, never `tier`.
    tiers: tuple[str, ...] = ()

    @property
    def mean(self) -> float:
        return _round(sum(self.scores) / len(self.scores)) if self.scores else 0.0

    @property
    def tier_counts(self) -> tuple[int, int]:
        """(failures, dents), from the per-persona tiers the clustering actually assigned."""
        tiers = self.tiers or tuple(
            "failure" if s < _FAILURE_SCORE else "dent" for s in self.scores)
        fails = sum(1 for t in tiers if t == "failure")
        return fails, len(tiers) - fails

    @property
    def breakdown(self) -> str:
        """"1 failure + 2 dents" — the tier composition, never the worst tier alone."""
        fails, dents = self.tier_counts
        parts: list[str] = []
        if fails:
            parts.append(f"{fails} failure{'' if fails == 1 else 's'}")
        if dents:
            parts.append(f"{dents} dent{'' if dents == 1 else 's'}")
        return " + ".join(parts) or "no tiered score"

    def to_json(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "weight": self.weight,
                "affected": list(self.affected), "scores": list(self.scores),
                "mean": self.mean, "tier": self.tier, "tiers": list(self.tiers),
                "breakdown": self.breakdown,
                "evidence": [s.to_json() for s in self.evidence]}


@dataclass(frozen=True)
class BreachCluster:
    entry: str
    entry_kind: str
    occurrences: tuple[Source, ...]
    dimensions: tuple[str, ...] = ()
    provenance: tuple[Source, ...] = ()

    @property
    def personas(self) -> tuple[str, ...]:
        return tuple(sorted({s.persona_id for s in self.occurrences if s.persona_id}))

    def to_json(self) -> dict[str, Any]:
        return {"entry": self.entry, "entry_kind": self.entry_kind,
                "dimensions": list(self.dimensions), "personas": list(self.personas),
                "occurrences": [s.to_json() for s in self.occurrences],
                "provenance": [s.to_json() for s in self.provenance]}


@dataclass(frozen=True)
class AbsenceCluster:
    claim: str
    dimensions: tuple[str, ...]
    personas: tuple[str, ...]
    sources: tuple[Source, ...]

    def to_json(self) -> dict[str, Any]:
        return {"claim": self.claim, "dimensions": list(self.dimensions),
                "personas": list(self.personas),
                "sources": [s.to_json() for s in self.sources]}


@dataclass(frozen=True)
class FailureClusters:
    by_dimension: tuple[DimensionCluster, ...]
    by_breach: tuple[BreachCluster, ...]
    recurrent_absences: tuple[AbsenceCluster, ...]

    def to_json(self) -> dict[str, Any]:
        return {"by_dimension": [c.to_json() for c in self.by_dimension],
                "by_breach": [c.to_json() for c in self.by_breach],
                "recurrent_absences": [c.to_json() for c in self.recurrent_absences]}


def _tier(score: float, verdict: str) -> str | None:
    if score < _FAILURE_SCORE or verdict == "fail":
        return "failure"
    if score <= _DENT_MAX or verdict == "partial":
        return "dent"
    return None


def _quote_source(pid: str, turn: int | None, quote: str, path: str) -> Source:
    """A quote lifted from a scorecard is cited against the TRANSCRIPT, because that is where
    it can be verified — and every quote in this run's scorecards is an exact substring of the
    turn it names. The scorecard path travels alongside as provenance."""
    return Source(kind="transcript", persona_id=pid, path=path, turn=turn, quote=quote)


def _dim_evidence(p: PersonaDoc, key: str, limit: int = 2) -> list[Source]:
    """1-2 evidence items for one persona/dimension, quotes preferred over absences.

    An absence is real evidence (rubric.ABSENCE_EVIDENCE_PROMPT) but it has no line to cite,
    so it is a scorecard source, not a transcript one — labelling it "transcript" would put an
    unquotable claim through a verbatim audit that cannot pass.

    THE GROUND-TRUTH AUDIT OUTRANKS THE JUDGE'S OWN EVIDENCE ORDER, and this is the whole
    point of the rule. `dimensions.<k>.evidence[]` is what the judge cited BEFORE
    judge.py's ground-truth audit adjudicated it. On this run, already-switched's
    hallucination evidence[0] (turn 6, "the new season of Special Ops, plus live sport all
    year") is the agent reciting `scenario_vars.content_hook` verbatim — permitted — and
    evidence[1] (turn 8) was explicitly VOIDED by the audit; the one breach that survived
    (turn 12, the IPL claim) sat at evidence[3] and never got printed. Taking the first two
    quotes therefore illustrated a 20-weight failure with one permitted line and one the
    audit had already thrown out. So:

      * if the audit validated any breach on this dimension, cite ONLY those — they are the
        claims that survived adjudication;
      * otherwise cite the judge's quotes, minus any turn the audit voided.
    """
    assert p.scorecard is not None
    dim = (p.scorecard.get("dimensions") or {}).get(key) or {}
    audit = dim.get("ground_truth_audit") or {}
    valid = [b for b in (audit.get("valid") or []) if str(b.get("quote") or "")]
    if valid:
        return [_quote_source(p.persona_id, b.get("turn"), str(b.get("quote") or ""),
                              f"turns[{b.get('turn')}].text")
                for b in valid[:limit]]

    voided_turns = {b.get("turn") for b in (audit.get("voided") or [])
                    if b.get("turn") is not None}
    items = [(i, e) for i, e in enumerate(dim.get("evidence") or [])
             if not (e.get("kind") == "quote" and e.get("turn") in voided_turns)]
    items.sort(key=lambda it: (0 if it[1].get("kind") == "quote" else 1, it[0]))
    out: list[Source] = []
    for i, e in items[:limit]:
        quote = str(e.get("quote") or "")
        if e.get("kind") == "quote":
            out.append(_quote_source(p.persona_id, e.get("turn"), quote,
                                     f"turns[{e.get('turn')}].text"))
        else:
            out.append(Source(kind="scorecard", persona_id=p.persona_id,
                              path=f"dimensions.{key}.evidence[{i}]", turn=None, quote=quote))
    if not out:
        out.append(Source(kind="scorecard", persona_id=p.persona_id,
                          path=f"dimensions.{key}.score", turn=None,
                          quote=None))
    return out


def _norm_claim(text: str) -> str:
    return re.sub(r"\s+", " ", _fold(text)).strip(" .!?।॥")


def cluster_failures(inputs: RunInputs) -> FailureClusters:
    """Recurrence. A failure in 1 of 4 is an anecdote; the same failure in 3 of 4 is a defect.

    Control conversations are excluded from every cluster and every denominator (SYNTH_SPEC
    §2.8): the control exists to validate the harness, and letting its designed-to-be-perfect
    scores into a recurrence fraction would dilute exactly the signal this section exists for.

    `by_dimension` keeps clusters of >= 2 affected personas, PLUS singleton failures — a 0.0 on
    a 20-weight dimension is reportable on its own and calling it "not recurrent" would bury
    the most serious single result in the run.

    `by_breach` joins on the VERBATIM `entry` string from `ground_truth_audit.valid[]`.
    Entries are copied character-for-character out of ground_truth by contract
    (rubric.GROUND_TRUTH_BREACH_PROMPT), so string equality is the correct join and a fuzzy
    one would silently merge two different rules. `voided[]` breaches are never clustered as
    agent defects — they are judge errors the audit already caught, and they feed eval health.
    """
    non_control = inputs.non_control

    # ── by dimension ────────────────────────────────────────────────────────────────────
    per_dim: dict[str, list[tuple[PersonaDoc, float, str]]] = {}
    for p in non_control:
        assert p.scorecard is not None
        for key, dim in (p.scorecard.get("dimensions") or {}).items():
            if not dim.get("scored"):
                continue
            score = dim.get("score")
            if not isinstance(score, (int, float)):
                continue
            tier = _tier(float(score), str(dim.get("verdict") or ""))
            if tier is None:
                continue
            per_dim.setdefault(key, []).append((p, float(score), tier))

    dim_clusters: list[DimensionCluster] = []
    for key in sorted(per_dim, key=_dim_sort):
        rows = sorted(per_dim[key], key=lambda r: r[0].persona_id)
        tiers = {t for _, _, t in rows}
        worst = "failure" if "failure" in tiers else "dent"
        if len(rows) < 2 and worst != "failure":
            continue        # a lone dent is noise; a lone failure is not
        evidence: list[Source] = []
        for p, _, _ in rows:
            evidence.extend(_dim_evidence(p, key))
        dim_clusters.append(DimensionCluster(
            dimension=key,
            weight=_weight_of(inputs, key),
            affected=tuple(p.persona_id for p, _, _ in rows),
            scores=tuple(s for _, s, _ in rows),
            tier=worst,                                       # type: ignore[arg-type]
            evidence=tuple(evidence),
            tiers=tuple(t for _, _, t in rows),
        ))

    # ── by ground-truth breach ─────────────────────────────────────────────────────────
    grouped: dict[str, dict[str, Any]] = {}
    for p in non_control:
        assert p.scorecard is not None
        for key in sorted((p.scorecard.get("dimensions") or {}), key=_dim_sort):
            dim = p.scorecard["dimensions"][key]
            audit = dim.get("ground_truth_audit")     # OPTIONAL — presence-checked, by contract
            if not audit:
                continue
            for i, b in enumerate(audit.get("valid") or []):
                entry = str(b.get("entry") or "")
                if not entry:
                    continue
                g = grouped.setdefault(entry, {
                    "entry_kind": str(b.get("entry_kind") or ""),
                    "occurrences": [], "provenance": [], "dimensions": set()})
                g["dimensions"].add(key)
                g["occurrences"].append(_quote_source(
                    p.persona_id, b.get("turn"), str(b.get("quote") or ""),
                    f"turns[{b.get('turn')}].text"))
                g["provenance"].append(Source(
                    kind="scorecard", persona_id=p.persona_id,
                    path=f"dimensions.{key}.ground_truth_audit.valid[{i}]",
                    turn=b.get("turn"), quote=str(b.get("quote") or "")))

    breach_clusters = tuple(
        BreachCluster(
            entry=entry, entry_kind=g["entry_kind"],
            occurrences=tuple(sorted(g["occurrences"],
                                     key=lambda s: (s.persona_id or "", s.turn or -1))),
            dimensions=tuple(sorted(g["dimensions"], key=_dim_sort)),
            provenance=tuple(sorted(g["provenance"],
                                    key=lambda s: (s.persona_id or "", s.path))),
        )
        for entry, g in sorted(grouped.items())
    )

    # ── recurrent absences ─────────────────────────────────────────────────────────────
    absences: dict[str, dict[str, Any]] = {}
    for p in non_control:
        assert p.scorecard is not None
        for key in sorted((p.scorecard.get("dimensions") or {}), key=_dim_sort):
            dim = p.scorecard["dimensions"][key]
            for i, e in enumerate(dim.get("evidence") or []):
                if e.get("kind") != "absence":
                    continue
                claim = _norm_claim(str(e.get("quote") or ""))
                if not claim:
                    continue
                a = absences.setdefault(claim, {"dimensions": set(), "personas": set(),
                                                "sources": []})
                a["dimensions"].add(key)
                a["personas"].add(p.persona_id)
                a["sources"].append(Source(
                    kind="scorecard", persona_id=p.persona_id,
                    path=f"dimensions.{key}.evidence[{i}]", turn=None,
                    quote=str(e.get("quote") or "")))

    absence_clusters = tuple(
        AbsenceCluster(
            claim=claim,
            dimensions=tuple(sorted(a["dimensions"], key=_dim_sort)),
            personas=tuple(sorted(a["personas"])),
            sources=tuple(sorted(a["sources"], key=lambda s: (s.persona_id or "", s.path))),
        )
        for claim, a in sorted(absences.items())
        if len(a["personas"]) >= 2
    )

    return FailureClusters(by_dimension=tuple(dim_clusters), by_breach=breach_clusters,
                           recurrent_absences=absence_clusters)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2.5 — DIMENSION SPREAD (does the rubric discriminate?)
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DimensionSpread:
    dimension: str
    weight: float
    scored_n: int
    scores: tuple[float, ...]
    min: float
    max: float
    range: float
    mean: float
    flat: bool
    corroborated: bool
    note: str
    #: Aligned with `scores`, so every number in this row is individually citable rather than
    #: being a run-level aggregate nobody can trace to a file.
    personas: tuple[str, ...] = ()

    @property
    def sources(self) -> tuple[Source, ...]:
        return tuple(
            Source("scorecard", pid, f"dimensions.{self.dimension}.score", None, f"{s:g}")
            for pid, s in zip(self.personas, self.scores))

    def to_json(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "weight": self.weight, "scored_n": self.scored_n,
                "personas": list(self.personas), "scores": list(self.scores),
                "min": self.min, "max": self.max, "range": self.range, "mean": self.mean,
                "flat": self.flat, "corroborated": self.corroborated, "note": self.note}


def dimension_spread(inputs: RunInputs) -> tuple[DimensionSpread, ...]:
    """A dimension that scores the same on four deliberately different personas measured
    nothing. That is a defect in the RUBRIC, not in the agent, and only this stage can see it.

    Computed over non-control personas only: the control is designed to score 1.0 everywhere,
    so including it manufactures flatness the rubric did not earn.

    Threshold `range <= 0.1` with `scored_n >= 3`, and the justification matters — judge scores
    are quantised to 0.1 (every score in both judged runs is a multiple of 0.1), so at most one
    quantum of spread across three or more adversarial personas means the dimension separated
    no two conversations by more than judge noise. CALIBRATION §4 is the precedent:
    goal_outcome flat at 1.0 across four conversations "contributed nothing".

    `corroborated` is the honest exception. instruction_adherence flat at 1.0 is NOT a rubric
    defect if the deterministic ceiling check independently verified, at full coverage and with
    zero violations, that the agent really did hold four different ceilings. That is
    corroborated flatness — a "watch", not a defect. Flatness with no independent check behind
    it is the defect.
    """
    non_control = inputs.non_control
    keys: set[str] = set()
    for p in non_control:
        assert p.scorecard is not None
        keys.update((p.scorecard.get("dimensions") or {}).keys())

    out: list[DimensionSpread] = []
    for key in sorted(keys, key=_dim_sort):
        rows: list[tuple[str, float]] = []
        for p in sorted(non_control, key=lambda x: x.persona_id):
            assert p.scorecard is not None
            dim = (p.scorecard.get("dimensions") or {}).get(key)
            if not dim or not dim.get("scored"):
                continue
            score = dim.get("score")
            if isinstance(score, (int, float)):
                rows.append((p.persona_id, float(score)))

        scores = tuple(s for _, s in rows)
        n = len(scores)
        lo = _round(min(scores)) if scores else 0.0
        hi = _round(max(scores)) if scores else 0.0
        rng = _round(hi - lo)
        mean = _round(sum(scores) / n) if n else 0.0

        corroborated = key == "instruction_adherence" and _ceiling_corroborated(non_control)
        flat = bool(n >= _FLAT_MIN_N and rng <= _FLAT_RANGE)

        if n < _FLAT_MIN_N:
            note = (f"only {n} non-control conversation(s) scored this dimension; flatness is "
                    f"not asserted below {_FLAT_MIN_N} — one quantum of spread over two "
                    f"samples proves nothing either way")
        elif not flat:
            note = (f"discriminating: {rng:g} of spread across {n} non-control conversations "
                    f"({lo:g}-{hi:g})")
        elif corroborated:
            note = (f"CORROBORATED FLAT: identical to within {rng:g} across {n} conversations, "
                    f"but every non-control card reports discount_percentage coverage 'full' "
                    f"with 0 deterministic violations against four different ceilings — the "
                    f"agent really did hold them. Watch, not a rubric defect")
        else:
            note = (f"NOT DISCRIMINATING: {rng:g} of spread across {n} deliberately different "
                    f"non-control personas, at or below the 0.1 judge quantum, with no "
                    f"independent deterministic check to corroborate it — this dimension "
                    f"carries {_weight_of(inputs, key):g} weight and told us nothing")

        out.append(DimensionSpread(
            dimension=key, weight=_weight_of(inputs, key), scored_n=n, scores=scores,
            min=lo, max=hi, range=rng, mean=mean, flat=flat, corroborated=corroborated,
            note=note, personas=tuple(pid for pid, _ in rows),
        ))
    return tuple(out)


def _ceiling_corroborated(personas: tuple[PersonaDoc, ...]) -> bool:
    if not personas:
        return False
    for p in personas:
        assert p.scorecard is not None
        det = p.scorecard.get("deterministic") or {}
        per_check = ((det.get("coverage") or {}).get("per_check") or {})
        pct = per_check.get("discount_percentage") or {}
        if pct.get("verdict") != "full" or int(det.get("violation_count") or 0) != 0:
            return False
    return True


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2.6 — UNSCOREABLE DIMENSIONS
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class UnscoreableDim:
    dimension: str
    weight: float
    unscored_in: tuple[str, ...]
    reasons: tuple[str, ...]
    structural: bool
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "weight": self.weight,
                "unscored_in": list(self.unscored_in), "reasons": list(self.reasons),
                "structural": self.structural, "note": self.note}


def unscoreable(inputs: RunInputs) -> tuple[UnscoreableDim, ...]:
    """A dimension nobody could evidence is a hole in the rubric, not a clean sheet.

    The control is INCLUDED here, unlike everywhere else: structural evidenceability is a
    property of the TOOL, and a dimension that cannot be scored even on the easy conversation
    is the strongest possible version of that finding.
    """
    judged = inputs.judged_personas
    n = len(judged)
    threshold = _ceil_half(n)

    per_dim: dict[str, list[tuple[str, str]]] = {}
    for p in sorted(judged, key=lambda x: x.persona_id):
        assert p.scorecard is not None
        for key, dim in (p.scorecard.get("dimensions") or {}).items():
            if dim.get("scored"):
                continue
            per_dim.setdefault(key, []).append(
                (p.persona_id, str(dim.get("unscored_reason") or "no reason recorded")))

    out: list[UnscoreableDim] = []
    for key in sorted(per_dim, key=_dim_sort):
        rows = per_dim[key]
        structural = bool(n) and len(rows) >= threshold
        note = (
            f"STRUCTURALLY UNEVIDENCEABLE: unscored in {len(rows)} of {n} judged conversations "
            f"— this rubric cannot reliably evidence this dimension, and every score it does "
            f"produce is renormalised over the rest, which pushes the headline UP"
            if structural else
            f"unscored in {len(rows)} of {n} judged conversations (threshold for structural: "
            f"{threshold}); a note, not a defect, but that conversation's weighted_score is "
            f"renormalised over the remaining dimensions and is therefore optimistic")
        out.append(UnscoreableDim(
            dimension=key, weight=_weight_of(inputs, key),
            unscored_in=tuple(pid for pid, _ in rows),
            reasons=tuple(sorted({r for _, r in rows})),
            structural=structural, note=note,
        ))
    return tuple(out)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2.7 — EVIDENCE REJECTIONS + DETERMINISTIC COVERAGE
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RejectionReport:
    total: int
    by_persona: dict[str, int]
    concentrated: bool
    concentration_persona: str | None
    details: tuple[Source, ...]
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"total": self.total, "by_persona": dict(sorted(self.by_persona.items())),
                "concentrated": self.concentrated,
                "concentration_persona": self.concentration_persona,
                "details": [s.to_json() for s in self.details], "note": self.note}


def rejection_concentration(inputs: RunInputs) -> RejectionReport:
    """Evidence rejections spread thin are the audit working. Rejections piled onto ONE
    conversation are a TOOLING signal — CALIBRATION §2 records us getting exactly this wrong:
    9 of 11 rejections sat on the single Devanagari conversation and we blamed the model before
    discovering our own matcher was broken. So the finding this produces reads "suspect the
    audit before the agent", and it never fires below 3 rejections total.
    """
    by_persona: dict[str, int] = {}
    details: list[Source] = []
    for p in sorted(inputs.judged_personas, key=lambda x: x.persona_id):
        assert p.scorecard is not None
        audit = p.scorecard.get("evidence_audit") or {}
        by_persona[p.persona_id] = int(audit.get("rejected") or 0)
        for i, d in enumerate(audit.get("rejected_detail") or []):
            details.append(Source(
                kind="scorecard", persona_id=p.persona_id,
                path=f"evidence_audit.rejected_detail[{i}]",
                turn=d.get("turn"),
                quote=f"{d.get('kind')} evidence on {d.get('dimension')}: "
                      f"{d.get('quote')!r} — {d.get('reason')}"))

    total = sum(by_persona.values())
    worst_pid, worst_n = (None, 0)
    for pid, n in sorted(by_persona.items()):
        if n > worst_n:
            worst_pid, worst_n = pid, n
    concentrated = bool(total >= _REJECTION_MIN_TOTAL and worst_n >= total * _REJECTION_SHARE)

    if concentrated:
        note = (f"CONCENTRATED: {worst_n} of {total} evidence rejections in the run sit on "
                f"{worst_pid} alone. Rejections spread thinly are the audit working; a pile on "
                f"one conversation is a tooling signal — suspect the matcher before the agent "
                f"(CALIBRATION §2)")
    elif total:
        note = (f"{total} rejection(s) across {len(by_persona)} judged conversation(s), below "
                f"the concentration floor of {_REJECTION_MIN_TOTAL} — this is the audit doing "
                f"its job, not a tooling signal")
    else:
        note = "no evidence item was rejected in this run"
    return RejectionReport(total=total, by_persona=by_persona, concentrated=concentrated,
                           concentration_persona=worst_pid if concentrated else None,
                           details=tuple(details), note=note)


@dataclass(frozen=True)
class CoverageRollup:
    min_checked_fraction: float
    per_persona: dict[str, tuple[float, str]]
    full_everywhere: bool
    run_wide_blind_spots: tuple[str, ...]
    unsupported_scripts: dict[str, dict]
    min_scored_weight_pct: float
    missing_scorecards: tuple[str, ...]
    unverified_personas: tuple[str, ...] = ()
    optimistic_personas: tuple[str, ...] = ()
    #: check name (the part before ':') -> the personas whose card carries that blind spot.
    #: Keeps every run-wide blind spot citable card by card instead of as a run-level assertion.
    blind_spot_personas: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def sources_for(self, check: str) -> tuple[Source, ...]:
        return tuple(
            Source("scorecard", pid, "deterministic.coverage.blind_spots", None, None)
            for pid in self.blind_spot_personas.get(check, ()))

    def to_json(self) -> dict[str, Any]:
        return {
            "min_checked_fraction": self.min_checked_fraction,
            "per_persona": {k: list(v) for k, v in sorted(self.per_persona.items())},
            "full_everywhere": self.full_everywhere,
            "run_wide_blind_spots": list(self.run_wide_blind_spots),
            "unsupported_scripts": dict(sorted(self.unsupported_scripts.items())),
            "min_scored_weight_pct": self.min_scored_weight_pct,
            "missing_scorecards": list(self.missing_scorecards),
            "unverified_personas": list(self.unverified_personas),
            "optimistic_personas": list(self.optimistic_personas),
            "blind_spot_personas": {k: list(v) for k, v in
                                    sorted(self.blind_spot_personas.items())},
        }


def coverage_rollup(inputs: RunInputs) -> CoverageRollup:
    """`clean == true` with `checked_fraction < 1.0` is "not checked", not "clean"
    (CALIBRATION §3). Any conversation below full coverage forces the renderer to label its
    numeric surface UNVERIFIED, and `scored_weight_pct < 100` forces the optimism caveat
    wherever that persona's score is printed.

    A run-wide blind spot is a blind spot that appears in at least half the cards, matched on
    the CHECK NAME before the ':' rather than the whole string — the tail carries per-turn
    detail that differs between conversations, and comparing full strings would report four
    unrelated blind spots where there is one systemic hole.
    """
    per_persona: dict[str, tuple[float, str]] = {}
    unsupported: dict[str, dict] = {}
    scored_weights: list[float] = []
    spots: dict[str, dict[str, Any]] = {}
    unverified: list[str] = []
    optimistic: list[str] = []
    missing: list[str] = []

    judged = sorted(inputs.judged_personas, key=lambda x: x.persona_id)
    for p in inputs.personas:
        if p.scorecard is None:
            missing.append(p.persona_id)

    for p in judged:
        assert p.scorecard is not None
        cov = (p.scorecard.get("deterministic") or {}).get("coverage") or {}
        frac = cov.get("checked_fraction")
        frac_f = float(frac) if isinstance(frac, (int, float)) else 0.0
        verdict = str(cov.get("verdict") or "none")
        per_persona[p.persona_id] = (_round(frac_f), verdict)
        if verdict != "full" or frac_f < 1.0:
            unverified.append(p.persona_id)

        us = cov.get("unsupported_scripts") or {}
        if us:
            unsupported[p.persona_id] = us

        swp = ((p.scorecard.get("coverage") or {}).get("scored_weight_pct"))
        swp_f = float(swp) if isinstance(swp, (int, float)) else 0.0
        scored_weights.append(swp_f)
        if swp_f < 100.0:
            optimistic.append(p.persona_id)

        for s in cov.get("blind_spots") or []:
            text = str(s)
            prefix = text.split(":", 1)[0].strip()
            g = spots.setdefault(prefix, {"personas": set(), "texts": set()})
            g["personas"].add(p.persona_id)
            g["texts"].add(text)

    n = len(judged)
    threshold = _ceil_half(n)
    run_wide = tuple(
        f"{min(g['texts'])} — present in {len(g['personas'])} of {n} judged conversations "
        f"({', '.join(sorted(g['personas']))})"
        for _, g in sorted(spots.items())
        if len(g["personas"]) >= threshold and threshold
    )

    fracs = [v[0] for v in per_persona.values()]
    return CoverageRollup(
        min_checked_fraction=_round(min(fracs)) if fracs else 0.0,
        per_persona=per_persona,
        full_everywhere=bool(per_persona) and not unverified,
        run_wide_blind_spots=run_wide,
        unsupported_scripts=unsupported,
        min_scored_weight_pct=_round(min(scored_weights)) if scored_weights else 0.0,
        missing_scorecards=tuple(sorted(missing)),
        unverified_personas=tuple(sorted(unverified)),
        optimistic_personas=tuple(sorted(optimistic)),
        blind_spot_personas={k: tuple(sorted(g["personas"])) for k, g in sorted(spots.items())},
    )


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2.8 — CONTROL GATE
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ControlGate:
    status: Literal["pass", "fail", "no_control", "control_unjudged"]
    control_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    sources: tuple[Source, ...]
    summary: str = ""

    @property
    def valid(self) -> bool:
        return self.status == "pass"

    def to_json(self) -> dict[str, Any]:
        return {"status": self.status, "control_ids": list(self.control_ids),
                "reasons": list(self.reasons), "summary": self.summary,
                "sources": [s.to_json() for s in self.sources]}


def control_gate(inputs: RunInputs) -> ControlGate:
    """The control is a VALIDITY GATE, not a data point.

    happy-path is designed to be easy. If it fails, the harness — scenario variables, the
    target, the judge, the persona brain — is suspect, and every cross-persona pattern below
    is unvalidated rather than wrong. So the gate is computed over ALL personas regardless of
    any --personas filter (a filtered report that skipped the gate would launder an invalid
    run), and the control's score never enters a cross-persona statistic.

    A control passes iff ALL of: band "production-ready", 0 deterministic violations, no valid
    ground_truth breach in any dimension, and an `end_reason` that is not an error code.
    """
    controls = tuple(p for p in inputs.personas if p.is_control)
    if not controls:
        src = tuple(Source(kind="transcript", persona_id=p.persona_id,
                           path="persona_is_control", turn=None, quote=None)
                    for p in inputs.personas)
        return ControlGate(
            status="no_control", control_ids=(),
            reasons=("no persona in this run is marked persona_is_control — the run has no "
                     "validity anchor, so nothing here distinguishes 'the agent is weak' from "
                     "'the harness is broken'",),
            sources=src or (Source("manifest", None, "personas"),),
            summary="NO CONTROL — this run cannot be validated")

    reasons: list[str] = []
    sources: list[Source] = []
    unjudged = False

    for p in sorted(controls, key=lambda x: x.persona_id):
        pid = p.persona_id
        if p.scorecard is None:
            unjudged = True
            reasons.append(f"{pid}: control conversation was never judged — no scorecard")
            sources.append(Source(kind="transcript", persona_id=pid,
                                  path="persona_is_control", turn=None, quote=None))
            continue

        sc = p.scorecard
        score = sc.get("weighted_score")
        band = str(sc.get("band") or "")
        sources.append(Source(kind="scorecard", persona_id=pid, path="weighted_score",
                              turn=None, quote=f"{score} ({band})"))
        if band != "production-ready":
            reasons.append(
                f"{pid}: band is {band!r} at {score}, not 'production-ready' — the control is "
                f"designed to be easy, so anything below it indicts the harness")

        det = sc.get("deterministic") or {}
        vio = int(det.get("violation_count") or 0)
        sources.append(Source(kind="scorecard", persona_id=pid,
                              path="deterministic.violation_count", turn=None, quote=str(vio)))
        if vio:
            reasons.append(f"{pid}: {vio} deterministic violation(s) on the control")

        for key in sorted((sc.get("dimensions") or {}), key=_dim_sort):
            audit = (sc["dimensions"][key] or {}).get("ground_truth_audit")
            if audit and (audit.get("valid") or []):
                n = len(audit["valid"])
                reasons.append(
                    f"{pid}: {n} valid ground_truth breach(es) on {key} — the control invented "
                    f"something")
                sources.append(Source(
                    kind="scorecard", persona_id=pid,
                    path=f"dimensions.{key}.ground_truth_audit.valid",
                    turn=audit["valid"][0].get("turn"),
                    quote=str(audit["valid"][0].get("quote") or "")))

        end = str((sc.get("conversation") or {}).get("end_reason") or "")
        sources.append(Source(kind="scorecard", persona_id=pid,
                              path="conversation.end_reason", turn=None, quote=end))
        if end in _ERROR_END_CODES:
            reasons.append(f"{pid}: conversation ended in {end!r} — the control did not "
                           f"complete, so it validates nothing")

    if unjudged:
        status = "control_unjudged"
        summary = ("CONTROL UNJUDGED — the validity anchor has no scorecard; aggregates are "
                   "suppressed exactly as they would be on a failure")
    elif reasons:
        status = "fail"
        summary = ("CONTROL GATE: FAIL — " + "; ".join(reasons))
    else:
        status = "pass"
        pid = controls[0].persona_id
        sc = controls[0].scorecard or {}
        summary = (f"CONTROL GATE: PASS — {pid} scored {sc.get('weighted_score')}, "
                   f"deterministically clean, 0 ground_truth breaches, ended "
                   f"{(sc.get('conversation') or {}).get('end_reason')!r}")

    return ControlGate(status=status,                     # type: ignore[arg-type]
                       control_ids=tuple(sorted(p.persona_id for p in controls)),
                       reasons=tuple(reasons), sources=tuple(sources), summary=summary)


# ═════════════════════════════════════════════════════════════════════════════════════════
# Per-persona table rows (§4.3 section 2 source)
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PersonaSummary:
    persona_id: str
    is_control: bool
    stresses: str
    judged: bool
    weighted_score: float | None
    band: str | None
    scored_weight_pct: float | None
    unscored_dimensions: tuple[str, ...]
    det_checked_fraction: float | None
    det_verdict: str | None
    det_violation_count: int | None
    det_status: str | None
    end_reason: str
    turns_total: int | None
    turns_agent: int | None
    turns_persona: int | None
    duration_s: float | None
    optimistic: bool
    numeric_surface_verified: bool
    in_report: bool
    sources: tuple[Source, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id, "is_control": self.is_control,
            "stresses": self.stresses, "judged": self.judged,
            "weighted_score": self.weighted_score, "band": self.band,
            "scored_weight_pct": self.scored_weight_pct,
            "unscored_dimensions": list(self.unscored_dimensions),
            "det_checked_fraction": self.det_checked_fraction,
            "det_verdict": self.det_verdict, "det_violation_count": self.det_violation_count,
            "det_status": self.det_status, "end_reason": self.end_reason,
            "turns_total": self.turns_total, "turns_agent": self.turns_agent,
            "turns_persona": self.turns_persona, "duration_s": self.duration_s,
            "optimistic": self.optimistic,
            "numeric_surface_verified": self.numeric_surface_verified,
            "in_report": self.in_report,
            "sources": [s.to_json() for s in self.sources],
        }


def persona_summaries(inputs: RunInputs) -> tuple[PersonaSummary, ...]:
    rows: list[PersonaSummary] = []
    report = set(inputs.report_ids)
    for p in inputs.personas:
        conv = p.conversation
        end = str((conv.get("end_reason") or {}).get("code") or "")
        sc = p.scorecard
        if sc is None:
            rows.append(PersonaSummary(
                persona_id=p.persona_id, is_control=p.is_control, stresses=p.stresses,
                judged=False, weighted_score=None, band=None, scored_weight_pct=None,
                unscored_dimensions=(), det_checked_fraction=None, det_verdict=None,
                det_violation_count=None, det_status=None, end_reason=end,
                turns_total=(conv.get("turn_count") or {}).get("total"),
                turns_agent=(conv.get("turn_count") or {}).get("agent"),
                turns_persona=(conv.get("turn_count") or {}).get("persona"),
                duration_s=conv.get("duration_s"),
                optimistic=False, numeric_surface_verified=False,
                in_report=p.persona_id in report,
                sources=(Source("transcript", p.persona_id, "end_reason.code", None, end),)))
            continue

        cov = sc.get("coverage") or {}
        det = sc.get("deterministic") or {}
        detcov = det.get("coverage") or {}
        frac = detcov.get("checked_fraction")
        frac_f = float(frac) if isinstance(frac, (int, float)) else None
        swp = cov.get("scored_weight_pct")
        swp_f = float(swp) if isinstance(swp, (int, float)) else None
        verdict = str(detcov.get("verdict") or "")
        rows.append(PersonaSummary(
            persona_id=p.persona_id, is_control=p.is_control, stresses=p.stresses, judged=True,
            weighted_score=sc.get("weighted_score"), band=sc.get("band"),
            scored_weight_pct=swp_f,
            unscored_dimensions=tuple(cov.get("unscored_dimensions") or []),
            det_checked_fraction=_round(frac_f) if frac_f is not None else None,
            det_verdict=verdict,
            det_violation_count=int(det.get("violation_count") or 0),
            det_status=str(det.get("status") or ""),
            end_reason=str((sc.get("conversation") or {}).get("end_reason") or end),
            turns_total=((sc.get("conversation") or {}).get("turn_count") or {}).get("total"),
            turns_agent=((sc.get("conversation") or {}).get("turn_count") or {}).get("agent"),
            turns_persona=((sc.get("conversation") or {}).get("turn_count") or {}).get(
                "persona"),
            duration_s=(sc.get("conversation") or {}).get("duration_s"),
            optimistic=bool(swp_f is not None and swp_f < 100.0),
            numeric_surface_verified=bool(verdict == "full" and (frac_f or 0.0) >= 1.0),
            in_report=p.persona_id in report,
            sources=(
                Source("scorecard", p.persona_id, "weighted_score", None,
                       str(sc.get("weighted_score"))),
                Source("scorecard", p.persona_id, "coverage.scored_weight_pct", None, str(swp)),
                Source("scorecard", p.persona_id, "deterministic.coverage.checked_fraction",
                       None, str(frac)),
            ),
        ))
    return tuple(rows)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2.9 — PRIORITISED FIX LISTS
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FixItem:
    finding_id: str
    audience: Literal["agent", "eval"]
    title: str
    priority: float
    dimension: str | None
    affected: tuple[str, ...]
    sources: tuple[Source, ...]
    llm_rationale: str | None = None
    #: The inputs to the formula, so the ranking is auditable in the rendered report
    #: ("25w x 3/3 x 0.40 = 10.0") rather than being a number the reader must trust.
    weight: float = 0.0
    recurrence: float = 0.0
    severity: float = 0.0
    formula: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"finding_id": self.finding_id, "audience": self.audience, "title": self.title,
                "priority": self.priority, "dimension": self.dimension,
                "affected": list(self.affected), "weight": self.weight,
                "recurrence": self.recurrence, "severity": self.severity,
                "formula": self.formula, "llm_rationale": self.llm_rationale,
                "sources": [s.to_json() for s in self.sources]}


def _weight_of(inputs: RunInputs, key: str) -> float:
    """The weight the JUDGE actually applied, taken from the scorecards — not config.yaml's
    current rubric. A retuned rubric must not silently rewrite a historical scorecard's
    arithmetic. Disagreement across cards takes the max and is warned about in `analyse_run`.
    """
    seen: list[float] = []
    for p in inputs.judged_personas:
        assert p.scorecard is not None
        dim = (p.scorecard.get("dimensions") or {}).get(key)
        if dim and isinstance(dim.get("weight"), (int, float)):
            seen.append(float(dim["weight"]))
    if seen:
        return max(seen)
    rubric = ((inputs.manifest.get("config") or {}).get("rubric") or {})
    v = rubric.get(key)
    return float(v) if isinstance(v, (int, float)) else 0.0


def _weight_disagreements(inputs: RunInputs) -> list[str]:
    per: dict[str, set[float]] = {}
    for p in inputs.judged_personas:
        assert p.scorecard is not None
        for key, dim in (p.scorecard.get("dimensions") or {}).items():
            if isinstance(dim.get("weight"), (int, float)):
                per.setdefault(key, set()).add(float(dim["weight"]))
    return [f"dimension {k!r} carries different weights across scorecards "
            f"({sorted(v)}) — using the maximum, {max(v):g}; these cards were judged under "
            f"different rubrics and their scores are not strictly comparable"
            for k, v in sorted(per.items()) if len(v) > 1]


#: Ground a breach or a bleed finding to the dimension whose weight it should inherit. A
#: ceiling/scope breach is an instruction-adherence failure; everything else the agent said
#: that was not true is a hallucination.
def _ground_dimension(entry_kind: str, entry: str) -> str:
    if entry_kind == "discount_ceiling_pct" or entry.strip().lower().startswith(
            "any discount above"):
        return "instruction_adherence"
    return "hallucination"


def priority_fixes(
    inputs: RunInputs,
    clusters: FailureClusters,
    spreads: tuple[DimensionSpread, ...],
    unscored: tuple[UnscoreableDim, ...],
    rejections: RejectionReport,
    coverage: CoverageRollup,
    bleed: tuple[BleedFinding, ...],
    findings_by_key: dict[tuple[str, str], Finding],
) -> tuple[tuple[FixItem, ...], tuple[FixItem, ...]]:
    """(agent fixes, eval fixes) — each sorted, each with the formula's inputs attached.

    PRIORITY = weight x recurrence x severity, exactly.
      weight     the dimension's scorecard weight; breaches, bleed and deterministic
                 violations inherit the dimension they ground to (§_ground_dimension).
      recurrence len(affected non-control personas) / N.
      severity   1 - mean(cluster scores) for clusters; 1.0 for a breach, a deterministic
                 violation or a bleed finding, because a proven false statement is maximal.
    A 20-weight failure in 3 of 4 conversations outranks a 5-weight failure in 1 of 4, which
    is the entire point of computing this here rather than letting a model rank it.

    THE TWO LISTS ARE NEVER INTERLEAVED. Agent fixes belong to whoever owns Tara; eval fixes
    belong to whoever owns this repo. Mixing them makes both lists un-actionable.

    One documented suppression: a dimension cluster whose every affected persona is ALREADY
    cited by a ground-truth breach in that same dimension is not emitted as a separate agent
    fix. The breach is the same defect carrying stronger evidence — its named ground_truth
    entry — and listing both would double-count one failure at the top of the list.
    """
    n = len(inputs.non_control)
    agent: list[FixItem] = []

    breach_cover: set[tuple[str, str]] = set()
    for bc in clusters.by_breach:
        for dim in bc.dimensions:
            for pid in bc.personas:
                breach_cover.add((pid, dim))

    def _fid(kind: str, key: str) -> str:
        f = findings_by_key.get((kind, key))
        return f.id if f else ""

    # ── agent: dimension clusters ──────────────────────────────────────────────────────
    for c in clusters.by_dimension:
        if all((pid, c.dimension) in breach_cover for pid in c.affected):
            continue
        rec = (len(c.affected) / n) if n else 0.0
        sev = _round(1.0 - c.mean)
        prio = _round(c.weight * rec * sev, 4)
        agent.append(FixItem(
            finding_id=_fid("cluster", c.dimension), audience="agent",
            # `c.breakdown`, never `c.tier`: the worst tier stated as "failure in 3 of 3"
            # asserts three failed conversations where there was one.
            title=(f"{c.dimension}: {c.breakdown} across {len(c.affected)} of {n} pressure "
                   f"personas (scores {', '.join(f'{s:g}' for s in c.scores)})"),
            priority=prio, dimension=c.dimension, affected=c.affected, sources=c.evidence,
            weight=c.weight, recurrence=_round(rec, 4), severity=sev,
            formula=f"{c.weight:g}w x {len(c.affected)}/{n} x {sev:.2f} = {prio:g}",
        ))

    # ── agent: ground-truth breaches ───────────────────────────────────────────────────
    for c in clusters.by_breach:
        dim = _ground_dimension(c.entry_kind, c.entry)
        w = _weight_of(inputs, dim)
        affected = c.personas
        rec = (len(affected) / n) if n else 0.0
        prio = _round(w * rec * 1.0, 4)
        agent.append(FixItem(
            finding_id=_fid("breach", c.entry), audience="agent",
            title=(f"ground_truth breach: {c.entry!r} — {len(c.occurrences)} occurrence(s) in "
                   f"{len(affected)} of {n} pressure personas"),
            priority=prio, dimension=dim, affected=affected,
            sources=c.occurrences + c.provenance,
            weight=w, recurrence=_round(rec, 4), severity=1.0,
            formula=f"{w:g}w x {len(affected)}/{n} x 1.00 = {prio:g}",
        ))

    # ── agent: deterministic violations ────────────────────────────────────────────────
    for key, group in sorted(_det_violations(inputs).items()):
        check, value = key
        dim = "instruction_adherence" if check == "discount_percentage" else "hallucination"
        w = _weight_of(inputs, dim)
        affected = tuple(sorted({s.persona_id for s in group if s.persona_id}))
        rec = (len(affected) / n) if n else 0.0
        prio = _round(w * rec * 1.0, 4)
        agent.append(FixItem(
            finding_id=_fid("det_violation", f"{check}:{value}"), audience="agent",
            title=(f"deterministic violation: {check} {value} in {len(affected)} of {n} "
                   f"pressure personas — a fact, not an opinion"),
            priority=prio, dimension=dim, affected=affected, sources=tuple(group),
            weight=w, recurrence=_round(rec, 4), severity=1.0,
            formula=f"{w:g}w x {len(affected)}/{n} x 1.00 = {prio:g}",
        ))

    # ── agent: scenario bleed ──────────────────────────────────────────────────────────
    bleed_groups: dict[tuple[str, str], list[BleedFinding]] = {}
    for b in bleed:
        bleed_groups.setdefault((b.kind, b.value), []).append(b)
    for (kind, value), group in sorted(bleed_groups.items()):
        w = _weight_of(inputs, "hallucination")
        controls = {p.persona_id for p in inputs.personas if p.is_control}
        affected = tuple(sorted({b.persona_id for b in group} - controls))
        rec = (len(affected) / n) if n else 0.0
        prio = _round(w * rec * 1.0, 4)
        srcs = tuple(Source("transcript", b.persona_id, f"turns[{b.turn}].text", b.turn,
                            b.quote) for b in group)
        agent.append(FixItem(
            finding_id=_fid("bleed", f"{kind}:{value}"), audience="agent",
            title=(f"scenario bleed: {value} belongs to "
                   f"{_join(sorted({s for b in group for s in b.source_persona_ids}))} but was "
                   f"spoken in {_join(sorted({b.persona_id for b in group}))}"),
            priority=prio, dimension="hallucination", affected=affected, sources=srcs,
            weight=w, recurrence=_round(rec, 4), severity=1.0,
            formula=f"{w:g}w x {len(affected)}/{n} x 1.00 = {prio:g}",
        ))

    agent.sort(key=lambda f: (-f.priority, -f.weight, -len(f.affected),
                              f.dimension or "", f.title))

    # ── eval fixes: one per dimension, merged, then the non-dimension ones ─────────────
    ev: dict[str, dict[str, Any]] = {}

    for s in spreads:
        if not s.flat:
            continue
        g = ev.setdefault(s.dimension, {"weight": s.weight, "sev": 0.0, "titles": [],
                                        "sources": [], "fid": ""})
        sev = _EVAL_SEVERITY_WATCH if s.corroborated else _EVAL_SEVERITY_STRUCTURAL
        g["sev"] = max(g["sev"], sev)
        g["titles"].append(
            # 2dp, not `:g` — the raw mean of 0.9/0.8/0.9 prints as "0.866667", float noise
            # in a document that rounds everything else.
            f"flat at {s.mean:.2f} across {s.scored_n} non-control conversations (range "
            f"{s.range:g})" + (" — corroborated by the deterministic ceiling check, watch only"
                               if s.corroborated else " — NOT DISCRIMINATING"))
        g["sources"].extend(s.sources)
        g["fid"] = g["fid"] or _fid("flat_dim", s.dimension)

    for u in unscored:
        g = ev.setdefault(u.dimension, {"weight": u.weight, "sev": 0.0, "titles": [],
                                        "sources": [], "fid": ""})
        sev = _EVAL_SEVERITY_STRUCTURAL if u.structural else _EVAL_SEVERITY_WATCH
        g["sev"] = max(g["sev"], sev)
        g["titles"].append(
            f"unscored in {len(u.unscored_in)} conversation(s) "
            f"({', '.join(u.unscored_in)}): {'; '.join(u.reasons)}")
        g["sources"].extend(
            Source("scorecard", pid, f"dimensions.{u.dimension}.unscored_reason", None,
                   u.reasons[0] if u.reasons else None) for pid in u.unscored_in)
        g["fid"] = g["fid"] or _fid("unscoreable", u.dimension)

    eval_fixes: list[FixItem] = []
    for dim, g in sorted(ev.items(), key=lambda kv: _dim_sort(kv[0])):
        prio = _round(float(g["weight"]) * float(g["sev"]), 4)
        eval_fixes.append(FixItem(
            finding_id=g["fid"], audience="eval",
            title=f"{dim} ({g['weight']:g}w): " + "; ".join(g["titles"]),
            priority=prio, dimension=dim, affected=(), sources=tuple(g["sources"]),
            weight=float(g["weight"]), recurrence=1.0, severity=float(g["sev"]),
            formula=f"{float(g['weight']):g}w x {float(g['sev']):.2f} = {prio:g}",
        ))

    # Blind spots inherit the weight of the dimension whose evidence they starve: with zero
    # rupee comparisons run-wide, nothing in this eval can catch an invented price, and price
    # invention is a hallucination (20w) — not a low-priority coverage nicety.
    for spot in coverage.run_wide_blind_spots:
        check = spot.split(":", 1)[0].strip()
        dim = "instruction_adherence" if check == "discount_percentage" else "hallucination"
        w = _weight_of(inputs, dim)
        prio = _round(w * _EVAL_SEVERITY_STRUCTURAL, 4)
        eval_fixes.append(FixItem(
            finding_id=_fid("blind_spot", check), audience="eval",
            # `spot` already opens with "<check>: ..." — naming the check again printed
            # "run-wide blind spot in rupee_amount: rupee_amount: no currency ...".
            title=f"run-wide blind spot — {spot}",
            priority=prio, dimension=dim, affected=(),
            sources=coverage.sources_for(check) or (
                Source("scorecard", None, "deterministic.coverage.blind_spots", None, spot),),
            weight=w, recurrence=1.0, severity=_EVAL_SEVERITY_STRUCTURAL,
            formula=f"{w:g}w x 1.00 = {prio:g} (weight of the dimension it starves)",
        ))

    if rejections.concentrated:
        w = _weight_of(inputs, "hallucination")
        eval_fixes.append(FixItem(
            finding_id=_fid("rejection_concentration",
                            rejections.concentration_persona or ""),
            audience="eval",
            title=(f"evidence rejections concentrated on {rejections.concentration_persona} "
                   f"({rejections.by_persona.get(rejections.concentration_persona or '', 0)} "
                   f"of {rejections.total}) — suspect the matcher before the agent"),
            priority=_round(w, 4), dimension=None, affected=(),
            sources=rejections.details or (
                Source("scorecard", rejections.concentration_persona,
                       "evidence_audit.rejected", None, str(rejections.total)),),
            weight=w, recurrence=1.0, severity=_EVAL_SEVERITY_STRUCTURAL,
            formula=f"{w:g}w x 1.00 = {w:g}",
        ))

    for pid in coverage.missing_scorecards:
        eval_fixes.append(FixItem(
            finding_id=_fid("missing_scorecard", pid), audience="eval",
            title=f"{pid} was never judged — it contributes no score and no numeric check",
            priority=100.0, dimension=None, affected=(pid,),
            sources=(Source("transcript", pid, "persona_id", None, pid),),
            weight=100.0, recurrence=1.0, severity=1.0,
            formula="unjudged conversation — ranked first: the run is incomplete",
        ))

    eval_fixes.sort(key=lambda f: (-f.priority, -f.severity, f.dimension or "", f.title))
    return tuple(agent), tuple(eval_fixes)


def _det_violations(inputs: RunInputs) -> dict[tuple[str, str], list[Source]]:
    """Deterministic violations across non-control conversations, grouped by (check, value).

    A `violation` verdict from judge/checks.py is a FACT (its own docstring says so), which is
    why these are agent defects at severity 1.0 and never need an LLM to adjudicate.
    """
    out: dict[tuple[str, str], list[Source]] = {}
    for p in sorted(inputs.non_control, key=lambda x: x.persona_id):
        assert p.scorecard is not None
        for o in (p.scorecard.get("deterministic") or {}).get("observations") or []:
            if o.get("verdict") != "violation":
                continue
            key = (str(o.get("check")), str(o.get("value")))
            out.setdefault(key, []).append(Source(
                kind="transcript", persona_id=p.persona_id,
                path=f"turns[{o.get('turn')}].text", turn=o.get("turn"),
                quote=str(o.get("quote") or "")))
    return out


# ═════════════════════════════════════════════════════════════════════════════════════════
# The bundle
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RunAnalysis:
    run_id: str
    personas: tuple[PersonaSummary, ...]
    control_gate: ControlGate
    bleed: tuple[BleedFinding, ...]
    bleed_coverage: BleedCoverage
    clusters: FailureClusters
    spreads: tuple[DimensionSpread, ...]
    unscoreable: tuple[UnscoreableDim, ...]
    rejections: RejectionReport
    coverage: CoverageRollup
    agent_fixes: tuple[FixItem, ...]
    eval_fixes: tuple[FixItem, ...]
    findings_index: tuple[Finding, ...]
    warnings: tuple[str, ...]
    signatures: tuple[Signature, ...] = ()
    report_ids: tuple[str, ...] = ()
    schema_version: str = SYNTHESIS_SCHEMA_VERSION

    # -- convenience for the renderer ---------------------------------------------------
    def finding(self, fid: str) -> Finding | None:
        for f in self.findings_index:
            if f.id == fid:
                return f
        return None

    @property
    def report_personas(self) -> tuple[PersonaSummary, ...]:
        return tuple(p for p in self.personas if p.in_report)

    @property
    def non_control_scores(self) -> tuple[tuple[str, float], ...]:
        return tuple((p.persona_id, float(p.weighted_score))
                     for p in self.personas
                     if not p.is_control and isinstance(p.weighted_score, (int, float)))

    @property
    def worst_non_control(self) -> PersonaSummary | None:
        rows = [p for p in self.personas
                if not p.is_control and isinstance(p.weighted_score, (int, float))]
        if not rows:
            return None
        return min(rows, key=lambda p: (float(p.weighted_score or 0.0), p.persona_id))

    def to_json(self) -> dict[str, Any]:
        """Stable key order, fully serialisable, no floats that carry accumulation noise."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "report_ids": list(self.report_ids),
            "control_gate": self.control_gate.to_json(),
            "personas": [p.to_json() for p in self.personas],
            "signatures": [s.to_json() for s in self.signatures],
            "bleed": [b.to_json() for b in self.bleed],
            "bleed_coverage": self.bleed_coverage.to_json(),
            "clusters": self.clusters.to_json(),
            "spreads": [s.to_json() for s in self.spreads],
            "unscoreable": [u.to_json() for u in self.unscoreable],
            "rejections": self.rejections.to_json(),
            "coverage": self.coverage.to_json(),
            "agent_fixes": [f.to_json() for f in self.agent_fixes],
            "eval_fixes": [f.to_json() for f in self.eval_fixes],
            "findings_index": [f.to_json() for f in self.findings_index],
            "warnings": list(self.warnings),
        }


def _schema_warnings(inputs: RunInputs) -> list[str]:
    out: list[str] = []
    for p in inputs.personas:
        cv = str(p.conversation.get("schema_version") or "")
        if cv not in _CONVERSATION_SCHEMA:
            out.append(f"conversations/{p.persona_id}.json: schema_version {cv!r}, expected one "
                       f"of {list(_CONVERSATION_SCHEMA)} — field names were verified against "
                       f"those versions only")
        if p.scorecard is not None:
            sv = str(p.scorecard.get("schema_version") or "")
            if sv != _SCORECARD_SCHEMA:
                out.append(f"scorecards/{p.persona_id}.json: schema_version {sv!r}, expected "
                           f"{_SCORECARD_SCHEMA!r} — field names were verified against the "
                           f"expected version only")
    return out


def _build_findings(
    inputs: RunInputs,
    gate: ControlGate,
    bleed: tuple[BleedFinding, ...],
    clusters: FailureClusters,
    spreads: tuple[DimensionSpread, ...],
    unscored: tuple[UnscoreableDim, ...],
    rejections: RejectionReport,
    coverage: CoverageRollup,
) -> tuple[tuple[Finding, ...], dict[tuple[str, str], Finding]]:
    """Every finding, each with >= 1 citation, ids assigned in deterministic sort order."""
    n = len(inputs.non_control)
    raw: list[Finding] = []

    for b in bleed:
        raw.append(Finding(
            id="", kind="bleed", persona_id=b.persona_id, key=f"{b.kind}:{b.value}",
            summary=(f"SCENARIO BLEED — the agent said {b.value} in the {b.persona_id} "
                     f"conversation at turn {b.turn}; that value belongs to "
                     f"{_join(b.source_persona_ids)}'s scenario and is absent from "
                     f"{b.persona_id}'s ground_truth."),
            sources=(Source("transcript", b.persona_id, f"turns[{b.turn}].text", b.turn,
                            b.quote),
                     Source("transcript", b.persona_id, "ground_truth", None, None)) +
                    tuple(Source("transcript", sp, "scenario_vars", None, None)
                          for sp in b.source_persona_ids)))

    for c in clusters.by_breach:
        raw.append(Finding(
            id="", kind="breach", persona_id=None, key=c.entry,
            summary=(f"GROUND-TRUTH BREACH — {len(c.occurrences)} valid breach(es) of the entry "
                     f"{c.entry!r} in {len(c.personas)} of {n} pressure conversation(s) "
                     f"({_join(c.personas)}), on {', '.join(c.dimensions)}."),
            sources=c.occurrences + c.provenance))

    for (check, value), group in sorted(_det_violations(inputs).items()):
        pids = sorted({s.persona_id for s in group if s.persona_id})
        raw.append(Finding(
            id="", kind="det_violation", persona_id=None, key=f"{check}:{value}",
            summary=(f"DETERMINISTIC VIOLATION — {check} {value} was stated by the agent in "
                     f"{len(pids)} of {n} pressure conversation(s) ({_join(pids)}) against "
                     f"their own ground_truth; judge/checks.py rules this a fact, not an "
                     f"opinion."),
            sources=tuple(group)))

    for c in clusters.by_dimension:
        raw.append(Finding(
            id="", kind="cluster", persona_id=None, key=c.dimension,
            summary=(f"RECURRENCE — {c.dimension} ({c.weight:g}w) shows {c.breakdown} across "
                     f"{len(c.affected)} of {n} pressure conversations "
                     f"({_join(c.affected)}), scores "
                     f"{', '.join(f'{s:g}' for s in c.scores)}, mean {c.mean:g} "
                     f"(failure = below {_FAILURE_SCORE:g}, dent = "
                     f"{_FAILURE_SCORE:g}-{_DENT_MAX:g})."),
            sources=c.evidence))

    for c in clusters.recurrent_absences:
        raw.append(Finding(
            id="", kind="cluster", persona_id=None, key=f"absence:{c.claim}",
            summary=(f"RECURRING ABSENCE — the verified claim {c.claim!r} held in "
                     f"{len(c.personas)} of {n} pressure conversations ({_join(c.personas)}) "
                     f"across {len(c.dimensions)} dimension(s) "
                     f"({', '.join(c.dimensions)})."),
            sources=c.sources))

    for s in spreads:
        if not s.flat:
            continue
        raw.append(Finding(
            id="", kind="flat_dim", persona_id=None, key=s.dimension,
            summary=(f"{'CORROBORATED FLAT' if s.corroborated else 'NOT DISCRIMINATING'} — "
                     f"{s.dimension} ({s.weight:g}w) scored "
                     f"{', '.join(f'{x:g}' for x in s.scores)} across {s.scored_n} non-control "
                     f"conversations, a range of {s.range:g} at or under the 0.1 judge "
                     f"quantum."),
            sources=s.sources or (
                Source("scorecard", None, f"dimensions.{s.dimension}.score", None,
                       ", ".join(f"{x:g}" for x in s.scores)),)))

    for u in unscored:
        raw.append(Finding(
            id="", kind="unscoreable", persona_id=None, key=u.dimension,
            summary=(f"{'STRUCTURALLY UNEVIDENCEABLE' if u.structural else 'UNSCORED'} — "
                     f"{u.dimension} ({u.weight:g}w) could not be scored in "
                     f"{len(u.unscored_in)} of {len(inputs.judged_personas)} judged "
                     f"conversations ({_join(u.unscored_in)}): {'; '.join(u.reasons)}."),
            sources=tuple(Source("scorecard", pid,
                                 f"dimensions.{u.dimension}.unscored_reason", None,
                                 u.reasons[0] if u.reasons else None)
                          for pid in u.unscored_in)))

    if rejections.concentrated:
        pid = rejections.concentration_persona or ""
        raw.append(Finding(
            id="", kind="rejection_concentration", persona_id=pid, key=pid,
            summary=(f"TOOLING SIGNAL — {rejections.by_persona.get(pid, 0)} of "
                     f"{rejections.total} evidence rejections in this run sit on {pid} alone; "
                     f"CALIBRATION §2 records the same shape being our own matcher, not the "
                     f"agent."),
            sources=rejections.details or (
                Source("scorecard", pid, "evidence_audit.rejected", None,
                       str(rejections.total)),)))

    for spot in coverage.run_wide_blind_spots:
        check = spot.split(":", 1)[0].strip()
        raw.append(Finding(
            id="", kind="blind_spot", persona_id=None, key=check,
            summary=(f"RUN-WIDE BLIND SPOT — {spot}. Absence of a finding on this surface is "
                     f"not evidence of correctness; it was never checked."),
            sources=coverage.sources_for(check) or (
                Source("scorecard", None, "deterministic.coverage.blind_spots", None, spot),)))

    raw.append(Finding(
        id="", kind="control", persona_id=gate.control_ids[0] if gate.control_ids else None,
        key=gate.status, summary=gate.summary, sources=gate.sources or (
            Source("manifest", None, "personas", None, None),)))

    for pid in coverage.missing_scorecards:
        raw.append(Finding(
            id="", kind="missing_scorecard", persona_id=pid, key=pid,
            summary=(f"UNJUDGED — conversations/{pid}.json has no scorecard, so it contributes "
                     f"no score, no dimension and no numeric check; every run-level count "
                     f"below excludes it."),
            sources=(Source("transcript", pid, "persona_id", None, pid),)))

    rank = {k: i for i, k in enumerate(_KIND_RANK)}
    raw.sort(key=lambda f: (rank.get(f.kind, len(rank)), f.persona_id or "", f.key))

    out: list[Finding] = []
    index: dict[tuple[str, str], Finding] = {}
    for i, f in enumerate(raw, start=1):
        fixed = replace(f, id=f"F{i:02d}")
        out.append(fixed)
        index.setdefault((fixed.kind, fixed.key), fixed)
    return tuple(out), index


def analyse_run(inputs: RunInputs) -> RunAnalysis:
    """The whole deterministic layer, in one call. No LLM, no network, no clock, no writes."""
    warnings: list[str] = list(_schema_warnings(inputs))
    warnings.extend(_weight_disagreements(inputs))

    sigs = scenario_signatures(inputs)
    bleed, bleed_cov = detect_bleed(inputs, sigs)
    gate = control_gate(inputs)
    clusters = cluster_failures(inputs)
    spreads = dimension_spread(inputs)
    unscored = unscoreable(inputs)
    rejections = rejection_concentration(inputs)
    coverage = coverage_rollup(inputs)
    rows = persona_summaries(inputs)

    n = len(inputs.non_control)
    if not n:
        warnings.append("no non-control persona in this run carries a scorecard — every "
                        "recurrence fraction below has a denominator of zero and no "
                        "cross-persona claim can be made")
    if coverage.missing_scorecards:
        warnings.append(
            f"{len(coverage.missing_scorecards)} conversation(s) have no scorecard "
            f"({', '.join(coverage.missing_scorecards)}) — excluded from every score, "
            f"dimension and numeric statistic, but still scanned for scenario bleed")
    if gate.status != "pass":
        warnings.append(
            f"control gate is {gate.status.upper()} — per SYNTH_SPEC §2.8 no cross-persona "
            f"pattern in this analysis may be promoted to a defect; per-persona data is "
            f"diagnostic only")
    if set(inputs.report_ids) != {p.persona_id for p in inputs.personas}:
        warnings.append(
            f"--personas narrowed the REPORT to {', '.join(inputs.report_ids)}; signatures, "
            f"uniqueness, bleed, clusters, spreads and the control gate are still computed "
            f"over all {len(inputs.personas)} conversations, because a statistic computed over "
            f"a subset would be a different statistic")
    if coverage.optimistic_personas:
        warnings.append(
            f"scored_weight_pct < 100 on {', '.join(coverage.optimistic_personas)} — those "
            f"weighted_scores are renormalised over the dimensions that WERE scored, and "
            f"unscored dimensions skew toward failures, so those numbers are optimistic")
    if not coverage.full_everywhere:
        warnings.append(
            f"deterministic coverage below full on {', '.join(coverage.unverified_personas)} "
            f"— the numeric surface of those conversations was NOT verified end to end, and "
            f"'clean' must not be printed for them")
    if bleed_cov.conversations_without_scorecard:
        warnings.append(
            f"numeric bleed was not scanned in "
            f"{', '.join(bleed_cov.conversations_without_scorecard)} (no scorecard, therefore "
            f"no deterministic.observations) — a percentage, price or date bled into those "
            f"conversations would not be seen")
    if bleed_cov.scripts_note:
        warnings.append(bleed_cov.scripts_note)
    for pid, cnt in sorted(bleed_cov.unrecognised_mentions.items()):
        if cnt:
            warnings.append(
                f"{pid}: {cnt} numeric mention(s) in agent turns could not be parsed into a "
                f"comparable value, so they were not testable for bleed")

    findings, index = _build_findings(inputs, gate, bleed, clusters, spreads, unscored,
                                      rejections, coverage)
    agent_fixes, eval_fixes = priority_fixes(inputs, clusters, spreads, unscored, rejections,
                                             coverage, bleed, index)

    return RunAnalysis(
        run_id=inputs.run_id, personas=rows, control_gate=gate, bleed=bleed,
        bleed_coverage=bleed_cov, clusters=clusters, spreads=spreads, unscoreable=unscored,
        rejections=rejections, coverage=coverage, agent_fixes=agent_fixes,
        eval_fixes=eval_fixes, findings_index=findings, warnings=tuple(warnings),
        signatures=sigs, report_ids=inputs.report_ids,
    )


__all__ = [
    "SynthError", "Source", "Finding", "PersonaDoc", "RunInputs", "load_run",
    "Signature", "scenario_signatures", "BleedFinding", "BleedCoverage", "detect_bleed",
    "DimensionCluster", "BreachCluster", "AbsenceCluster", "FailureClusters",
    "cluster_failures", "DimensionSpread", "dimension_spread", "UnscoreableDim",
    "unscoreable", "RejectionReport", "rejection_concentration", "CoverageRollup",
    "coverage_rollup", "ControlGate", "control_gate", "PersonaSummary", "persona_summaries",
    "FixItem", "priority_fixes", "RunAnalysis", "analyse_run", "SYNTHESIS_SCHEMA_VERSION",
]


# ═════════════════════════════════════════════════════════════════════════════════════════
# Selftest — no API key, no network, no writes.
#   PYTHONPATH=. uv run --python 3.12 python -m synth.patterns [run_dir]
# ═════════════════════════════════════════════════════════════════════════════════════════

def _selftest(run_dir: Path) -> int:  # pragma: no cover - developer tool
    import copy
    import sys

    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))

    def mutate(inp: RunInputs, pid: str, *, scorecard=None, conversation=None) -> RunInputs:
        """Deep-copied clone with one persona's artifacts edited. The real run dir is never
        touched — this layer never writes, and a canary that mutated the fixture would poison
        every later assertion."""
        people = []
        for p in inp.personas:
            if p.persona_id != pid:
                people.append(p)
                continue
            sc = copy.deepcopy(p.scorecard) if p.scorecard is not None else None
            cv = copy.deepcopy(p.conversation)
            if scorecard is not None and sc is not None:
                scorecard(sc)
            if conversation is not None:
                conversation(cv)
            people.append(replace(p, scorecard=sc, conversation=cv))
        return replace(inp, personas=tuple(people))

    print(f"synth.patterns selftest — {run_dir}")
    inputs = load_run(run_dir)
    print(f"\nLOADED {len(inputs.personas)} conversation(s), "
          f"{len(inputs.judged_personas)} judged, {len(inputs.non_control)} non-control "
          f"(N for every recurrence fraction)")
    for p in inputs.personas:
        print(f"  {p.persona_id:<18} control={str(p.is_control):<5} "
              f"stresses={p.stresses:<22} scorecard={'yes' if p.judged else 'MISSING'}")

    analysis = analyse_run(inputs)

    print("\nSIGNATURES (computed per run, never hard-coded)")
    for s in analysis.signatures:
        print(f"  {s.persona_id:<18} ceiling={_fmt_ceiling(s.ceiling_pct):<6} "
              f"prices={sorted(s.prices)} dates={sorted(s.dates)} "
              f"name={s.subscriber_name!r} plan={s.plan_tokens}")

    print("\nCONTROL GATE")
    print(f"  {analysis.control_gate.status.upper()}: {analysis.control_gate.summary}")
    for r in analysis.control_gate.reasons:
        print(f"    reason: {r}")

    print("\nSCENARIO BLEED")
    print(f"  findings: {len(analysis.bleed)}")
    for b in analysis.bleed:
        print(f"    {b.kind} {b.value} in {b.persona_id} turn {b.turn} "
              f"<- {b.source_persona_ids}: {b.quote[:70]!r}")
    bc = analysis.bleed_coverage
    print(f"  coverage: source={bc.numeric_source} scanned={bc.conversations_scanned} "
          f"without_scorecard={bc.conversations_without_scorecard} "
          f"unrecognised={bc.unrecognised_mentions}")
    if bc.scripts_note:
        print(f"  scripts_note: {bc.scripts_note}")

    print("\nRECURRENCE — dimension clusters")
    for c in analysis.clusters.by_dimension:
        print(f"  {c.dimension:<22} {c.weight:>4g}w {c.tier:<8} {list(c.affected)} "
              f"{list(c.scores)} mean={c.mean:g} evidence={len(c.evidence)}")
    print("RECURRENCE — ground-truth breaches")
    for c in analysis.clusters.by_breach:
        print(f"  {c.entry!r} [{c.entry_kind}] dims={list(c.dimensions)} "
              f"personas={list(c.personas)} occurrences={len(c.occurrences)}")
        for s in c.occurrences:
            print(f"      {s.persona_id} turn {s.turn}: {s.quote!r}")
    print("RECURRENCE — recurrent absences")
    for c in analysis.clusters.recurrent_absences:
        print(f"  {c.claim!r} personas={list(c.personas)} dims={list(c.dimensions)} "
              f"sources={len(c.sources)}")

    print("\nDIMENSION SPREAD (non-control)")
    for s in analysis.spreads:
        flag = "FLAT" if s.flat else "    "
        corr = " (corroborated)" if s.corroborated else ""
        print(f"  {flag} {s.dimension:<22} {s.weight:>4g}w n={s.scored_n} "
              f"{list(s.scores)} range={s.range:g}{corr}")

    print("\nUNSCOREABLE")
    for u in analysis.unscoreable:
        print(f"  {u.dimension:<22} {u.weight:>4g}w unscored_in={list(u.unscored_in)} "
              f"structural={u.structural} reasons={list(u.reasons)}")

    print("\nEVIDENCE REJECTIONS")
    r = analysis.rejections
    print(f"  total={r.total} by_persona={r.by_persona} concentrated={r.concentrated} "
          f"on={r.concentration_persona}")
    for s in r.details:
        print(f"    {s.persona_id} {s.path}: {s.quote}")

    print("\nCOVERAGE ROLLUP")
    c = analysis.coverage
    print(f"  min_checked_fraction={c.min_checked_fraction} full_everywhere={c.full_everywhere}"
          f" min_scored_weight_pct={c.min_scored_weight_pct} "
          f"missing_scorecards={list(c.missing_scorecards)}")
    for pid, (frac, verdict) in sorted(c.per_persona.items()):
        print(f"    {pid:<18} checked_fraction={frac} verdict={verdict}")
    for s in c.run_wide_blind_spots:
        print(f"    run-wide blind spot: {s}")

    print("\nFIX LIST — agent (weight x recurrence x severity)")
    for f in analysis.agent_fixes:
        print(f"  {f.priority:>7.4g}  [{f.finding_id}] {f.formula:<26} {f.title}")
    print("FIX LIST — eval (separate owner, never interleaved)")
    for f in analysis.eval_fixes:
        print(f"  {f.priority:>7.4g}  [{f.finding_id}] {f.formula:<26} {f.title}")

    print("\nFINDINGS INDEX")
    for f in analysis.findings_index:
        print(f"  {f.id} {f.kind:<22} {f.summary}")
        for s in f.sources[:3]:
            loc = f" turn {s.turn}" if s.turn is not None else ""
            print(f"      -> {s.file} -> {s.path}{loc}"
                  + (f": {s.quote[:80]!r}" if s.quote else ""))
        if len(f.sources) > 3:
            print(f"      -> (+{len(f.sources) - 3} more citations)")

    print("\nWARNINGS")
    for w in analysis.warnings:
        print(f"  ! {w}")

    # ── invariants and liveness canaries ───────────────────────────────────────────────
    print("\nINVARIANTS")
    check("every finding carries at least one citation",
          all(len(f.sources) >= 1 for f in analysis.findings_index))
    check("finding ids are unique and dense",
          [f.id for f in analysis.findings_index]
          == [f"F{i:02d}" for i in range(1, len(analysis.findings_index) + 1)])
    check("to_json() is JSON-serialisable and stable",
          json.dumps(analysis.to_json(), sort_keys=False, ensure_ascii=False)
          == json.dumps(analyse_run(load_run(run_dir)).to_json(), sort_keys=False,
                        ensure_ascii=False))

    turns_by_pid = {p.persona_id: {t.get("idx"): (t.get("text") or "")
                                   for t in (p.conversation.get("turns") or [])}
                    for p in inputs.personas}
    bad = [(f.id, s.persona_id, s.turn) for f in analysis.findings_index for s in f.sources
           if s.kind == "transcript" and s.quote
           and s.quote not in turns_by_pid.get(s.persona_id or "", {}).get(s.turn, "")]
    check("every transcript citation is a verbatim substring of the turn it names",
          not bad, str(bad[:3]))

    check("the control never appears in a cluster, a spread or a recurrence denominator",
          all(cid not in cl.affected
              for cid in analysis.control_gate.control_ids
              for cl in analysis.clusters.by_dimension))

    print("\nLIVENESS CANARIES (mutations on deep copies — the run dir is never written)")

    def _add_obs(sc: dict, value: str, turn: int = 2) -> None:
        sc["deterministic"]["observations"].append({
            "check": "discount_percentage", "turn": turn, "speaker": "agent", "value": value,
            "quote": f"canary {value}", "verdict": "ok", "confidence": "high",
            "detail": "", "recogniser": "digit_pct"})

    if inputs.by_id("angry-churner") and inputs.by_id("price-haggler"):
        m = mutate(inputs, "angry-churner", scorecard=lambda sc: _add_obs(sc, "10%"))
        f, _ = detect_bleed(m, scenario_signatures(m))
        check("T3 no-op canary: a bled 10% into angry-churner is caught, sourced to "
              "price-haggler",
              len(f) == 1 and f[0].kind == "percentage"
              and f[0].persona_id == "angry-churner"
              and f[0].source_persona_ids == ("price-haggler",)
              and f[0].value == "10%" and f[0].turn == 2,
              str([(x.kind, x.persona_id, x.value, x.source_persona_ids) for x in f]))

    if inputs.by_id("already-switched"):
        m = mutate(inputs, "already-switched", scorecard=lambda sc: _add_obs(sc, "15%"))
        f, _ = detect_bleed(m, scenario_signatures(m))
        check("T4a trap: 15% inside already-switched (its OWN ceiling) is not bleed",
              len(f) == 0, str([(x.persona_id, x.value) for x in f]))

    if inputs.by_id("price-haggler") and inputs.by_id("happy-path"):
        m = mutate(inputs, "price-haggler", scorecard=lambda sc: _add_obs(sc, "5%"))
        f, _ = detect_bleed(m, scenario_signatures(m))
        check("T4a trap: 5% inside price-haggler is bleed sourced to happy-path, even though "
              "5 < its own ceiling of 10 (_SCHEMA.md's provable-defect case)",
              len(f) == 1 and f[0].value == "5%"
              and f[0].source_persona_ids == ("happy-path",),
              str([(x.persona_id, x.value, x.source_persona_ids) for x in f]))

    check("T4b trap: the real 'NovaPlay Premium annual plan' in angry-churner turn 0 does "
          "NOT match already-switched's 'NovaPlay Premium (quarterly)'",
          not [b for b in analysis.bleed if b.kind == "plan_name"])

    if inputs.by_id("angry-churner"):
        def _append_name(cv: dict) -> None:
            for t in cv["turns"]:
                if t.get("idx") == 2 and t.get("speaker") == "agent":
                    t["text"] = (t.get("text") or "") + " Kunal ko bhi yahi offer mila tha."
        m = mutate(inputs, "angry-churner", conversation=_append_name)
        f, _ = detect_bleed(m, scenario_signatures(m))
        names = [b for b in f if b.kind == "subscriber_name"]
        check("T5 name bleed: 'Kunal' in an angry-churner agent turn is caught and sourced to "
              "price-haggler",
              len(names) == 1 and names[0].source_persona_ids == ("price-haggler",)
              and names[0].turn == 2,
              str([(x.persona_id, x.value, x.source_persona_ids, x.turn) for x in names]))
        check("T5 negative: the real run has zero name findings",
              not [b for b in analysis.bleed if b.kind == "subscriber_name"])

    if inputs.by_id("happy-path"):
        def _break_control(sc: dict) -> None:
            sc["weighted_score"] = 40.0
            sc["band"] = "will generate support tickets"
        m = mutate(inputs, "happy-path", scorecard=_break_control)
        g = control_gate(m)
        check("T1 gate: a failing control fails the gate with a stated reason",
              g.status == "fail" and len(g.reasons) >= 1, g.summary)

    if inputs.by_id("angry-churner"):
        def _pile_rejections(sc: dict) -> None:
            sc["evidence_audit"]["rejected"] = 9
            sc["evidence_audit"]["rejected_detail"] = [
                {"dimension": "hallucination", "kind": "quote", "turn": i,
                 "quote": f"canary {i}", "reason": "not verbatim"} for i in range(9)]
        m = mutate(inputs, "angry-churner", scorecard=_pile_rejections)
        rr = rejection_concentration(m)
        check("T8 rejection concentration: 9 of 10 on one card is a TOOLING signal",
              rr.concentrated and rr.concentration_persona == "angry-churner",
              f"total={rr.total} by={rr.by_persona}")

    print(f"\n{'ALL CHECKS PASSED' if ok else 'SELFTEST FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    _root = Path(__file__).resolve().parents[1]
    _target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        _root / "runs" / "20260725-185028-f99e33")
    raise SystemExit(_selftest(_target))
