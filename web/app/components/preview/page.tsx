/**
 * Component preview harness — route `/components/preview`.
 *
 * This exists so the five presentation components can be exercised against REAL
 * run artefacts before the API and the real page are wired up. It reads
 * `runs/<run_id>/` straight off disk; it never invents data.
 *
 * It is a dev surface, not the product. Delete this folder once `app/page.tsx`
 * mounts the components for real — nothing else imports it.
 */
import fs from 'node:fs';
import path from 'node:path';
import PreviewClient, { type RunBundle } from './PreviewClient';
import type {
  Conversation,
  ControlGate,
  RunPersonaSummary,
  RunSummary,
  Scorecard,
} from '../types';

export const dynamic = 'force-dynamic';

const RUNS_DIR = path.join(process.cwd(), '..', 'runs');
/** Enough runs to cover both a Level 0 text run and a Level 1 audio run. */
const MAX_RUNS = 8;

function readJson<T>(file: string): T | null {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8')) as T;
  } catch {
    return null;
  }
}

function readDirJson<T>(dir: string): T[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => readJson<T>(path.join(dir, f)))
    .filter((x): x is T => x !== null);
}

function humaniseId(id: string): string {
  const s = id.replace(/[_-]+/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

interface RunJson {
  run_id?: string;
  level?: number;
  started_at?: string;
  config?: { target?: { mode?: string } };
  personas?: Array<{ persona_id: string; end_reason?: string }>;
}

function loadBundle(runId: string): RunBundle | null {
  const dir = path.join(RUNS_DIR, runId);
  const runJson = readJson<RunJson>(path.join(dir, 'run.json'));
  if (!runJson) return null;

  const conversations = readDirJson<Conversation>(
    path.join(dir, 'conversations')
  );
  const scorecards = readDirJson<Scorecard>(path.join(dir, 'scorecards'));

  const synthesis = readJson<{ analysis?: { control_gate?: ControlGate } }>(
    path.join(dir, 'synthesis.json')
  );
  const controlGate = synthesis?.analysis?.control_gate ?? null;

  const audioDir = path.join(dir, 'audio');
  const hasAudio =
    fs.existsSync(audioDir) && fs.readdirSync(audioDir).length > 0;

  const cardById = new Map(scorecards.map((c) => [c.persona_id, c]));
  const convById = new Map(conversations.map((c) => [c.persona_id, c]));

  const ids = new Set<string>([
    ...(runJson.personas ?? []).map((p) => p.persona_id),
    ...conversations.map((c) => c.persona_id),
    ...scorecards.map((c) => c.persona_id),
  ]);

  const personas: RunPersonaSummary[] = [...ids].sort().map((id) => {
    const card = cardById.get(id);
    const conv = convById.get(id);
    const endReason =
      typeof conv?.end_reason === 'string'
        ? conv.end_reason
        : (conv?.end_reason?.code ?? null);
    return {
      id,
      name: conv?.persona_name ?? humaniseId(id),
      isControl:
        conv?.persona_is_control === true ||
        (controlGate?.control_ids ?? []).includes(id),
      score: card?.weighted_score ?? null,
      band: card?.band ?? null,
      endReason,
    };
  });

  const summary: RunSummary = {
    id: runId,
    startedAt: runJson.started_at ?? '',
    level: runJson.level ?? 0,
    mode: runJson.config?.target?.mode ?? (hasAudio ? 'audio' : 'text'),
    personaCount: personas.length,
    hasAudio,
    hasReport: fs.existsSync(path.join(dir, 'report.md')),
    personas,
  };

  return { summary, conversations, scorecards, controlGate };
}

export default async function PreviewPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const wanted = typeof params.run === 'string' ? params.run : null;
  const wantedPersona =
    typeof params.persona === 'string' ? params.persona : null;

  if (!fs.existsSync(RUNS_DIR)) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-20">
        <p className="text-sm text-neutral-500">
          No <code className="font-mono">runs/</code> directory at{' '}
          <code className="font-mono">{RUNS_DIR}</code>.
        </p>
      </main>
    );
  }

  const runIds = fs
    .readdirSync(RUNS_DIR)
    .filter((d) => !d.startsWith('.') && !d.startsWith('_'))
    .filter((d) => fs.existsSync(path.join(RUNS_DIR, d, 'run.json')))
    .sort()
    .reverse()
    .slice(0, MAX_RUNS);

  const bundles = runIds
    .map(loadBundle)
    .filter((b): b is RunBundle => b !== null)
    .filter((b) => b.conversations.length > 0);

  return (
    <PreviewClient
      bundles={bundles}
      initialRunId={wanted}
      initialPersonaId={wantedPersona}
    />
  );
}
