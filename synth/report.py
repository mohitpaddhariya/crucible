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


def render_report(analysis: RunAnalysis, narrative: Narrative | None,
                  manifest: dict, *, generated_at: str = "",
                  llm_note: str = "") -> str:
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
             "The run ships at its weakest behaviour above.")
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
    report_md = render_report(analysis, narrative, manifest,
                              generated_at=generated_at, llm_note=llm_note)

    synthesis = {
        "analysis": analysis.to_json(),
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
]
