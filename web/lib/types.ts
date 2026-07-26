/**
 * Shared types for the voice-spar web UI data layer.
 *
 * Everything here is derived from what is actually on disk under `runs/<run_id>/`.
 * Nothing is hardcoded to a single run id. Components should code against these
 * types and never read the filesystem themselves.
 */

export type Speaker = 'agent' | 'persona';

/** 0 = text-only conversation, 1 = real audio (TTS/STT over the wire). */
export type Level = 0 | 1;

/** `target.mode` from the conversation artifact. */
export type TargetMode = 'text' | 'audio' | string;

// ---------------------------------------------------------------------------
// GET /api/runs  →  RunSummary[]
// ---------------------------------------------------------------------------

export type PersonaSummary = {
  id: string;
  name: string;
  isControl: boolean;
  /** Weighted score 0-100, or null when the persona has no scorecard yet. */
  score: number | null;
  /** e.g. "production-ready" | "do not ship" | null when unjudged. */
  band: string | null;
  /** `end_reason.code`, e.g. "goal_reached" | "seconds_over" | null. */
  endReason: string | null;
  /**
   * True when this persona has PCM on disk. Determined from the filesystem, so
   * it is correct even for an in-flight run whose conversation JSON has not
   * been written yet.
   */
  hasAudio: boolean;
  /** Stitched whole-conversation WAV, or null when there is no audio. */
  fullAudioUrl: string | null;
  /** Timeline JSON for that WAV, or null when there is no audio. */
  fullAudioTimelineUrl: string | null;
};

export type RunSummary = {
  id: string;
  /** ISO 8601. From run.json, else earliest conversation, else parsed from the run id. */
  startedAt: string;
  /** Derived from the conversations (run.json's `level` is unreliable — see notes). */
  level: Level;
  /** "text" | "audio" | null when there are no conversations yet. */
  mode: TargetMode | null;
  personaCount: number;
  hasAudio: boolean;
  hasReport: boolean;
  personas: PersonaSummary[];
};

// ---------------------------------------------------------------------------
// GET /api/runs/[id]  →  RunDetail
// ---------------------------------------------------------------------------

export type EndReason = {
  code: string;
  kind: string | null;
  detail: string | null;
  atTurn: number | null;
  evidence: unknown;
};

export type TurnCount = {
  total: number;
  agent: number;
  persona: number;
};

/** What the ElevenLabs agent's ASR believed the persona said (audio runs only). */
export type TaraHeard = {
  text: string;
  eventId: number | null;
  provenance: string | null;
  truncationSuspect: boolean;
};

export type Turn = {
  idx: number;
  speaker: Speaker;
  text: string;
  latencyMs: number | null;
  ts: string | null;
  eventId: number | null;
  /** "persona_intended" | "agent_emitted" | null (text runs). */
  textProvenance: string | null;
  /** True when a playable `turn_<idx>_<speaker>.pcm` exists on disk. */
  hasAudio: boolean;
  /** Real duration measured from the PCM byte length, not from metadata. */
  audioDurationS: number | null;
  /** Ready-to-use `<audio src>`; null when there is no audio for this turn. */
  audioUrl: string | null;
  /** Audio-run persona turns only. */
  taraHeard: TaraHeard | null;
  /** The untouched `meta` blob, for anything not lifted above. */
  meta: Record<string, unknown>;
};

export type Evidence = {
  turn: number | null;
  quote: string;
  kind: string | null;
};

export type Dimension = {
  key: string;
  score: number | null;
  weight: number;
  verdict: string;
  reasoning: string;
  scored: boolean;
  unscoredReason: string | null;
  evidence: Evidence[];
  /** Present on `hallucination`: the confirmed ground-truth breaches. */
  groundTruthAudit: GroundTruthAudit | null;
};

export type GroundTruthBreach = {
  entry: string;
  turn: number | null;
  quote: string;
};

export type GroundTruthAudit = {
  valid: GroundTruthBreach[];
  /** Raw audit blob — shape varies by judge version. */
  raw: Record<string, unknown>;
};

export type Scorecard = {
  personaId: string;
  weightedScore: number | null;
  band: string | null;
  /** `coverage.scored_weight_pct`. */
  coveragePct: number;
  coverage: Record<string, unknown>;
  /** Sorted by weight, heaviest first. */
  dimensions: Dimension[];
  deterministic: Record<string, unknown>;
  evidenceAudit: Record<string, unknown>;
  judgedAt: string | null;
};

export type Conversation = {
  personaId: string;
  personaName: string;
  isControl: boolean;
  /** Which rubric dimension this persona is designed to stress. */
  stresses: string | null;
  level: Level;
  mode: TargetMode | null;
  agentName: string | null;
  startedAt: string | null;
  endedAt: string | null;
  durationS: number;
  endReason: EndReason;
  turnCount: TurnCount;
  turns: Turn[];
  /** True when at least one turn has a PCM file on disk. */
  hasAudio: boolean;
  /** Stitched whole-conversation WAV; null on level 0 / no audio. */
  fullAudioUrl: string | null;
  /** Timeline JSON for the stitched WAV; null when there is no audio. */
  fullAudioTimelineUrl: string | null;
  scenarioVars: Record<string, unknown>;
  groundTruth: Record<string, unknown>;
  speech: Record<string, unknown> | null;
  usage: Record<string, unknown>;
  cost: Record<string, unknown>;
  errors: unknown[];
  warnings: unknown[];
};

/** One persona in a run: its conversation plus its scorecard, already merged. */
export type PersonaDetail = PersonaSummary & {
  stresses: string | null;
  conversation: Conversation | null;
  scorecard: Scorecard | null;
};

export type RunDetail = {
  id: string;
  startedAt: string;
  endedAt: string | null;
  durationS: number | null;
  level: Level;
  mode: TargetMode | null;
  agentName: string | null;
  personaCount: number;
  hasAudio: boolean;
  hasReport: boolean;
  hasSynthesis: boolean;
  personas: PersonaDetail[];
  /** Structured synthesizer output (`synthesis.json`), verbatim. Never report.md. */
  synthesis: Synthesis | null;
  /** Run-level warnings from run.json. */
  warnings: string[];
  /** Non-fatal problems the reader hit (missing/corrupt files). Never throws. */
  readErrors: string[];
};

/**
 * `synthesis.json`. `analysis` is the deterministic structured roll-up and is
 * the thing worth rendering; `narrative` / `llm_audit` are null unless an LLM
 * pass ran. Left loosely typed on purpose — synth/report.py owns this schema.
 */
export type Synthesis = {
  analysis: SynthesisAnalysis | null;
  /** Full transcripts the synthesizer worked from, when it emits them. */
  transcripts?: Array<Record<string, unknown>>;
  /** Turn-level markers: which findings cite which turn. */
  cited_turns?: Array<{ persona_id: string; turn: number; markers: string[] }>;
  narrative: unknown;
  llm_audit: unknown;
  generated_at: string | null;
  generator: string | null;
  llm: unknown;
  /** synth/report.py owns this schema; unknown keys pass through untouched. */
  [key: string]: unknown;
};

export type SynthesisAnalysis = {
  schema_version?: string;
  run_id?: string;
  report_ids?: string[];
  control_gate?: {
    status: string;
    control_ids: string[];
    reasons: unknown[];
    summary: string;
    sources?: unknown[];
  };
  personas?: Array<Record<string, unknown>>;
  signatures?: Array<Record<string, unknown>>;
  bleed?: Array<Record<string, unknown>>;
  bleed_coverage?: Record<string, unknown>;
  clusters?: Record<string, unknown>;
  spreads?: Array<Record<string, unknown>>;
  unscoreable?: Array<Record<string, unknown>>;
  rejections?: Record<string, unknown>;
  coverage?: Record<string, unknown>;
  agent_fixes?: Array<Record<string, unknown>>;
  eval_fixes?: Array<Record<string, unknown>>;
  findings_index?: Array<Record<string, unknown>>;
  warnings?: string[];
  [key: string]: unknown;
};

// ---------------------------------------------------------------------------
// GET /api/audio/[runId]/[personaId]/full?meta=1  →  AudioTimeline
// ---------------------------------------------------------------------------

export type TimelineEntry = {
  turnIdx: number;
  speaker: Speaker;
  /** Seconds into the stitched WAV where this turn starts. */
  startS: number;
  durationS: number;
};

export type AudioTimeline = {
  runId: string;
  personaId: string;
  sampleRate: number;
  /** Seconds of silence inserted between turns. */
  gapS: number;
  totalDurationS: number;
  /** Byte length of the WAV that `/full` returns (44-byte header included). */
  byteLength: number;
  turns: TimelineEntry[];
};

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export type ApiError = { error: string };

/** Audio format on disk: raw headerless PCM, 16 kHz, mono, signed 16-bit LE. */
export const PCM_SAMPLE_RATE = 16000;
export const PCM_CHANNELS = 1;
export const PCM_BITS_PER_SAMPLE = 16;
