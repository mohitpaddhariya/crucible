"""synth/report.py — the narrative layer and renderer of the synthesizer.

WHAT THIS FILE OWNS (docs/SYNTH_SPEC.md §3)
    The one LLM interaction, the Markdown renderer, `synthesis.json`, and the traceability
    audit. Every number, count, ranking and verdict in the report is computed upstream in
    `synth/patterns.py` with no LLM; the model here NAMES patterns in sentences over facts it
    is handed. It computes nothing, and the enforcement of that is code, not prompt:
    `audit_llm_sentences` rejects any sentence carrying a number the digest does not contain,
    any sentence citing an unknown finding id, and any quote-shaped span the evidence audit
    never saw. Rejected sentences are dropped and recorded; a deterministic fallback narrative
    covers every section, so `--no-llm`, an LLM outage, and a full rejection all still yield a
    complete, correct report.

THE TRANSCRIPT APPENDIX (§4.6 below)
    Evidence quotes say WHY a score happened; they never show WHAT WAS SAID, and a reader who
    cannot see the conversation cannot check the judge. The report therefore ends — findings
    first, always — with every turn of every conversation, verbatim and uncut, with the cited
    turns marked. At Level 1 each persona turn additionally shows what the target's ASR HEARD
    next to what was spoken, because that pair is the product (LEVEL1_SPEC §2.2). Level is read
    off the artifact (`level`, `target.mode`), never off config. The whole section is
    deterministic rendering of data already on disk: `--no-llm` produces it in full.

NO CLAIM WITHOUT A CITATION
    Every narrative sentence is rendered with its finding ids; every finding id resolves in
    the Findings Index to scorecard JSON paths and verbatim transcript quotes. A reader can
    verify every bracket, which is the point — this project has been burned twice by
    asserted-but-unevidenced claims and the report is where that failure is most expensive.

SARVAM RULES (SYNTH_SPEC §0.3)
    Reasoning cannot be disabled. Ladder 2000 → 3000 → 4096, never higher — 4096 is a hard
    tier cap, a 400, not a degradation. `content: None` + finish_reason "length" is retryable.
    `reasoning_content` is read off the result and discarded; a final assertion refuses to
    write any artifact containing the string.

    `spar report` never opens a socket to the target and costs zero ElevenLabs quota.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import MAX_MAX_TOKENS, Config
from synth.patterns import (
    RunAnalysis,
    Source,
    SynthError,
    analyse_run,
    load_run,
)

log = logging.getLogger("spar.report")


class ReportError(Exception):
    """The report could not be produced at all. LLM failure is never this — the deterministic
    fallback covers it; this is reserved for unreadable runs and unwritable artifacts."""


# ═════════════════════════════════════════════════════════════════════════════════════════
# Narrative types
# ═════════════════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LLMSentence:
    text: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class Narrative:
    """What survived the audit. `None` anywhere means the deterministic fallback renders."""
    executive_summary: tuple[LLMSentence, ...]
    fix_rationales: dict[str, str]        # finding_id -> one audited sentence
    pattern_names: dict[str, str]         # finding_id -> 2-5 word label

    def to_json(self) -> dict[str, Any]:
        return {
            "executive_summary": [
                {"text": s.text, "source_ids": list(s.source_ids)}
                for s in self.executive_summary],
            "fix_rationales": dict(sorted(self.fix_rationales.items())),
            "pattern_names": dict(sorted(self.pattern_names.items())),
        }


@dataclass(frozen=True)
class LLMAudit:
    accepted: tuple[LLMSentence, ...]
    rejected: tuple[tuple[str, str], ...]      # (text, reason)

    def to_json(self) -> dict[str, Any]:
        return {"accepted": len(self.accepted),
                "rejected": [{"text": t, "reason": r} for t, r in self.rejected]}


# ═════════════════════════════════════════════════════════════════════════════════════════
# §3.3 — the traceability audit (pure, unit-testable offline)
# ═════════════════════════════════════════════════════════════════════════════════════════

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_FID_RE = re.compile(r"\bF\d{2,}\b")
#: Trailing "[F03]" / "[F03, F07]." the model writes into its own sentence text — the
#: renderer appends the audited citations itself, so an embedded copy would print twice.
_TRAILING_CITE_RE = re.compile(r"\s*\[\s*F\d{2,}(?:\s*,\s*F\d{2,})*\s*\]\s*([.!?])?\s*$")
#: The model repeatedly packs its own schema keys into the prose ("… costly. pattern_name:
#: process_failure_under_pressure"). That field is rendered separately; the stowaway is not
#: a sentence and does not belong in the report.
_SCHEMA_KEY_TAIL_RE = re.compile(
    r"\s*\b(pattern_names?|source_ids?|finding_id)\s*[:=].*$", re.IGNORECASE | re.DOTALL)
#: A quoted span of more than this many words is quote-shaped text that never touched the
#: evidence audit — exactly how fake evidence would sneak into a report.
_QUOTE_WORDS_MAX = 5
_QUOTE_SPAN_RE = re.compile(r'["“”]([^"“”]+)["“”]')


def _num_forms(token: str) -> set[str]:
    """Canonical membership forms for one numeric token: raw, and %g of its float value,
    so `10`, `10.0` and `10.00` in the digest all license each other in a sentence."""
    forms = {token}
    try:
        forms.add(f"{float(token):g}")
    except ValueError:
        pass
    return forms


def allowed_numbers_from(digest: str) -> frozenset[str]:
    out: set[str] = set()
    for m in _NUM_RE.finditer(digest):
        out |= _num_forms(m.group(0))
    return frozenset(out)


#: Words that assert a FAILURE tier. Checked only against clusters the sentence itself cites
#: and scores the sentence itself prints — never as a general ban on the word.
_FAILURE_WORD_RE = re.compile(
    r"\b(fail|fails|failed|failing|failure|failures|breakdown|broke\s+down|broken)\b",
    re.IGNORECASE)

#: goal_outcome is scored on PROCESS. rubric.py's `_GOAL_OUTCOME_ADDENDUM` says so twice
#: ("judge PROCESS, never outcome"; "customers here are frequently unconvertible BY DESIGN")
#: and CALIBRATION §4 records punishing non-conversion as a measurement error. A narrative
#: sentence framing goal_outcome as the agent failing to reach its goal, as costing
#: conversions, or as "broken", inverts the rubric — and none of those is a verdict the
#: deterministic layer computed. Rejected in code, not discouraged in the prompt.
_GOAL_FRAMING_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(fail\w*|unable|did\s+not|does\s+not|never)\b[^.]{0,40}?"
               r"\b(achiev\w*|reach\w*|attain\w*|accomplish\w*|convert\w*|close[sd]?)\b"
               r"[^.]{0,30}?\b(goal|objective|mandate|conversion)\b", re.IGNORECASE),
    #: any outcome framing at all: the customers are unconvertible by design, so a
    #: conversion or sale is never the thing this dimension measured.
    re.compile(r"\b(convert\w*|conversions?|sales?|close[sd]?\s+the\s+(deal|sale))\b",
               re.IGNORECASE),
    re.compile(r"\bbroken\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ClusterTiers:
    """What the deterministic layer computed for one cited cluster: the scores it holds and
    which of them are failures. The narrative may describe these; it may not re-tier them."""
    dimension: str
    scores: tuple[float, ...]
    failures: tuple[float, ...]


def cluster_tiers_from(analysis: RunAnalysis) -> dict[str, ClusterTiers]:
    """finding id -> the tier facts of the cluster it names, for the tier-fidelity rule."""
    out: dict[str, ClusterTiers] = {}
    for c in analysis.clusters.by_dimension:
        fid = next((f.id for f in analysis.findings_index
                    if f.kind == "cluster" and f.key == c.dimension), "")
        if not fid:
            continue
        tiers = c.tiers or tuple("failure" if s < 0.5 else "dent" for s in c.scores)
        out[fid] = ClusterTiers(
            dimension=c.dimension, scores=tuple(c.scores),
            failures=tuple(s for s, t in zip(c.scores, tiers) if t == "failure"))
    return out


def _tier_fidelity_reason(text: str, source_ids: tuple[str, ...],
                          clusters: dict[str, ClusterTiers]) -> str | None:
    """Reject a sentence that calls a DENT a failure.

    The rule fires only on what the sentence itself supplies: a failure word, plus a number
    that is one of the cited cluster's own scores and is NOT one of its failures. That is how
    "the agent fails to achieve its goal in all three pressure conversations, with scores of
    0.7, 0.4, and 0.7" is caught — 0.7 is a goal_outcome score, and it is a dent, which
    rubric.py anchors as "ADEQUATE. The mandate held". The numbers rule cannot see this
    (0.7 IS in the digest) and neither can the id rule (F04 IS the goal_outcome cluster);
    only the tiers can, and they are computed, not opined.
    """
    cited = [clusters[fid] for fid in source_ids if fid in clusters]
    if not cited:
        return None

    # Outcome framing needs no failure word to be wrong: "never reached the goal" is the
    # same inversion of the rubric as "failed to achieve its goal".
    if any(ct.dimension == "goal_outcome" for ct in cited) and any(
            r.search(text) for r in _GOAL_FRAMING_RES):
        return ("frames goal_outcome as an OUTCOME — reaching a goal, converting, or being "
                "'broken'. The rubric scores PROCESS on customers who are unconvertible by "
                "design, and no scorecard asserts a goal failure")

    if not _FAILURE_WORD_RE.search(text):
        return None

    printed = {f"{float(t):g}" for t in _NUM_RE.findall(_FID_RE.sub("", text))}
    for ct in cited:
        scores = {f"{s:g}" for s in ct.scores}
        fails = {f"{s:g}" for s in ct.failures}
        mislabelled = sorted(printed & (scores - fails))
        if mislabelled:
            return (f"tier mislabel: {', '.join(mislabelled)} on {ct.dimension} "
                    f"{'is a dent' if len(mislabelled) == 1 else 'are dents'} "
                    f"(>= 0.5), not a failure — the sentence calls them failures")
    return None


def audit_llm_sentences(sentences: list[LLMSentence],
                        allowed_numbers: frozenset[str],
                        known_finding_ids: frozenset[str],
                        cluster_tiers: dict[str, ClusterTiers] | None = None) -> LLMAudit:
    """Every sentence keeps or loses its place on four rules, all enforced here in code:

      1. every digit-group must already exist in the digest (no arithmetic, no new numbers,
         no "roughly half" dressed as a figure);
      2. every source id must be a known finding id, and there must be at least one;
      3. no quoted span longer than 5 words — the model is given no quotes and may produce
         none, because a "quote" that never touched the verbatim audit is manufactured
         evidence wearing punctuation;
      4. tier fidelity — a sentence may not call a computed dent a failure, and may not
         frame goal_outcome as a failure to convert. Rules 1-3 check numbers, ids and quote
         shape; they passed two sentences in the shipped report that the scorecards
         contradict, both overclaiming failure, which is the exact direction this project
         has been burned by. Rule 4 is the semantic hole they left, closed with computed
         tiers rather than with prompt text.
    """
    tiers = cluster_tiers or {}
    accepted: list[LLMSentence] = []
    rejected: list[tuple[str, str]] = []
    for s in sentences:
        text = (s.text or "").strip()
        if not text:
            rejected.append((s.text, "empty sentence"))
            continue
        if not s.source_ids:
            rejected.append((text, "no source_ids — a claim without a citation"))
            continue
        unknown = [fid for fid in s.source_ids if fid not in known_finding_ids]
        if unknown:
            rejected.append((text, f"unknown finding id(s): {', '.join(unknown)}"))
            continue
        span = next((m.group(1) for m in _QUOTE_SPAN_RE.finditer(text)
                     if len(m.group(1).split()) > _QUOTE_WORDS_MAX), None)
        if span is not None:
            rejected.append((text, f"quote-shaped span of >{_QUOTE_WORDS_MAX} words "
                                   f"({span[:40]!r}…) — narrative may not manufacture quotes"))
            continue
        bare = _FID_RE.sub("", text)
        bad = next((tok for tok in (m.group(0) for m in _NUM_RE.finditer(bare))
                    if not (_num_forms(tok) & allowed_numbers)), None)
        if bad is not None:
            rejected.append((text, f"number {bad!r} does not appear in the facts digest — "
                                   f"the narrative computes nothing"))
            continue
        reason = _tier_fidelity_reason(text, tuple(s.source_ids), tiers)
        if reason is not None:
            rejected.append((text, reason))
            continue
        accepted.append(LLMSentence(text=text, source_ids=tuple(s.source_ids)))
    return LLMAudit(accepted=tuple(accepted), rejected=tuple(rejected))


# ═════════════════════════════════════════════════════════════════════════════════════════
# §3.2 — the facts digest and the ONE LLM call
# ═════════════════════════════════════════════════════════════════════════════════════════

def build_digest(analysis: RunAnalysis) -> str:
    """Everything the model may talk about, and nothing else. Built ONLY from RunAnalysis:
    no transcripts, no scorecard JSON, no persona prompts."""
    lines: list[str] = [f"RUN {analysis.run_id}"]
    g = analysis.control_gate
    lines.append(f"CONTROL GATE: {g.status} — {g.summary}")

    lines.append("PERSONAS (weighted_score / band / % of rubric weight scored):")
    for p in analysis.personas:
        tag = " (CONTROL — excluded from every aggregate)" if p.is_control else ""
        if p.judged:
            lines.append(
                f"- {p.persona_id}{tag}: {p.weighted_score:g} / {p.band} / "
                f"{p.scored_weight_pct:g}% scored / stress {p.stresses}")
        else:
            lines.append(f"- {p.persona_id}{tag}: NEVER JUDGED / stress {p.stresses}")

    lines.append("FINDINGS (cite these ids; every sentence needs at least one):")
    for f in analysis.findings_index:
        lines.append(f"- {f.id} [{f.kind}] {f.summary}")

    lines.append("AGENT FIX RANKING (priority = weight x recurrence x severity, computed):")
    for i, fx in enumerate(analysis.agent_fixes, 1):
        lines.append(f"- {i}. [{fx.finding_id}] priority {fx.priority:g} ({fx.formula}) "
                     f"— {fx.title}")
    lines.append("EVAL FIX RANKING (tool defects — different owner, never interleaved):")
    for i, fx in enumerate(analysis.eval_fixes, 1):
        lines.append(f"- {i}. [{fx.finding_id}] priority {fx.priority:g} — {fx.title}")

    for w in analysis.warnings:
        lines.append(f"WARNING: {w}")
    return "\n".join(lines)


_SYSTEM_PROMPT = """You are the narrative layer of an evaluation synthesizer. You are handed a
facts digest computed deterministically from judged conversation scorecards. Your ONLY job is
to name the patterns in plain prose for two readers: the owner of the voice agent under test,
and the maintainer of this eval.

HARD RULES — sentences breaking any of these are deleted by code after you answer:
- Every sentence must cite at least one finding id from the digest in source_ids.
- Use ONLY numbers that literally appear in the digest. No arithmetic, no new figures, no
  approximations ("roughly half" is fine ONLY as words; never invent a digit).
- Do not quote anyone. You have no transcript access; quotation marks around more than five
  words get the sentence deleted.
- Do not declare the run passing or failing — the control gate already did, and your verdict
  would be discarded.
- Use the digest's own tier vocabulary exactly: a "failure" is a score below 0.5, a "dent"
  is 0.5-0.8. Never call a dent a failure, and never describe a cluster as a failure "in all"
  or "in N of M" conversations when the digest says it is a mix — say what the mix is.
  goal_outcome measures the QUALITY OF THE AGENT'S PROCESS — these customers are frequently
  unconvertible by design — so never phrase it as the agent failing to convert or failing to
  achieve a goal. Both of these are checked against computed tiers after you answer, and a
  sentence that prints a dent's score next to a failure word is deleted.
- 3 to 6 executive summary sentences. Short, concrete, no filler, no restating the table.
- One fix_rationale sentence per AGENT fix. Do NOT restate the priority formula (it is
  already printed); say what the recurring behaviour is and why leaving it unfixed is
  costly, strictly in terms of facts in the digest.
- pattern_names are optional 2-5 word labels naming the BEHAVIOUR a cluster shows (e.g. a
  label like "discount-first apology"). Never a label that restates the finding type such
  as "recurrence" or "recurring absence" — omit the name instead."""


def _response_format() -> dict:
    sentence = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "source_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text", "source_ids"],
        "additionalProperties": False,
    }
    keyed = lambda field: {  # noqa: E731 - local schema shorthand
        "type": "object",
        "properties": {"finding_id": {"type": "string"}, field: {"type": "string"}},
        "required": ["finding_id", field],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "synth_narrative", "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "executive_summary": {"type": "array", "items": sentence},
                    "fix_rationales": {"type": "array", "items": keyed("text")},
                    "pattern_names": {"type": "array", "items": keyed("name")},
                },
                "required": ["executive_summary", "fix_rationales", "pattern_names"],
                "additionalProperties": False,
            },
        },
    }


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"no JSON object in narrative response: {text[:200]!r}")


async def _call_llm(cfg: Config, digest: str) -> tuple[dict[str, Any] | None,
                                                        dict[str, Any] | None,
                                                        list[str]]:
    """(parsed narrative | None, llm meta | None, errors). Never raises for LLM trouble —
    the fallback narrative is not an error path, it is a designed output."""
    # Imported here so `--no-llm` (and every patterns-only consumer) never pays for httpx.
    from agent.sarvam import LLMError, SarvamClient

    errors: list[str] = []
    scfg = cfg.synthesizer
    client = SarvamClient(cfg.secrets.sarvam_api_key, scfg, label="synthesizer")
    # SYNTH_SPEC §3.2: 2000 -> 3000 -> 4096, never higher; 4096+1 is an HTTP 400.
    ladder = (max(scfg.max_tokens, 2000), 3000, MAX_MAX_TOKENS)
    messages = [{"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": digest}]
    calls = 0
    total_tokens = 0
    try:
        for attempt, cap in enumerate(ladder, start=1):
            cap = min(cap, MAX_MAX_TOKENS)
            try:
                res = await client.complete(messages, response_format=_response_format(),
                                            max_tokens=cap)
            except LLMError as exc:
                retry = exc.transport in {"timeout", "transport"} or (
                    exc.status_code in {429, 500, 502, 503, 504})
                errors.append(f"attempt {attempt}: {exc} (retryable={retry})")
                if not retry or attempt == len(ladder):
                    return None, None, errors
                await asyncio.sleep(2 ** (attempt - 1))
                continue
            calls += 1
            total_tokens += res.usage.total_tokens
            # `reasoning_content` is read off the result here and dropped on the floor —
            # it enters no artifact and no log line above DEBUG (SYNTH_SPEC §0.3).
            if not res.text:
                errors.append(f"attempt {attempt}: content=None finish={res.finish_reason} "
                              f"at max_tokens={cap} — retryable")
                continue
            try:
                parsed = _extract_json(res.text)
            except ValueError as exc:
                errors.append(f"attempt {attempt}: {exc}")
                continue
            meta = {"model": scfg.model, "calls": calls,
                    "usage": {"total_tokens": total_tokens}}
            return parsed, meta, errors
    finally:
        await client.aclose()
    return None, None, errors


def narrate(parsed: dict[str, Any], digest: str,
            analysis: RunAnalysis) -> tuple[Narrative, LLMAudit]:
    """Audit the model's output down to what is traceable. Everything rejected is recorded."""
    allowed = allowed_numbers_from(digest)
    known = frozenset(f.id for f in analysis.findings_index)
    tiers = cluster_tiers_from(analysis)

    summary_in = [LLMSentence(str(s.get("text") or ""),
                              tuple(str(x) for x in (s.get("source_ids") or [])))
                  for s in (parsed.get("executive_summary") or [])]
    rat_in = [LLMSentence(str(r.get("text") or ""), (str(r.get("finding_id") or ""),))
              for r in (parsed.get("fix_rationales") or [])]

    # Audited SEPARATELY, and what renders is the audit's OWN accepted objects — never the
    # un-audited originals re-selected by matching their text against an accepted-text set.
    # That set was a hole: two sentences with identical text but different source_ids
    # (a routine LLM repetition at temperature 0.2) shared one membership test, so a sentence
    # rejected for citing an unknown id rendered anyway, carrying its unresolvable bracket
    # into the report. Auditing the two pools together also let a rationale be licensed by an
    # accepted SUMMARY sentence of the same text.
    audit_summary = audit_llm_sentences(summary_in, allowed, known, tiers)
    audit_rationales = audit_llm_sentences(rat_in, allowed, known, tiers)

    def _clean(text: str) -> str:
        text = _SCHEMA_KEY_TAIL_RE.sub("", text.strip())
        return _TRAILING_CITE_RE.sub(lambda m: m.group(1) or "", text.strip()).strip()

    summary = tuple(LLMSentence(_clean(s.text), s.source_ids)
                    for s in audit_summary.accepted)[:6]
    rationales = {s.source_ids[0]: _clean(s.text) for s in audit_rationales.accepted
                  if s.source_ids and s.source_ids[0]}

    names: dict[str, str] = {}
    rejected_names: list[tuple[str, str]] = []
    #: A pattern name that restates the finding TYPE names nothing — the kind is already
    #: printed next to it. Vacuous labels are rejected like any other untraceable sentence.
    _vacuous = {"recurrence", "recurring absence", "recurring failure", "cluster", "bleed",
                "breach", "pattern", "failure", "dent", "absence"}
    for n in (parsed.get("pattern_names") or []):
        fid, name = str(n.get("finding_id") or ""), str(n.get("name") or "").strip()
        if fid not in known:
            rejected_names.append((name, f"unknown finding id {fid!r}"))
        elif not name or len(name.split()) > 5:
            rejected_names.append((name, "pattern name must be 2-5 words"))
        elif name.casefold() in _vacuous:
            rejected_names.append((name, "pattern name restates the finding type — names "
                                         "must describe the behaviour"))
        elif _NUM_RE.search(name) and not all(
                _num_forms(t) & allowed for t in _NUM_RE.findall(name)):
            rejected_names.append((name, "pattern name carries a number not in the digest"))
        else:
            names[fid] = name

    audit = LLMAudit(accepted=audit_summary.accepted + audit_rationales.accepted,
                     rejected=audit_summary.rejected + audit_rationales.rejected
                     + tuple(rejected_names))
    return Narrative(executive_summary=summary, fix_rationales=rationales,
                     pattern_names=names), audit


# ═════════════════════════════════════════════════════════════════════════════════════════
# §4 — the Markdown renderer (pure, no I/O, no clock beyond the passed-in timestamp)
# ═════════════════════════════════════════════════════════════════════════════════════════

_ELLIPSIS_PREFIX = 200      # display truncation keeps a verbatim prefix well above the 60 floor


def _q(quote: str | None) -> str:
    """Display a quote byte-for-byte; if it must be shortened, only with a trailing `…` after
    a verbatim prefix (CALIBRATION §2: store full, display prefixed)."""
    if not quote:
        return ""
    if len(quote) <= _ELLIPSIS_PREFIX + 20:
        return quote
    return quote[:_ELLIPSIS_PREFIX].rstrip() + "…"


def _cite(s: Source) -> str:
    loc = f" turn {s.turn}" if s.turn is not None else ""
    body = f"`{s.file} → {s.path}`{loc}"
    if s.quote:
        body += f": “{_q(s.quote)}”"
    return body


def _num(x: float | None, spec: str = "g", missing: str = "not reported") -> str:
    """Format a number that the contract allows to be absent.

    `judge/checks.py::_coverage` returns `checked_fraction: None` whenever a conversation's
    agent turns contain no percentage, amount or date to compare — a legitimate, in-contract
    value (a call that dies after the greeting, a persona that stonewalls before the offer).
    `patterns.py` types the field `float | None` and handles it; formatting it with `:g`
    raised TypeError and killed the whole report, for every conversation, in exactly the
    harness-failure case where the report is most needed.
    """
    if x is None:
        return missing
    return format(x, spec)


def _score_cell(p) -> str:
    if not p.judged:
        return "— (never judged)"
    if p.weighted_score is None:
        return "— (judged, but the scorecard reports no weighted_score)"
    cell = f"{p.weighted_score:.1f}"
    if p.optimistic:
        cell += f" ({_num(p.scored_weight_pct)}% of rubric weight scored — optimistic)"
    return cell


def _tier_breakdown(cluster) -> str:
    """`failure in 3 of 3` reads as three failures; say what the tiers actually were.

    Counted from the cluster's own per-persona tiers, which is what `_tier()` assigned —
    not re-derived from scores here, because a 0.7 carrying verdict "fail" is a failure and
    a score-only recount would silently disagree with the clustering.
    """
    fails, dents = cluster.tier_counts
    parts = []
    if fails:
        parts.append(f"{fails} failure(s) below 0.5")
    if dents:
        parts.append(f"{dents} dent(s) in 0.5-0.8")
    return " + ".join(parts)


def _det_cell(p) -> str:
    if not p.judged:
        return "—"
    if p.det_checked_fraction is None:
        # A null fraction is "nothing was comparable", which is not a low coverage number and
        # must not be printed as one — but it is emphatically not a verified surface either.
        return (f"{p.det_verdict or '—'} (nothing to compare — no percentage, amount or date "
                f"in any agent turn; the numeric surface was NOT verified)")
    cell = f"{p.det_verdict or '—'} ({p.det_checked_fraction:g})"
    if not p.numeric_surface_verified:
        cell += (f" — numeric surface only PARTIALLY verified "
                 f"(fraction {p.det_checked_fraction:g})")
    return cell


def _bleed_fid(analysis: RunAnalysis, b) -> str:
    """The finding id of THIS bleed occurrence.

    `_build_findings` emits one Finding per (persona, turn) occurrence but keys them all
    `"{kind}:{value}"`, and `findings_by_key` keeps only the first. Matching on the key alone
    therefore stamped every bullet with the first occurrence's id: a claim about one
    conversation citing a finding about another, and the remaining findings orphaned — in the
    one section whose entire premise is "no claim without a citation". Persona and turn
    disambiguate, and they come off the finding's own transcript source.
    """
    key = f"{b.kind}:{b.value}"
    for f in analysis.findings_index:
        if f.kind == "bleed" and f.key == key and f.persona_id == b.persona_id:
            if any(s.turn == b.turn for s in f.sources):
                return f.id
    return ""


def _bleed_counts(analysis: RunAnalysis) -> tuple[int, int, int]:
    """(occurrences, distinct values, conversations). The Verdict used to print only the
    distinct-value count, so one value repeated at three turns across two conversations
    reported as "1" — an undercount of provable defects in the report's most-read line."""
    return (len(analysis.bleed),
            len({(b.kind, b.value) for b in analysis.bleed}),
            len({b.persona_id for b in analysis.bleed}))


def _bleed_note(analysis: RunAnalysis) -> str:
    occ, vals, convs = _bleed_counts(analysis)
    if not occ:
        return "none detected (§2.3 states exactly what that does and does not cover)"
    return (f"{vals} value(s) in {occ} occurrence(s) across {convs} conversation(s)")


def _fallback_summary(analysis: RunAnalysis) -> list[str]:
    """Deterministic template narrative over the same facts. Complete on its own — `--no-llm`
    is a supported mode, not a degraded one."""
    a = analysis
    out = [f"Control gate: {a.control_gate.status.upper()}."]
    n_breach = len(a.clusters.by_breach)
    n_bleed, n_bleed_values, _ = _bleed_counts(a)
    n_det = sum(1 for f in a.findings_index if f.kind == "det_violation")
    ids = [f.id for f in a.findings_index if f.kind in ("breach", "bleed", "det_violation")]
    out.append(f"Confirmed agent defects: {n_breach + n_bleed + n_det} "
               f"({n_breach} ground-truth breach(es), {n_det} deterministic violation(s), "
               f"{n_bleed} scenario-bleed occurrence(s) of {n_bleed_values} value(s))"
               + (f" [{', '.join(ids)}]." if ids else "."))
    if a.agent_fixes:
        top = a.agent_fixes[0]
        out.append(f"Top agent fix: {top.title} — priority {top.priority:g} "
                   f"({top.formula}) [{top.finding_id}].")
    if a.eval_fixes:
        out.append(f"Eval health: {len(a.eval_fixes)} flag(s); top: "
                   f"{a.eval_fixes[0].title} [{a.eval_fixes[0].finding_id}].")
    return out


# ═════════════════════════════════════════════════════════════════════════════════════════
# §4.6 — the transcript appendix (deterministic; no LLM, no network, no scorecard needed)
# ═════════════════════════════════════════════════════════════════════════════════════════
#
# The report above explains WHY a score happened and quotes only the spans the judge's
# evidence audit kept. That is not enough to check the judge's work: a reader who cannot see
# the conversation cannot tell a cherry-picked quote from a representative one. This appendix
# reproduces every turn of every conversation, uncut, at the BACK of the document — findings
# stay first — and marks the turns that were cited above so a score and its moment in the
# dialogue are one hop apart.
#
# At Level 1 the interesting object is not the line, it is the PAIR (LEVEL1_SPEC §2.2): what
# the persona said, and what the agent's ASR heard. Tara's `user_transcript` is a first-class
# product finding — measurably lossy in three ways (code-switch mangling, phantom numbers,
# silent truncation) — and the intended-vs-heard diff in the artifact is the ONLY place that
# loss is ever visible. So when the two differ they are rendered adjacent, labelled, in full.
#
# Level is read off the ARTIFACT (`level`, `target.mode`), never off config: a config says
# what a run was asked to do, an artifact says what it did, and a report that renders empty
# "heard" rows for a text run because the config said audio is lying about a measurement.

_ASR_NOTE = (
    "Tara heard = the target's own `user_transcript` ASR of our audio, recorded verbatim in "
    "`meta.tara_heard` with provenance `asr`. It is never the persona's words for any "
    "purpose: no deterministic check parses it and no dimension is scored on it "
    "(LEVEL1_SPEC §2.2). It is here because it is a finding about the target."
)


@dataclass(frozen=True)
class TurnView:
    """One turn, exactly as the artifact records it. Nothing here is derived except the
    presence flags; `text` and `heard_text` are byte-for-byte from the conversation JSON."""
    idx: int
    speaker: str
    text: str
    provenance: str = ""                    # meta.text_provenance, "" when the key is absent
    sent: bool | None = None                # meta.sent — False = generated, never delivered
    speech_s: float | None = None           # agent turns (audio)
    peak: float | None = None               # agent turns (audio)
    heard_text: str | None = None           # meta.tara_heard.text — None = no key at all
    heard_provenance: str = ""
    heard_event_id: int | None = None
    truncation_suspect: bool | None = None  # artifact's own heuristic flag

    @property
    def has_heard(self) -> bool:
        return self.heard_text is not None

    @property
    def heard_differs(self) -> bool:
        """Whitespace-normalised inequality. Anything beyond that (case, transliteration,
        numerals) is a difference the reader must see for themselves — folding it here would
        hide exactly the mangling this section exists to show."""
        if self.heard_text is None:
            return False
        return " ".join(self.heard_text.split()) != " ".join(self.text.split())

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"idx": self.idx, "speaker": self.speaker, "text": self.text}
        if self.provenance:
            out["text_provenance"] = self.provenance
        if self.sent is not None:
            out["sent"] = self.sent
        if self.speech_s is not None:
            out["speech_s"] = self.speech_s
        if self.peak is not None:
            out["peak"] = self.peak
        if self.heard_text is not None:
            out["tara_heard"] = {"text": self.heard_text,
                                 "provenance": self.heard_provenance or None,
                                 "event_id": self.heard_event_id,
                                 "truncation_suspect": self.truncation_suspect,
                                 "differs_from_spoken": self.heard_differs}
        return out


@dataclass(frozen=True)
class PersonaTranscript:
    persona_id: str
    is_control: bool
    audio: bool
    level: int | None
    mode: str
    end_reason: str
    turns: tuple[TurnView, ...]

    @property
    def counts(self) -> tuple[int, int, int]:
        """(agent turns, persona turns, total)."""
        a = sum(1 for t in self.turns if t.speaker == "agent")
        p = sum(1 for t in self.turns if t.speaker == "persona")
        return a, p, len(self.turns)

    @property
    def heard_stats(self) -> tuple[int, int, int, int]:
        """(persona turns, turns with a heard transcript, of those how many differ,
        how many the artifact flagged truncation_suspect)."""
        persona = [t for t in self.turns if t.speaker == "persona"]
        heard = [t for t in persona if t.has_heard]
        return (len(persona), len(heard),
                sum(1 for t in heard if t.heard_differs),
                sum(1 for t in heard if t.truncation_suspect))

    def to_json(self) -> dict[str, Any]:
        return {"persona_id": self.persona_id, "is_control": self.is_control,
                "level": self.level, "mode": self.mode, "audio": self.audio,
                "end_reason": self.end_reason,
                "turns": [t.to_json() for t in self.turns]}


def _as_float(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _as_int(x: Any) -> int | None:
    return int(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _end_reason_text(value: Any) -> str:
    """`end_reason` is a string in some artifacts and the full end object in others. Print the
    code either way rather than a dict repr, and never invent one when it is absent."""
    if isinstance(value, dict):
        code = str(value.get("code") or "")
        kind = str(value.get("kind") or "")
        return f"{code}" + (f" ({kind})" if code and kind else "")
    return str(value or "—")


def is_audio_conversation(conversation: dict[str, Any]) -> bool:
    """Audio iff the ARTIFACT says so — `target.mode == "audio"` or `level >= 1`. A Level 0
    artifact has neither, carries none of the §3.2 meta keys, and must render as a plain
    transcript: no empty 'heard' rows, no apology for data that was never meant to exist."""
    mode = str((conversation.get("target") or {}).get("mode") or "")
    if mode == "audio":
        return True
    level = _as_int(conversation.get("level"))
    return level is not None and level >= 1


def build_transcripts(personas: Any) -> tuple[PersonaTranscript, ...]:
    """PersonaDoc-likes (`.persona_id`, `.is_control`, `.conversation`) -> renderable turns.

    Pure, total, and independent of the judge: a run with no scorecards still has a full
    transcript, which is the case where reading the conversation matters most.
    """
    out: list[PersonaTranscript] = []
    for p in personas:
        conv = getattr(p, "conversation", None) or {}
        views: list[TurnView] = []
        for i, turn in enumerate(conv.get("turns") or []):
            if not isinstance(turn, dict):
                continue
            meta = turn.get("meta") if isinstance(turn.get("meta"), dict) else {}
            heard = meta.get("tara_heard") if isinstance(meta.get("tara_heard"), dict) else None
            idx = _as_int(turn.get("idx"))
            views.append(TurnView(
                idx=idx if idx is not None else i,
                speaker=str(turn.get("speaker") or "?"),
                text=str(turn.get("text") or ""),
                provenance=str(meta.get("text_provenance") or ""),
                sent=meta.get("sent") if isinstance(meta.get("sent"), bool) else None,
                speech_s=_as_float(meta.get("speech_s")),
                peak=_as_float(meta.get("peak")),
                heard_text=None if heard is None else str(heard.get("text") or ""),
                heard_provenance="" if heard is None else str(heard.get("provenance") or ""),
                heard_event_id=None if heard is None else _as_int(heard.get("event_id")),
                truncation_suspect=(None if heard is None
                                    else bool(heard.get("truncation_suspect"))),
            ))
        out.append(PersonaTranscript(
            persona_id=str(getattr(p, "persona_id", "") or conv.get("persona_id") or "?"),
            is_control=bool(getattr(p, "is_control", False)),
            audio=is_audio_conversation(conv),
            level=_as_int(conv.get("level")),
            mode=str((conv.get("target") or {}).get("mode") or ""),
            end_reason=_end_reason_text(conv.get("end_reason")),
            turns=tuple(views),
        ))
    return tuple(out)


#: How a finding is named in a turn's citation marker. The kind alone ("cluster") names
#: nothing to a reader scanning the dialogue; the dimension or the defect does.
def _finding_label(f) -> str:
    if f.kind == "cluster":
        key = f.key or ""
        if key.startswith("absence:"):
            claim = key[len("absence:"):].strip()
            # A LABEL may be shortened (it is a pointer, not evidence); the dialogue below it
            # never is. The full claim is in §3 and §6.
            return "absence: " + (claim if len(claim) <= 60 else claim[:60].rstrip() + "…")
        return key or "recurring pattern"
    if f.kind == "breach":
        return "ground-truth breach"
    if f.kind == "det_violation":
        return "deterministic violation"
    if f.kind == "bleed":
        return "scenario bleed"
    return f.kind.replace("_", " ")


def cited_turns(analysis: RunAnalysis) -> dict[tuple[str, int], tuple[str, ...]]:
    """(persona_id, turn) -> the markers to print, built from the SAME Finding sources the
    body of the report cites. Nothing is asserted here that §6 cannot resolve."""
    hits: dict[tuple[str, int], dict[str, str]] = {}
    for f in analysis.findings_index:
        label = _finding_label(f)
        for s in f.sources:
            if s.kind != "transcript" or s.turn is None or not s.persona_id:
                continue
            hits.setdefault((s.persona_id, int(s.turn)), {})[f.id] = label
    return {k: tuple(f"{lbl} [{fid}]" for fid, lbl in sorted(v.items()))
            for k, v in hits.items()}


def _blockquote(text: str, label: str = "") -> list[str]:
    """A verbatim blockquote. Multi-line agent turns keep their line breaks (a `>` per line,
    `>` alone for blank ones) — the alternative, flattening to one line or to `<br>`, edits
    the transcript, and the whole point of this section is that it is not edited."""
    head = f"**{label}** " if label else ""
    lines = text.split("\n") if text else []
    if not lines:
        return [f"> {head}*(no text recorded in the artifact)*"]
    out = [f"> {head}{lines[0]}".rstrip()]
    for ln in lines[1:]:
        out.append(f"> {ln}".rstrip() if ln.strip() else ">")
    return out


def _turn_facts(t: TurnView, audio: bool) -> str:
    """The measured per-turn facts, and only the ones the artifact actually carries."""
    bits: list[str] = []
    if t.provenance:
        bits.append(f"provenance `{t.provenance}`")
    if audio and t.speech_s is not None:
        bits.append(f"speech {t.speech_s:g}s")
    if audio and t.peak is not None:
        bits.append(f"peak {t.peak:g}")
    if t.sent is False:
        bits.append("**never delivered** (`meta.sent: false`) — generated, then the "
                    "conversation ended before it was spoken")
    return " · ".join(bits)


def _render_turn(L: list[str], t: TurnView, audio: bool, marks: tuple[str, ...]) -> None:
    head = f"**turn {t.idx} · {t.speaker}**"
    facts = _turn_facts(t, audio)
    if facts:
        head += f" · {facts}"
    if marks:
        head += f"  ←  cited: {', '.join(marks)}"
    L.append(head)
    L.append("")
    if audio and t.has_heard and t.heard_differs:
        # The pair, adjacent, both in full. This is the product.
        L += _blockquote(t.text, "we said:")
        L.append(">")
        if t.heard_text:
            L += _blockquote(t.heard_text, "Tara heard:")
        else:
            L.append("> **Tara heard:** *(empty — `meta.tara_heard.text` is a blank string: "
                     "the target's ASR returned nothing for this turn)*")
        # Only when the lengths actually differ: "heard 111 chars vs 111 spoken" on a line the
        # ASR rewrote without shortening reads like a contradiction of the diff above it.
        note: list[str] = []
        if len(t.heard_text or "") != len(t.text):
            note.append(f"heard {len(t.heard_text or '')} chars vs {len(t.text)} spoken")
        if t.truncation_suspect:
            note.append("`truncation_suspect: true` (the artifact's own heuristic — heard "
                        "shorter than 60% of intended, LEVEL1_SPEC §3.2)")
        if t.heard_event_id is not None:
            note.append(f"event_id {t.heard_event_id}")
        L.append(">")
        L.append("> *ASR differs from the spoken line"
                 + (f" — {'; '.join(note)}" if note else "") + ".*")
    elif audio and t.has_heard:
        L += _blockquote(t.text)
        L.append(">")
        L.append("> *Tara's ASR returned this line unchanged (whitespace aside).*")
    elif audio and t.speaker == "persona" and t.sent is not False:
        L += _blockquote(t.text)
        L.append(">")
        L.append("> *No `user_transcript` was recorded for this turn — what the target heard "
                 "is unknown, not identical.*")
    else:
        L += _blockquote(t.text)
    L.append("")


def render_transcripts(transcripts: tuple[PersonaTranscript, ...],
                       cited: dict[tuple[str, int], tuple[str, ...]],
                       order: tuple[str, ...], *, section: str = "8") -> list[str]:
    """The appendix, in the scorecard table's own persona order."""
    L: list[str] = []
    L.append(f"## {section}. Full transcripts")
    L.append("")
    if not transcripts:
        L.append("No conversation artifacts were loaded for this report.")
        L.append("")
        return L

    by_id = {t.persona_id: t for t in transcripts}
    rows = [by_id[pid] for pid in order if pid in by_id]
    rows += [t for t in transcripts if t.persona_id not in set(order)]

    L.append("Every turn of every conversation above, verbatim and uncut. The quotes in §2-§4 "
             "are the spans the judge's evidence audit kept; this is the conversation they "
             "were taken from, so a reader can check the judge's work instead of trusting it. "
             "Turns cited anywhere above carry a `←  cited: … [Fxx]` marker; every id "
             f"resolves in §{int(section) - 2 if section.isdigit() else '6'}.")
    L.append("")
    if any(t.audio for t in rows):
        L.append(f"Audio conversations show two streams per persona turn: **we said** — the "
                 f"persona's intended line, which is what was synthesised and what the judge "
                 f"scored (`text_provenance: persona_intended`) — and **Tara heard**. "
                 f"{_ASR_NOTE}")
        L.append("")
    for i, tr in enumerate(rows, 1):
        tag = " *(control — excluded from aggregates)*" if tr.is_control else ""
        lvl = f"level {tr.level}" if tr.level is not None else "level not recorded"
        mode = tr.mode or ("audio" if tr.audio else "not recorded")
        n_agent, n_persona, n_total = tr.counts
        L.append(f"### {section}.{i} {tr.persona_id}{tag} — {mode}, {lvl}")
        L.append("")
        L.append(f"{n_total} turns ({n_agent} agent / {n_persona} persona) · "
                 f"ended `{tr.end_reason}`")
        if tr.audio:
            n_p, n_heard, n_diff, n_trunc = tr.heard_stats
            L.append("")
            L.append(f"Target ASR: `user_transcript` recorded on {n_heard} of {n_p} persona "
                     f"turns; it differs from the spoken line on {n_diff} of those, and "
                     f"{n_trunc} carry the artifact's `truncation_suspect` flag.")
        L.append("")
        if not tr.turns:
            L.append("*(the artifact records no turns)*")
            L.append("")
            continue
        for t in tr.turns:
            _render_turn(L, t, tr.audio, cited.get((tr.persona_id, t.idx), ()))
    return L


def render_report(analysis: RunAnalysis, narrative: Narrative | None,
                  manifest: dict, *, generated_at: str = "",
                  llm_note: str = "",
                  transcripts: tuple[PersonaTranscript, ...] = ()) -> str:
    a = analysis
    gate = a.control_gate
    valid = gate.valid
    unval = "" if valid else " *(unvalidated — control failed)*"

    header = manifest.get("_header") or {}
    agent_name = header.get("agent_name") or (
        (manifest.get("config") or {}).get("target") or {}).get("agent_id") or "unknown target"
    judge_model = header.get("judge_model") or (
        (manifest.get("config") or {}).get("judge") or {}).get("model") or "unknown judge"
    n_all = len(a.personas)
    n_judged = sum(1 for p in a.personas if p.judged)

    L: list[str] = []
    L.append(f"# voice-spar report — run {a.run_id}")
    L.append("")
    L.append(f"Target: {agent_name} (ElevenLabs) · {n_all} conversations "
             f"({n_judged} judged) · judged by {judge_model}")
    L.append(f"Generated: {generated_at} by spar report{llm_note}")
    L.append("")
    L.append("## Verdict")
    L.append("")
    if gate.status == "pass":
        L.append(f"**{gate.summary}.**")
    elif gate.status == "no_control":
        L.append(f"**{gate.summary}.** RUN UNANCHORED: nothing below distinguishes a weak "
                 f"agent from a broken harness.")
    else:
        L.append(f"**{gate.summary}.** RUN INVALID: per-persona data follows for diagnosis, "
                 f"but no cross-persona finding below is promoted to a defect.")

    worst = a.worst_non_control
    if worst is not None:
        opt = (f" ({worst.scored_weight_pct:g}% of rubric weight scored — optimistic)"
               if worst.optimistic else "")
        L.append(f"Worst non-control result: {worst.persona_id} — {worst.weighted_score:.1f}"
                 f"{opt}, “{worst.band}”.")
    n_breach = len(a.clusters.by_breach)
    n_bleed, _, _ = _bleed_counts(a)
    n_det = sum(1 for f in a.findings_index if f.kind == "det_violation")
    bleed_note = _bleed_note(a)
    # A defect count with no name forces a jump to find out what it is. Name each proven
    # defect inline while the list is short; past 3 the section reference carries it.
    defect_bits: list[str] = []
    for c in a.clusters.by_breach[:3]:
        fid = next((f.id for f in a.findings_index
                    if f.kind == "breach" and f.key == c.entry), "")
        defect_bits.append(f"invented claim on {_persons(c.personas)}, §2.1 [{fid}]")
    for f in [f for f in a.findings_index if f.kind == "det_violation"][:3]:
        defect_bits.append(f"deterministic violation, §2.2 [{f.id}]")
    named = f" ({'; '.join(defect_bits)})" if defect_bits and (n_breach + n_det) <= 3 else ""
    L.append(f"Confirmed agent defects: {n_breach + n_det + n_bleed}{named}. "
             f"Scenario bleed: {bleed_note}. Eval health: {len(a.eval_fixes)} flag(s).")
    if a.agent_fixes:
        top = a.agent_fixes[0]
        L.append(f"Top fix: {top.title} [{top.finding_id}].")
    L.append("")

    # ── 1b. executive summary ────────────────────────────────────────────────────────────
    L.append("### Summary")
    L.append("")
    if narrative and len(narrative.executive_summary) >= 2:
        # Say whose sentences these are. Everything above and below the Summary is computed;
        # these lines are model prose over the computed findings, and a bracketed [F04] on an
        # LLM sentence otherwise borrows the authority of the deterministic finding it cites.
        L.append("*Model prose over the computed findings — each line survived the "
                 "traceability audit (numbers, finding ids, tier fidelity, no manufactured "
                 "quotes), but the claim it makes is the model's, not a computed one. The "
                 "computed findings themselves are §2, §3 and §6.*")
        L.append("")
        for s in narrative.executive_summary:
            L.append(f"- {s.text} [{', '.join(s.source_ids)}]")
    else:
        for s in _fallback_summary(a):
            L.append(f"- {s}")
        if narrative is None and not llm_note:
            L.append("- (deterministic narrative — the LLM call failed or was rejected; "
                     "every line above is template text over computed facts)")
    L.append("")

    # ── 2. scorecard table ───────────────────────────────────────────────────────────────
    L.append("## 1. Scorecards")
    L.append("")
    L.append("| persona | stress | score | band | rubric wt scored | deterministic coverage "
             "| end |")
    L.append("|---|---|---|---|---|---|---|")
    for p in a.report_personas:
        pid = p.persona_id + (" *(control — excluded from aggregates)*" if p.is_control else "")
        band = p.band or "—"
        swp = f"{p.scored_weight_pct:g}%" if p.scored_weight_pct is not None else "—"
        L.append(f"| {pid} | {p.stresses} | {_score_cell(p)} | {band} | {swp} "
                 f"| {_det_cell(p)} | {p.end_reason} |")
    L.append("")
    L.append("There is deliberately no run-level average: these personas are adversarial "
             "probes, not a traffic sample, and the control is excluded from every aggregate. "
             "The run ships at its weakest behaviour above. Every conversation behind these "
             "rows is reproduced in full, turn by turn, in §8.")
    L.append("")

    # ── 3. confirmed defects ─────────────────────────────────────────────────────────────
    L.append(f"## 2. Confirmed defects{unval}")
    L.append("")

    L.append("### 2.1 Ground-truth breaches")
    L.append("")
    if not a.clusters.by_breach:
        L.append("None. Every fail verdict that named no valid ground_truth entry was "
                 "discarded by the judge's own audit before this report.")
    for c in a.clusters.by_breach:
        fid = next((f.id for f in a.findings_index
                    if f.kind == "breach" and f.key == c.entry), "")
        L.append(f"**The agent breached the ground_truth entry {c.entry!r}** "
                 f"({len(c.occurrences)} occurrence(s), {_persons(c.personas)}) "
                 f"[{fid}]{unval}")
        for s in c.occurrences:
            L.append(f"> “{_q(s.quote)}” — ({s.persona_id}, turn {s.turn})")
        if c.provenance:
            L.append(f"  Audit trail: {_cite(c.provenance[0])}")
        L.append("")

    L.append("### 2.2 Deterministic violations")
    L.append("")
    det_findings = [f for f in a.findings_index if f.kind == "det_violation"]
    if not det_findings:
        L.append("None. Every percentage, rupee amount and date the script-aware checks "
                 "recognised in agent turns was inside its conversation's own ground_truth "
                 "(coverage per conversation in §5.4).")
    for f in det_findings:
        L.append(f"**{f.summary}** [{f.id}]{unval}")
        for s in f.sources[:4]:
            L.append(f"> {_cite(s)}")
    L.append("")

    L.append("### 2.3 Scenario bleed (cross-conversation)")
    L.append("")
    L.append("The four scenarios carry deliberately distinct values, so a value from one "
             "persona's scenario appearing in another's transcript is a provable defect no "
             "per-conversation judge can see. Unique values per scenario:")
    L.append("")
    L.append("| persona | ceiling | prices | dates (d/m) | subscriber | plan |")
    L.append("|---|---|---|---|---|---|")
    for s in a.signatures:
        ceil = f"{s.ceiling_pct:g}%" if s.ceiling_pct is not None else "—"
        L.append(f"| {s.persona_id} | {ceil} | "
                 f"{', '.join(str(x) for x in sorted(s.prices)) or '—'} | "
                 f"{', '.join(f'{d}/{m}' for d, m in sorted(s.dates)) or '—'} | "
                 f"{s.subscriber_name} | {s.plan_name} |")
    L.append("")
    if a.bleed:
        for b in a.bleed:
            fid = _bleed_fid(a, b)
            L.append(f"**{b.value} ({b.kind}) bled into {b.persona_id} at turn {b.turn}** — "
                     f"belongs to {_persons(b.source_persona_ids)} [{fid}]{unval}")
            L.append(f"> “{_q(b.quote)}” — ({b.persona_id}, turn {b.turn})")
            L.append(f"  {b.detail}")
            L.append("")
    else:
        bc = a.bleed_coverage
        L.append(f"**No bleed detected.** Numeric scan consumed the scorecards' own "
                 f"script-aware observations ({bc.numeric_source}) across "
                 f"{bc.conversations_scanned} conversations; the name/plan scan read every "
                 f"agent turn. Membership was tested on parsed values, never substrings, so "
                 f"shared prefixes and nested digits cannot false-positive.")
        L.append("")
        L.append("What this does NOT cover:")
        if bc.conversations_without_scorecard:
            L.append(f"- numeric bleed was not scanned in "
                     f"{', '.join(bc.conversations_without_scorecard)} (no scorecard).")
        if bc.scripts_note:
            L.append(f"- {bc.scripts_note}.")
        unrec = {k: v for k, v in bc.unrecognised_mentions.items() if v}
        if unrec:
            L.append(f"- unparseable numeric mentions, not testable for bleed: "
                     f"{', '.join(f'{k}: {v}' for k, v in sorted(unrec.items()))}.")
        if not (bc.conversations_without_scorecard or bc.scripts_note or unrec):
            L.append("- nothing: every conversation was scanned on both surfaces.")
    L.append("")

    # ── 4. recurring patterns ────────────────────────────────────────────────────────────
    L.append(f"## 3. Recurring patterns{unval}")
    L.append("")
    n = sum(1 for p in a.personas if not p.is_control and p.judged)
    if not a.clusters.by_dimension and not a.clusters.recurrent_absences:
        L.append("No dimension failed or dented in more than one pressure conversation.")
    for c in a.clusters.by_dimension:
        fid = next((f.id for f in a.findings_index
                    if f.kind == "cluster" and f.key == c.dimension), "")
        name = ""
        if narrative and fid in narrative.pattern_names:
            name = f" — “{narrative.pattern_names[fid]}”"
        rows = ", ".join(f"{p} {s:g}" for p, s in zip(c.affected, c.scores))
        L.append(f"### {c.dimension} ({c.weight:g}w) — {c.breakdown} across "
                 f"{len(c.affected)} of {n} pressure conversations{name} [{fid}]{unval}")
        L.append("")
        L.append(f"Scores: {rows} (mean {c.mean:g}; {_tier_breakdown(c)}).")
        seen: set[str] = set()
        for s in c.evidence:
            if s.persona_id in seen:
                continue
            seen.add(s.persona_id or "")
            if s.kind == "transcript" and s.quote:
                L.append(f"> “{_q(s.quote)}” — ({s.persona_id}, turn {s.turn})")
            elif s.quote:
                L.append(f"> {s.persona_id}: {_q(s.quote)} *(absence claim, verified by "
                         f"scan — no line exists to quote)*")
        L.append("")
    for c in a.clusters.recurrent_absences:
        fid = next((f.id for f in a.findings_index
                    if f.kind == "cluster" and f.key == f"absence:{c.claim}"), "")
        L.append(f"### Recurring verified absence [{fid}]{unval}")
        L.append("")
        L.append(f"“{c.claim}” held in {len(c.personas)} of {n} pressure "
                 f"conversations ({', '.join(c.personas)}), cited on "
                 f"{', '.join(c.dimensions)}. An absence has no line to quote; it was "
                 f"verified by scanning every agent turn for contradiction probes.")
        L.append("")

    # ── 5. agent fix list ────────────────────────────────────────────────────────────────
    L.append(f"## 4. Prioritised fix list — agent{unval}")
    L.append("")
    L.append("Priority = dimension weight × recurrence × severity. The formula's inputs are "
             "printed so the ranking is auditable, not trusted. Titles, priorities and "
             "citations are computed; an italic line under an item is model prose over them.")
    L.append("")
    if not a.agent_fixes:
        L.append("Nothing to fix from this run's evidence.")
    for i, fx in enumerate(a.agent_fixes, 1):
        L.append(f"{i}. **{fx.title}** — priority {fx.priority:g} ({fx.formula}) "
                 f"[{fx.finding_id}]")
        rationale = fx.llm_rationale or (
            narrative.fix_rationales.get(fx.finding_id) if narrative else None)
        if rationale:
            L.append(f"   *{rationale}*")
        for s in fx.sources[:2]:
            L.append(f"   - {_cite(s)}")
    L.append("")

    # ── 6. eval health ───────────────────────────────────────────────────────────────────
    L.append("## 5. Eval health — the eval grades itself")
    L.append("")
    L.append("Findings about the TOOL, with a different owner than §4. A rubric defect left "
             "here silently becomes a wrong agent verdict next run.")
    L.append("")

    flats = [s for s in a.spreads if s.flat]
    L.append("### 5.1 Dimensions that did not discriminate")
    L.append("")
    if not flats:
        L.append("None — every scored dimension separated at least two pressure personas.")
    for s in flats:
        L.append(f"- **{s.dimension}** ({s.weight:g}w): scores "
                 f"{', '.join(f'{x:g}' for x in s.scores)} across {s.scored_n} non-control "
                 f"conversations (range {s.range:g}). {s.note}.")
    L.append("")

    L.append("### 5.2 Unscoreable dimensions")
    L.append("")
    if not a.unscoreable:
        L.append("None — every dimension was scored in every judged conversation.")
    for u in a.unscoreable:
        L.append(f"- **{u.dimension}** ({u.weight:g}w) unscored in "
                 f"{', '.join(u.unscored_in)}: {'; '.join(u.reasons)}. {u.note}.")
    L.append("")

    L.append("### 5.3 Evidence-audit rejections")
    L.append("")
    L.append(f"- {a.rejections.note}.")
    for s in a.rejections.details:
        L.append(f"  - {s.persona_id}: {_q(s.quote)}")
    L.append("")

    L.append("### 5.4 Deterministic coverage")
    L.append("")
    # `coverage_rollup` coerces a null checked_fraction to 0.0 so `min()` has a float; the
    # per-persona row keeps the artifact's own value, because printing "0" for "the scorecard
    # reports null" states a coverage number the judge never computed.
    raw_frac = {p.persona_id: p.det_checked_fraction for p in a.personas if p.judged}
    for pid, (frac, verdict) in sorted(a.coverage.per_persona.items()):
        mark = "" if verdict == "full" and frac >= 1.0 else \
            " — numeric surface only PARTIALLY verified; 'clean' is not printed for it"
        shown = _num(raw_frac.get(pid, frac),
                     missing="null (no percentage, amount or date appeared in the agent turns "
                             "— nothing was compared)")
        L.append(f"- {pid}: checked_fraction {shown}, verdict {verdict}{mark}")
    L.append(f"- minimum scored rubric weight: {a.coverage.min_scored_weight_pct:g}% "
             f"(below 100 the weighted score is renormalised over what WAS scored, and "
             f"unscored dimensions skew toward failures — the number is optimistic)")
    for spot in a.coverage.run_wide_blind_spots:
        L.append(f"- **run-wide blind spot:** {spot}. Absence of a finding on this surface "
                 f"is not evidence of correctness; it was never checked.")
    for pid in a.coverage.missing_scorecards:
        L.append(f"- **{pid} was never judged** — excluded from every statistic above, "
                 f"still scanned for lexical bleed.")
    L.append("")

    L.append("### 5.5 Fix list — eval")
    L.append("")
    for i, fx in enumerate(a.eval_fixes, 1):
        L.append(f"{i}. **{fx.title}** — priority {fx.priority:g} ({fx.formula}) "
                 f"[{fx.finding_id}]")
    if not a.eval_fixes:
        L.append("No tool defects surfaced by this run.")
    L.append("")

    if a.warnings:
        L.append("### 5.6 Analysis warnings")
        L.append("")
        for w in a.warnings:
            L.append(f"- {w}")
        L.append("")

    # ── 7. findings index ────────────────────────────────────────────────────────────────
    L.append("## 6. Findings index")
    L.append("")
    L.append("Every bracketed id above resolves here; every entry cites the exact file, JSON "
             "path and (for transcripts) turn + verbatim quote.")
    L.append("")
    for f in a.findings_index:
        L.append(f"**{f.id}** ({f.kind}) — {f.summary}")
        for s in f.sources[:4]:
            L.append(f"  - {_cite(s)}")
        if len(f.sources) > 4:
            L.append(f"  - (+{len(f.sources) - 4} more citations in synthesis.json)")
        L.append("")

    # ── 8. run appendix ──────────────────────────────────────────────────────────────────
    L.append("## 7. Run appendix")
    L.append("")
    L.append(f"- run started {manifest.get('started_at')} · wall clock "
             f"{manifest.get('duration_s')}s · level {manifest.get('level')}")
    for row in manifest.get("personas") or []:
        tc = row.get("turn_count") or {}
        L.append(f"- {row.get('persona_id')}: {tc.get('total')} turns "
                 f"({tc.get('agent')} agent / {tc.get('persona')} persona), "
                 f"{row.get('duration_s')}s, ended {row.get('end_reason')!r} "
                 f"({row.get('end_kind')}), errors {row.get('errors')}")
    totals = manifest.get("totals") or {}
    if totals:
        L.append(f"- totals: {totals.get('conversations')} conversations, "
                 f"{totals.get('ok')} ok, {totals.get('failed')} failed, "
                 f"{totals.get('turns')} turns")
    for w in manifest.get("warnings") or []:
        L.append(f"- run.json warning (verbatim): {w}")
    L.append("")

    # ── 9. transcripts ───────────────────────────────────────────────────────────────────
    # LAST, deliberately. It is the longest section by far and it is an appendix: the verdict,
    # the defects and the fix list are what a reader acts on, and none of them may be pushed
    # down the page by the evidence they rest on.
    L += render_transcripts(transcripts, cited_turns(a),
                            tuple(p.persona_id for p in a.report_personas))
    return "\n".join(L)


def _persons(ids) -> str:
    ids = list(ids)
    return ids[0] if len(ids) == 1 else ", ".join(ids)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §3.1 — orchestration
# ═════════════════════════════════════════════════════════════════════════════════════════

def _llm_audit_json(audit: LLMAudit | None, errors: list[str]) -> dict[str, Any] | None:
    """The audit block, whether or not there was an audit to report. A failed ladder still
    has to say what it tried; a clean `--no-llm` run has nothing to say at all."""
    if audit is None and not errors:
        return None
    base = audit.to_json() if audit is not None else {"accepted": 0, "rejected": []}
    return base | {"call_errors": errors}


async def generate_report(run_dir: Path, cfg: Config, *,
                          only: list[str] | None = None,
                          use_llm: bool = True) -> dict[str, Any]:
    """Loads, analyses, narrates, renders, writes. A report is ALWAYS written on return."""
    run_dir = Path(run_dir)
    try:
        inputs = load_run(run_dir, only=only)
    except SynthError as exc:
        raise ReportError(str(exc)) from exc

    analysis = analyse_run(inputs)

    narrative: Narrative | None = None
    llm_audit: LLMAudit | None = None
    llm_meta: dict[str, Any] | None = None
    llm_errors: list[str] = []
    if use_llm:
        digest = build_digest(analysis)
        parsed, llm_meta, llm_errors = await _call_llm(cfg, digest)
        if parsed is not None:
            narrative, llm_audit = narrate(parsed, digest, analysis)
        for e in llm_errors:
            log.warning("narrative LLM: %s", e)
        if parsed is None:
            log.warning("narrative LLM unavailable after retries — deterministic fallback")

    # Header facts that live in the conversation artifacts, not the redacted manifest.
    agent_name = ""
    for p in inputs.personas:
        agent_name = str((p.conversation.get("target") or {}).get("agent_name") or "")
        if agent_name:
            break
    judge_model = ""
    for p in inputs.judged_personas:
        assert p.scorecard is not None
        judge_model = str((p.scorecard.get("judge") or {}).get("model") or "")
        if judge_model:
            break
    manifest = dict(inputs.manifest)
    manifest["_header"] = {"agent_name": agent_name, "judge_model": judge_model}

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    llm_note = "" if use_llm else " (--no-llm: deterministic narrative only, zero LLM calls)"
    # Built from the conversation artifacts already in memory — no LLM, no second read, so
    # `--no-llm` and a total LLM outage both still produce the appendix in full.
    report_ids = set(analysis.report_ids) or {p.persona_id for p in inputs.personas}
    transcripts = build_transcripts(
        [p for p in inputs.personas if p.persona_id in report_ids])
    report_md = render_report(analysis, narrative, manifest,
                              generated_at=generated_at, llm_note=llm_note,
                              transcripts=transcripts)

    synthesis = {
        "analysis": analysis.to_json(),
        # Additive: every pre-existing key keeps its exact shape and meaning. A consumer that
        # does not know about transcripts is unaffected; one that does gets the same turns the
        # appendix rendered, with the same citation markers.
        "transcripts": [t.to_json() for t in transcripts],
        "cited_turns": [{"persona_id": pid, "turn": turn, "markers": list(marks)}
                        for (pid, turn), marks in sorted(cited_turns(analysis).items())],
        "narrative": narrative.to_json() if narrative else None,
        # `llm_audit` is None whenever the call ladder was exhausted, and `llm_errors` is
        # exactly then non-empty — so the old `(llm_audit or llm_errors)` guard was TRUE with
        # llm_audit None and `None.to_json()` killed every LLM-failure path, taking the whole
        # report with it. The deterministic fallback below it was unreachable code.
        "llm_audit": _llm_audit_json(llm_audit, llm_errors) if use_llm else None,
        "generated_at": generated_at,
        "generator": "synth/report.py",
        "llm": llm_meta,
    }
    synthesis_blob = json.dumps(synthesis, ensure_ascii=False, indent=2)

    # SYNTH_SPEC §0.3 / §3.4: the model's reasoning enters no artifact, ever, and nothing in
    # this repo may even name the vendor simulator. Checked on the bytes about to be written.
    for blob, name in ((synthesis_blob, "synthesis.json"), (report_md, "report.md")):
        for banned in ("reasoning_content", "simulate-conversation"):
            if banned in blob:
                raise ReportError(f"refusing to write {name}: contains {banned!r}")

    report_path = run_dir / "report.md"
    synthesis_path = run_dir / "synthesis.json"
    try:
        report_path.write_text(report_md, encoding="utf-8")
        synthesis_path.write_text(synthesis_blob + "\n", encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"cannot write report artifacts: {exc}") from exc

    return {
        "report_path": str(report_path),
        "synthesis_path": str(synthesis_path),
        "control_gate": analysis.control_gate.to_json(),
        "n_findings": len(analysis.findings_index),
        "warnings": list(analysis.warnings),
    }


__all__ = [
    "ReportError", "LLMSentence", "Narrative", "LLMAudit", "ClusterTiers",
    "audit_llm_sentences", "cluster_tiers_from", "allowed_numbers_from", "build_digest",
    "render_report", "generate_report",
    "TurnView", "PersonaTranscript", "build_transcripts", "is_audio_conversation",
    "cited_turns", "render_transcripts",
]


# ═════════════════════════════════════════════════════════════════════════════════════════
# Selftest for the transcript appendix — no API key, no network, no writes, no run dir.
#   PYTHONPATH=. uv run --python 3.12 python -m synth.report
# ═════════════════════════════════════════════════════════════════════════════════════════

def _selftest() -> int:  # pragma: no cover - developer tool
    ok = True

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))

    class _Doc:
        def __init__(self, pid, conv, ctrl=False):
            self.persona_id, self.conversation, self.is_control = pid, conv, ctrl

    long_line = "Arre, maine socha tha ki abhi series nahi aa rahi, toh thoda save kar loon."
    heard_line = "अरे, मैंने सथोड़ा save कर लूँ।"
    l0 = {"level": 0, "target": {"mode": "text"}, "end_reason": {"code": "goal_reached"},
          "turns": [{"idx": 0, "speaker": "agent", "text": "line one\n\nline two",
                     "meta": {"is_opening": True}},
                    {"idx": 1, "speaker": "persona", "text": long_line,
                     "meta": {"attempts": 1, "sent": True}}]}
    l1 = {"level": 1, "target": {"mode": "audio"}, "end_reason": {"code": "seconds_over",
                                                                  "kind": "hard"},
          "turns": [{"idx": 0, "speaker": "agent", "text": "opening", "meta": {}},
                    {"idx": 1, "speaker": "persona", "text": long_line,
                     "meta": {"text_provenance": "persona_intended",
                              "tara_heard": {"text": heard_line, "event_id": 40,
                                             "provenance": "asr",
                                             "truncation_suspect": True}}},
                    {"idx": 2, "speaker": "agent", "text": "reply",
                     "meta": {"text_provenance": "agent_emitted", "speech_s": 12.0,
                              "peak": 22161}},
                    {"idx": 3, "speaker": "persona", "text": "never spoken",
                     "meta": {"sent": False}}]}

    print("LEVEL DETECTION (from the artifact, never from config)")
    check("text/level 0 artifact is not audio", not is_audio_conversation(l0))
    check("audio/level 1 artifact is audio", is_audio_conversation(l1))
    check("mode 'audio' alone is enough", is_audio_conversation({"target": {"mode": "audio"}}))
    check("level 1 alone is enough", is_audio_conversation({"level": 1}))
    check("an empty artifact is Level 0, not audio", not is_audio_conversation({}))

    t0 = build_transcripts([_Doc("p0", l0)])[0]
    t1 = build_transcripts([_Doc("p1", l1)])[0]
    md0 = "\n".join(render_transcripts((t0,), {}, ("p0",)))
    md1 = "\n".join(render_transcripts((t1,), {("p1", 2): ("hallucination [F01]",)}, ("p1",)))

    print("LEVEL 0 — a plain transcript, no empty audio columns, no apology")
    check("no 'heard' anywhere", "heard" not in md0.lower())
    check("no ASR note", "ASR" not in md0 and "asr" not in md0)
    check("no audio meta printed", "speech " not in md0 and "peak " not in md0)
    check("multi-line turn keeps its break", "> line one\n>\n> line two" in md0)
    check("the persona line is uncut", long_line in md0)

    print("LEVEL 1 — the spoken/heard pair, adjacent and complete")
    check("both streams present", "we said:" in md1 and "Tara heard:" in md1)
    check("spoken line in full", long_line in md1)
    check("heard line in full", heard_line in md1)
    check("they are adjacent", md1.index(long_line) < md1.index(heard_line)
          < md1.index(long_line) + 400)
    check("difference is flagged", "ASR differs from the spoken line" in md1)
    check("truncation flag surfaced", "truncation_suspect: true" in md1)
    check("provenance surfaced", "`persona_intended`" in md1 and "`agent_emitted`" in md1)
    check("agent audio facts surfaced", "speech 12s" in md1 and "peak 22161" in md1)
    check("citation marker lands on the cited turn",
          "←  cited: hallucination [F01]" in md1.split("**turn 2")[1].split("**turn 3")[0])
    check("an undelivered persona line says so, and is still printed",
          "never delivered" in md1 and "never spoken" in md1)

    print("MULTI-PERSONA — sections follow the scorecard order, and none is dropped")
    many = build_transcripts([_Doc("b", l1), _Doc("a", l0), _Doc("c", l1, ctrl=True)])
    md = "\n".join(render_transcripts(many, {}, ("a", "b", "c")))
    heads = [ln for ln in md.split("\n") if ln.startswith("### ")]
    check("one section per persona, in the given order", len(heads) == 3
          and " a " in heads[0] and " b " in heads[1] and " c " in heads[2], str(heads))
    check("the control is labelled", "control — excluded from aggregates" in heads[2])
    check("a persona absent from the order is still rendered",
          len([ln for ln in "\n".join(
              render_transcripts(many, {}, ("a",))).split("\n") if ln.startswith("### ")]) == 3)

    print("ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_selftest())
