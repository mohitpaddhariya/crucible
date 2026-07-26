/**
 * Disk readers for `runs/<run_id>/`.
 *
 * Rules this module keeps:
 *  - Nothing is hardcoded to a run id.
 *  - Partial runs (conversations but no scorecards, audio but no conversations,
 *    a run.json that never got written) list and load without throwing.
 *  - `synthesis.json` is the structured source of truth; `report.md` is only
 *    ever checked for existence, never parsed.
 *  - `.env` is never read and no credential ever leaves here.
 */

import fs from 'fs';
import path from 'path';

import {
  exists,
  fileSize,
  isDir,
  listDir,
  personaAudioDir,
  readJson,
  RUNS_DIR,
  runDir,
  safeSegment,
  turnPcmPath,
} from './paths';
import type {
  Conversation,
  Dimension,
  EndReason,
  Evidence,
  GroundTruthAudit,
  Level,
  PersonaDetail,
  PersonaSummary,
  RunDetail,
  RunSummary,
  Scorecard,
  Speaker,
  Synthesis,
  Turn,
} from './types';
import { pcmDurationS } from './wav';

/**
 * Fixed display order for the four demo personas. Anything else sorts after
 * them, alphabetically — a new persona never disappears, it just goes last.
 */
export const PERSONA_ORDER = [
  'price-haggler',
  'happy-path',
  'already-switched',
  'angry-churner',
];

function personaRank(id: string): number {
  const i = PERSONA_ORDER.indexOf(id);
  return i === -1 ? PERSONA_ORDER.length : i;
}

export function comparePersonaIds(a: string, b: string): number {
  const d = personaRank(a) - personaRank(b);
  return d !== 0 ? d : a.localeCompare(b);
}

// ---------------------------------------------------------------------------
// Small coercions — every field on disk is treated as possibly absent.
// ---------------------------------------------------------------------------

const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const bool = (v: unknown): boolean => v === true;

/** `20260726-083836-d39549` → `2026-07-26T08:38:36.000Z`. Fallback only. */
function startedAtFromRunId(id: string): string | null {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(id);
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  return `${y}-${mo}-${d}T${h}:${mi}:${s}.000Z`;
}

// ---------------------------------------------------------------------------
// Run discovery
// ---------------------------------------------------------------------------

/**
 * Every real run directory under `runs/`.
 * Skips `_spike*` and every other underscore-prefixed scratch dir, plus dotfiles
 * and loose files (Archive.zip, .DS_Store, .gitkeep).
 */
export function listRunIds(): string[] {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(RUNS_DIR, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .filter((name) => !name.startsWith('_') && !name.startsWith('.'))
    .filter((name) => safeSegment(name) !== null);
}

/** Persona ids present in a run, from conversations ∪ scorecards ∪ audio dirs. */
export function listPersonaIds(runId: string): string[] {
  const dir = runDir(runId);
  if (!dir) return [];
  const ids = new Set<string>();
  for (const sub of ['conversations', 'scorecards']) {
    for (const f of listDir(path.join(dir, sub))) {
      if (f.endsWith('.json')) {
        const id = safeSegment(f.slice(0, -5));
        if (id) ids.add(id);
      }
    }
  }
  const audioRoot = path.join(dir, 'audio');
  if (isDir(audioRoot)) {
    for (const f of listDir(audioRoot)) {
      const id = safeSegment(f);
      if (id && isDir(path.join(audioRoot, f))) ids.add(id);
    }
  }
  return [...ids].sort(comparePersonaIds);
}

/** True when this persona has at least one `.pcm` on disk. */
export function personaHasAudio(runId: string, personaId: string): boolean {
  const dir = personaAudioDir(runId, personaId);
  if (!isDir(dir)) return false;
  return listDir(dir).some((f) => f.endsWith('.pcm'));
}

export function runHasAudio(runId: string): boolean {
  const dir = runDir(runId);
  if (!dir) return false;
  const audioRoot = path.join(dir, 'audio');
  if (!isDir(audioRoot)) return false;
  return listDir(audioRoot).some((p) => personaHasAudio(runId, p));
}

// ---------------------------------------------------------------------------
// Turn audio resolution
// ---------------------------------------------------------------------------

/**
 * Where a turn's audio actually lives.
 *
 * Disk is the authority, not `meta.audio_path`: the opening agent turn has a
 * PCM file but no `audio_path` in its meta, and the final persona turn of a
 * timed-out run has neither. `meta.audio_path` is also an absolute path from
 * the machine that produced the run, so it is not portable anyway.
 */
export function turnAudioFile(
  runId: string,
  personaId: string,
  turnIdx: number,
  speaker: Speaker
): { file: string; size: number } | null {
  const file = turnPcmPath(runId, personaId, turnIdx, speaker);
  if (!file) return null;
  const size = fileSize(file);
  if (size === null || size <= 0) return null;
  return { file, size };
}

/** Which speaker owns `turn_<idx>_*.pcm`, when we only have the index. */
export function resolveTurnSpeaker(
  runId: string,
  personaId: string,
  turnIdx: number
): Speaker | null {
  for (const speaker of ['agent', 'persona'] as const) {
    if (turnAudioFile(runId, personaId, turnIdx, speaker)) return speaker;
  }
  return null;
}

export const audioUrlForTurn = (runId: string, personaId: string, turnIdx: number) =>
  `/api/audio/${encodeURIComponent(runId)}/${encodeURIComponent(personaId)}/${turnIdx}`;

export const fullAudioUrl = (runId: string, personaId: string) =>
  `/api/audio/${encodeURIComponent(runId)}/${encodeURIComponent(personaId)}/full`;

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

function parseEndReason(v: unknown): EndReason {
  const e = obj(v);
  return {
    code: str(e.code) ?? 'unknown',
    kind: str(e.kind),
    detail: str(e.detail),
    atTurn: num(e.at_turn),
    evidence: e.evidence ?? null,
  };
}

function parseTurn(raw: unknown, runId: string, personaId: string): Turn {
  const t = obj(raw);
  const meta = obj(t.meta);
  const idx = num(t.idx) ?? 0;
  const speaker: Speaker = t.speaker === 'agent' ? 'agent' : 'persona';

  const audio = turnAudioFile(runId, personaId, idx, speaker);
  const heard = obj(meta.tara_heard);

  return {
    idx,
    speaker,
    text: typeof t.text === 'string' ? t.text : '',
    latencyMs: num(t.latency_ms),
    ts: str(t.ts),
    eventId: num(t.event_id),
    textProvenance: str(meta.text_provenance),
    hasAudio: audio !== null,
    audioDurationS: audio ? round3(pcmDurationS(audio.size)) : null,
    audioUrl: audio ? audioUrlForTurn(runId, personaId, idx) : null,
    taraHeard: meta.tara_heard
      ? {
          text: typeof heard.text === 'string' ? heard.text : '',
          eventId: num(heard.event_id),
          provenance: str(heard.provenance),
          truncationSuspect: bool(heard.truncation_suspect),
        }
      : null,
    meta,
  };
}

const round3 = (n: number) => Math.round(n * 1000) / 1000;

export function readConversation(runId: string, personaId: string): Conversation | null {
  const dir = runDir(runId);
  const persona = safeSegment(personaId);
  if (!dir || !persona) return null;
  const raw = readJson<Record<string, unknown>>(
    path.join(dir, 'conversations', `${persona}.json`)
  );
  if (!raw) return null;

  const target = obj(raw.target);
  const turns = arr(raw.turns)
    .map((t) => parseTurn(t, runId, persona))
    .sort((a, b) => a.idx - b.idx);
  const hasAudio = turns.some((t) => t.hasAudio);
  const tc = obj(raw.turn_count);

  return {
    personaId: str(raw.persona_id) ?? persona,
    personaName: str(raw.persona_name) ?? persona,
    isControl: bool(raw.persona_is_control),
    stresses: str(raw.persona_stresses),
    level: (num(raw.level) === 1 ? 1 : 0) as Level,
    mode: str(target.mode),
    agentName: str(target.agent_name),
    startedAt: str(raw.started_at),
    endedAt: str(raw.ended_at),
    durationS: num(raw.duration_s) ?? 0,
    endReason: parseEndReason(raw.end_reason),
    turnCount: {
      total: num(tc.total) ?? turns.length,
      agent: num(tc.agent) ?? turns.filter((t) => t.speaker === 'agent').length,
      persona: num(tc.persona) ?? turns.filter((t) => t.speaker === 'persona').length,
    },
    turns,
    hasAudio,
    fullAudioUrl: hasAudio ? fullAudioUrl(runId, persona) : null,
    fullAudioTimelineUrl: hasAudio ? `${fullAudioUrl(runId, persona)}?meta=1` : null,
    scenarioVars: obj(raw.scenario_vars),
    groundTruth: obj(raw.ground_truth),
    speech: raw.speech ? obj(raw.speech) : null,
    usage: obj(raw.usage),
    cost: obj(raw.cost),
    errors: arr(raw.errors),
    warnings: arr(raw.warnings),
  };
}

// ---------------------------------------------------------------------------
// Scorecards
// ---------------------------------------------------------------------------

function parseEvidence(v: unknown): Evidence[] {
  return arr(v)
    .map((e) => obj(e))
    .filter((e) => typeof e.quote === 'string' && e.quote)
    .map((e) => ({
      turn: num(e.turn),
      quote: String(e.quote),
      kind: str(e.kind),
    }));
}

function parseGroundTruthAudit(v: unknown): GroundTruthAudit | null {
  if (!v || typeof v !== 'object') return null;
  const a = obj(v);
  const valid = arr(a.valid)
    .map((x) => obj(x))
    .map((x) => ({
      entry: typeof x.entry === 'string' ? x.entry : '',
      turn: num(x.turn),
      quote: typeof x.quote === 'string' ? x.quote : '',
    }));
  return { valid, raw: a };
}

export function readScorecard(runId: string, personaId: string): Scorecard | null {
  const dir = runDir(runId);
  const persona = safeSegment(personaId);
  if (!dir || !persona) return null;
  const raw = readJson<Record<string, unknown>>(path.join(dir, 'scorecards', `${persona}.json`));
  if (!raw) return null;

  const dimensions: Dimension[] = Object.entries(obj(raw.dimensions)).map(([key, value]) => {
    const d = obj(value);
    return {
      key,
      score: num(d.score),
      weight: num(d.weight) ?? 0,
      verdict: str(d.verdict) ?? '',
      reasoning: str(d.reasoning) ?? '',
      scored: d.scored !== false,
      unscoredReason: str(d.unscored_reason),
      evidence: parseEvidence(d.evidence),
      groundTruthAudit: parseGroundTruthAudit(d.ground_truth_audit),
    };
  });
  dimensions.sort((a, b) => b.weight - a.weight || a.key.localeCompare(b.key));

  const coverage = obj(raw.coverage);
  return {
    personaId: str(raw.persona_id) ?? persona,
    weightedScore: num(raw.weighted_score),
    band: str(raw.band),
    coveragePct: num(coverage.scored_weight_pct) ?? 0,
    coverage,
    dimensions,
    deterministic: obj(raw.deterministic),
    evidenceAudit: obj(raw.evidence_audit),
    judgedAt: str(raw.judged_at),
  };
}

// ---------------------------------------------------------------------------
// Synthesis
// ---------------------------------------------------------------------------

export function readSynthesis(runId: string): Synthesis | null {
  const dir = runDir(runId);
  if (!dir) return null;
  const raw = readJson<Record<string, unknown>>(path.join(dir, 'synthesis.json'));
  if (!raw) return null;
  return {
    ...raw,
    analysis: (raw.analysis as Synthesis['analysis']) ?? null,
    narrative: raw.narrative ?? null,
    llm_audit: raw.llm_audit ?? null,
    generated_at: str(raw.generated_at),
    generator: str(raw.generator),
    llm: raw.llm ?? null,
  };
}

// ---------------------------------------------------------------------------
// Run summary + detail
// ---------------------------------------------------------------------------

type RunMeta = {
  startedAt: string;
  endedAt: string | null;
  durationS: number | null;
  warnings: string[];
};

function readRunMeta(runId: string, conversations: Conversation[]): RunMeta {
  const dir = runDir(runId);
  const raw = dir ? readJson<Record<string, unknown>>(path.join(dir, 'run.json')) : null;

  const convStarts = conversations.map((c) => c.startedAt).filter((s): s is string => !!s).sort();
  const convEnds = conversations.map((c) => c.endedAt).filter((s): s is string => !!s).sort();

  const startedAt =
    str(raw?.started_at) ??
    convStarts[0] ??
    startedAtFromRunId(runId) ??
    new Date(0).toISOString();

  return {
    startedAt,
    endedAt: str(raw?.ended_at) ?? convEnds[convEnds.length - 1] ?? null,
    durationS: num(raw?.duration_s),
    warnings: arr(raw?.warnings).filter((w): w is string => typeof w === 'string'),
  };
}

/**
 * Level of a run.
 *
 * NOT taken from `run.json.level` — that field says 0 on runs that are plainly
 * Level 1 (audio on disk, conversations that say `"level": 1`). The
 * conversation artifacts are the honest source; audio on disk is the fallback.
 */
function deriveLevel(conversations: Conversation[], hasAudio: boolean): Level {
  if (conversations.length) {
    return conversations.some((c) => c.level === 1) ? 1 : 0;
  }
  return hasAudio ? 1 : 0;
}

export function getRunSummary(runId: string): RunSummary | null {
  const dir = runDir(runId);
  if (!isDir(dir)) return null;

  const personaIds = listPersonaIds(runId);
  const conversations: Conversation[] = [];
  const personas: PersonaSummary[] = [];

  for (const id of personaIds) {
    const conv = readConversation(runId, id);
    const card = readScorecard(runId, id);
    if (conv) conversations.push(conv);
    const audio = personaHasAudio(runId, id);
    personas.push({
      id,
      name: conv?.personaName ?? id,
      isControl: conv?.isControl ?? false,
      score: card?.weightedScore ?? null,
      band: card?.band ?? null,
      endReason: conv?.endReason.code ?? null,
      hasAudio: audio,
      fullAudioUrl: audio ? fullAudioUrl(runId, id) : null,
      fullAudioTimelineUrl: audio ? `${fullAudioUrl(runId, id)}?meta=1` : null,
    });
  }

  const hasAudio = runHasAudio(runId);
  const meta = readRunMeta(runId, conversations);
  const mode = conversations.find((c) => c.mode)?.mode ?? null;

  return {
    id: runId,
    startedAt: meta.startedAt,
    level: deriveLevel(conversations, hasAudio),
    mode,
    personaCount: personaIds.length,
    hasAudio,
    hasReport: exists(path.join(dir!, 'report.md')),
    personas,
  };
}

/** Every run, newest first. Never throws on a partial or broken run directory. */
export function getAllRunSummaries(): RunSummary[] {
  const out: RunSummary[] = [];
  for (const id of listRunIds()) {
    try {
      const s = getRunSummary(id);
      if (s) out.push(s);
    } catch {
      // A single unreadable run must never take the index down.
    }
  }
  out.sort((a, b) => (a.startedAt < b.startedAt ? 1 : a.startedAt > b.startedAt ? -1 : b.id.localeCompare(a.id)));
  return out;
}

export function getRunDetail(runId: string): RunDetail | null {
  const dir = runDir(runId);
  if (!isDir(dir)) return null;

  const readErrors: string[] = [];
  const personaIds = listPersonaIds(runId);
  const conversations: Conversation[] = [];
  const personas: PersonaDetail[] = [];

  for (const id of personaIds) {
    let conversation: Conversation | null = null;
    let scorecard: Scorecard | null = null;
    try {
      conversation = readConversation(runId, id);
    } catch (e) {
      readErrors.push(`conversations/${id}.json: ${(e as Error).message}`);
    }
    try {
      scorecard = readScorecard(runId, id);
    } catch (e) {
      readErrors.push(`scorecards/${id}.json: ${(e as Error).message}`);
    }
    if (conversation) conversations.push(conversation);
    if (!conversation && exists(path.join(dir!, 'conversations', `${id}.json`))) {
      readErrors.push(`conversations/${id}.json is unreadable or not valid JSON`);
    }
    const audio = personaHasAudio(runId, id);
    personas.push({
      id,
      name: conversation?.personaName ?? id,
      isControl: conversation?.isControl ?? false,
      score: scorecard?.weightedScore ?? null,
      band: scorecard?.band ?? null,
      endReason: conversation?.endReason.code ?? null,
      hasAudio: audio,
      fullAudioUrl: audio ? fullAudioUrl(runId, id) : null,
      fullAudioTimelineUrl: audio ? `${fullAudioUrl(runId, id)}?meta=1` : null,
      stresses: conversation?.stresses ?? null,
      conversation,
      scorecard,
    });
  }

  const hasAudio = runHasAudio(runId);
  const meta = readRunMeta(runId, conversations);
  const synthesis = readSynthesis(runId);

  return {
    id: runId,
    startedAt: meta.startedAt,
    endedAt: meta.endedAt,
    durationS: meta.durationS,
    level: deriveLevel(conversations, hasAudio),
    mode: conversations.find((c) => c.mode)?.mode ?? null,
    agentName: conversations.find((c) => c.agentName)?.agentName ?? null,
    personaCount: personaIds.length,
    hasAudio,
    hasReport: exists(path.join(dir!, 'report.md')),
    hasSynthesis: synthesis !== null,
    personas,
    synthesis,
    warnings: meta.warnings,
    readErrors,
  };
}
