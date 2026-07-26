import fs from 'fs';
import path from 'path';
import YAML from 'yaml';

export const RUN_ID = '20260726-061134-705345';
const ROOT = path.join(process.cwd(), '..');
const RUN_DIR = path.join(ROOT, 'runs', RUN_ID);

// Fixed display order across every screen.
export const ORDER = ['price-haggler', 'happy-path', 'already-switched', 'angry-churner'];
const rank = (id: string) => {
  const i = ORDER.indexOf(id);
  return i === -1 ? 99 : i;
};

export type Persona = {
  id: string;
  name: string;
  stresses: string;
  who: string;
  language: string;
  offer_text: string;
  control: boolean;
};

export type Turn = {
  idx: number;
  speaker: 'agent' | 'persona';
  text: string;
};

export type Conversation = {
  persona_id: string;
  persona_name: string;
  duration_s: number;
  end_reason: string;
  turn_count: number;
  turns: Turn[];
};

export type Evidence = { turn: number; quote: string };

export type Dimension = {
  key: string;
  score: number | null;
  weight: number;
  verdict: string;
  reasoning: string;
  evidence: Evidence[];
};

export type Scorecard = {
  persona_id: string;
  weighted_score: number;
  band: string;
  coverage_pct: number;
  dimensions: Dimension[];
};

export type Defect = { entry: string; turn: number; quote: string };

export function getPersonas(): Persona[] {
  const dir = path.join(ROOT, 'personas');
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.yaml') || f.endsWith('.yml'));
  const out: Persona[] = [];
  for (const f of files) {
    try {
      const d = YAML.parse(fs.readFileSync(path.join(dir, f), 'utf8')) ?? {};
      out.push({
        id: d.id ?? f.replace(/\.ya?ml$/, ''),
        name: d.name ?? d.id ?? f,
        stresses: String(d.stresses ?? '').trim(),
        who: d.identity?.who ?? '',
        language: d.language?.primary ?? '',
        offer_text: d.scenario?.vars?.offer_text ?? '',
        control: d.control === true,
      });
    } catch {
      // A malformed persona must never take the whole demo down.
    }
  }
  return out.sort((a, b) => rank(a.id) - rank(b.id));
}

export function getConversations(): Conversation[] {
  const dir = path.join(RUN_DIR, 'conversations');
  if (!fs.existsSync(dir)) return [];
  const out: Conversation[] = [];
  for (const f of fs.readdirSync(dir).filter((f) => f.endsWith('.json'))) {
    try {
      const d = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
      out.push({
        persona_id: d.persona_id,
        persona_name: d.persona_name ?? d.persona_id,
        duration_s: d.duration_s ?? 0,
        end_reason: d.end_reason?.code ?? 'unknown',
        turn_count: d.turn_count?.total ?? (d.turns ?? []).length,
        turns: (d.turns ?? []).map((t: Turn) => ({
          idx: t.idx,
          speaker: t.speaker === 'agent' ? 'agent' : 'persona',
          text: t.text ?? '',
        })),
      });
    } catch {}
  }
  return out.sort((a, b) => rank(a.persona_id) - rank(b.persona_id));
}

export function getScorecards(): Scorecard[] {
  const dir = path.join(RUN_DIR, 'scorecards');
  if (!fs.existsSync(dir)) return [];
  const out: Scorecard[] = [];
  for (const f of fs.readdirSync(dir).filter((f) => f.endsWith('.json'))) {
    try {
      const d = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
      const dims: Dimension[] = Object.entries(d.dimensions ?? {}).map(
        ([key, v]) => {
          const dim = v as Record<string, unknown>;
          return {
            key,
            score: typeof dim.score === 'number' ? dim.score : null,
            weight: Number(dim.weight ?? 0),
            verdict: String(dim.verdict ?? ''),
            reasoning: String(dim.reasoning ?? ''),
            evidence: ((dim.evidence ?? []) as Evidence[])
              .filter((e) => e && e.quote)
              .map((e) => ({ turn: e.turn, quote: e.quote })),
          };
        }
      );
      dims.sort((a, b) => b.weight - a.weight);
      out.push({
        persona_id: d.persona_id,
        weighted_score: d.weighted_score,
        band: d.band,
        coverage_pct: d.coverage?.scored_weight_pct ?? 0,
        dimensions: dims,
      });
    } catch {}
  }
  return out.sort((a, b) => rank(a.persona_id) - rank(b.persona_id));
}

/** The confirmed ground-truth breaches from the already-switched run. */
export function getDefects(): Defect[] {
  const f = path.join(RUN_DIR, 'scorecards', 'already-switched.json');
  if (!fs.existsSync(f)) return [];
  try {
    const d = JSON.parse(fs.readFileSync(f, 'utf8'));
    const valid = d?.dimensions?.hallucination?.ground_truth_audit?.valid ?? [];
    return valid.map((v: Defect) => ({ entry: v.entry, turn: v.turn, quote: v.quote }));
  } catch {
    return [];
  }
}
