/**
 * Shapes consumed by the presentation components.
 *
 * NOTE: the brief says to import these from `web/lib/types.ts`. At the time these
 * components were written that file did not exist yet (the API is being built in
 * parallel), so the interfaces are declared here instead, matching the documented
 * API contract and the real run artefacts on disk under `runs/<run_id>/`.
 *
 * TypeScript is structural: once `web/lib/types.ts` lands, either re-export from
 * here (`export type { RunSummary } from '@/lib/types'`) or delete this file and
 * repoint the imports. No component logic depends on the nominal identity.
 */

/* ------------------------------------------------------------------ runs */

/** One persona row inside a run summary — `GET /api/runs`. */
export interface RunPersonaSummary {
  id: string;
  name: string;
  isControl: boolean;
  /** 0–100 weighted score, or null when the persona was never judged. */
  score: number | null;
  /** "production-ready" | "ships with known gaps" | "will generate support tickets" | "do not ship" */
  band: string | null;
  endReason: string | null;
}

/** `GET /api/runs` → RunSummary[] */
export interface RunSummary {
  id: string;
  /** ISO-8601 */
  startedAt: string;
  /** 0 = text, 1 = audio. */
  level: number;
  /** "text" | "audio" */
  mode: string;
  personaCount: number;
  hasAudio: boolean;
  hasReport: boolean;
  personas: RunPersonaSummary[];
}

/* ---------------------------------------------------------- conversation */

/** What the target agent's ASR actually transcribed for a persona turn. */
export interface TaraHeard {
  text: string;
  event_id?: number | null;
  /** "asr" */
  provenance?: string;
  /** The ASR emitted an event that looks cut off mid-utterance. */
  truncation_suspect?: boolean;
}

export interface TurnMeta {
  tara_heard?: TaraHeard | null;
  audio_path?: string | null;
  playout_s?: number | null;
  is_opening?: boolean;
  text_provenance?: string;
  [key: string]: unknown;
}

export interface Turn {
  idx: number;
  speaker: 'agent' | 'persona';
  text: string;
  latency_ms?: number | null;
  ts?: string | null;
  event_id?: number | null;
  meta?: TurnMeta | null;
}

export interface EndReason {
  code: string;
  kind?: string | null;
  detail?: string | null;
  at_turn?: number | null;
}

export interface TurnCount {
  total: number;
  agent: number;
  persona: number;
}

export interface Conversation {
  persona_id: string;
  persona_name?: string;
  persona_is_control?: boolean;
  /** 0 = text-only, 1 = real audio. */
  level?: number;
  duration_s?: number;
  /** The runner writes an object; some API shapes flatten it to a string. Both are accepted. */
  end_reason?: EndReason | string | null;
  turn_count?: TurnCount;
  turns: Turn[];
}

/* ------------------------------------------------------------ scorecards */

export interface Evidence {
  kind?: string;
  turn: number;
  quote: string;
}

/** One confirmed ground-truth breach — `dimensions.hallucination.ground_truth_audit.valid[]`. */
export interface GroundTruthBreach {
  /** The rule from the persona's ground truth that this line broke. */
  entry: string;
  entry_kind?: string;
  turn: number;
  quote: string;
}

export interface GroundTruthAudit {
  breaches_claimed?: number;
  breaches_valid?: number;
  valid?: GroundTruthBreach[];
  voided?: unknown[];
  reprompted?: boolean;
}

export interface Dimension {
  /** 0.0–1.0, or null when the judge could not score it. */
  score: number | null;
  /** "pass" | "partial" | "fail" */
  verdict?: string;
  /** Rubric weight out of 100. */
  weight?: number;
  reasoning?: string;
  evidence?: Evidence[];
  scored?: boolean;
  unscored_reason?: string | null;
  ground_truth_audit?: GroundTruthAudit;
}

export interface Coverage {
  scored_weight_pct?: number;
  unscored_dimensions?: string[];
  note?: string;
}

export interface Scorecard {
  persona_id: string;
  /** 0–100. */
  weighted_score: number | null;
  band: string | null;
  coverage?: Coverage;
  dimensions: Record<string, Dimension>;
  overall_note?: string | null;
  conversation?: {
    end_reason?: string;
    duration_s?: number;
    turn_count?: TurnCount;
  };
  deterministic?: {
    violation_count?: number;
    status?: string;
    summary?: string;
  };
}

/* ------------------------------------------------------------- synthesis */

export interface ControlGate {
  /** "pass" | "fail" */
  status: string;
  control_ids?: string[];
  reasons?: string[];
  summary?: string;
}

export interface Synthesis {
  analysis?: {
    control_gate?: ControlGate;
    personas?: unknown[];
  };
  [key: string]: unknown;
}

/* ----------------------------------------------------------------- audio */

/** `GET /api/audio/[runId]/[personaId]/full?meta=1` → AudioTurnMeta[] */
export interface AudioTurnMeta {
  turnIdx: number;
  speaker: string;
  startS: number;
  durationS: number;
}
