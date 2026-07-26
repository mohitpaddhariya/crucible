'use client';

import { useEffect, useState } from 'react';
import type { RunSummary } from './types';
import {
  Badge,
  EmptyState,
  bandTone,
  formatDate,
  scoreTone,
  toneText,
} from './ui';

export interface RunPickerProps {
  /** Rows from `GET /api/runs`. Pass [] with `loading` while they are in flight. */
  runs: RunSummary[];
  selectedRunId?: string | null;
  onSelect?: (runId: string) => void;
  loading?: boolean;
  /** Message from a failed fetch — rendered verbatim, not swallowed. */
  error?: string | null;
  className?: string;
}

/**
 * Choose among the real runs on disk. Level badge (TEXT / AUDIO), date, persona
 * count, and every persona's score — so a run can be picked on its evidence
 * rather than by hardcoding one id.
 */
export default function RunPicker({
  runs,
  selectedRunId = null,
  onSelect,
  loading = false,
  error = null,
  className = '',
}: RunPickerProps) {
  if (error) {
    return (
      <EmptyState
        className={className}
        title="Could not load runs"
        detail={error}
      />
    );
  }

  if (loading) {
    return (
      <div className={`space-y-3 ${className}`} aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-24 animate-pulse rounded-2xl border border-neutral-800 bg-neutral-900/30"
          />
        ))}
      </div>
    );
  }

  if (!runs.length) {
    return (
      <EmptyState
        className={className}
        title="No runs found"
        detail="Nothing has been written to runs/ yet. Execute a sparring run and this list fills itself."
      />
    );
  }

  return (
    <div className={`space-y-3 ${className}`}>
      {runs.map((run) => (
        <RunRow
          key={run.id}
          run={run}
          selected={run.id === selectedRunId}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function RunRow({
  run,
  selected,
  onSelect,
}: {
  run: RunSummary;
  selected: boolean;
  onSelect?: (runId: string) => void;
}) {
  const isAudio = run.mode === 'audio' || run.hasAudio;

  return (
    <button
      type="button"
      onClick={() => onSelect?.(run.id)}
      aria-pressed={selected}
      className={`w-full rounded-2xl border px-6 py-5 text-left transition ${
        selected
          ? 'border-emerald-500/50 bg-emerald-500/[0.07]'
          : 'border-neutral-800 bg-neutral-900/30 hover:border-neutral-700 hover:bg-neutral-900/60'
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Badge tone={isAudio ? 'good' : 'unknown'}>
          {isAudio ? 'Audio' : 'Text'}
        </Badge>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-600">
          Level {run.level}
        </span>

        <span className="font-mono text-sm text-neutral-300">{run.id}</span>

        <span className="text-sm text-neutral-500">
          {formatDate(run.startedAt)}
        </span>

        <span className="text-sm text-neutral-500">
          {run.personaCount} persona{run.personaCount === 1 ? '' : 's'}
        </span>

        <span className="ml-auto flex items-center gap-3 text-[11px] uppercase tracking-wider">
          <Capability on={run.hasAudio} label="audio" />
          <Capability on={run.hasReport} label="report" />
        </span>
      </div>

      {run.personas.length ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {run.personas.map((p) => (
            <PersonaScoreChip key={p.id} persona={p} />
          ))}
        </div>
      ) : (
        <p className="mt-4 text-sm text-neutral-600">
          No personas recorded for this run.
        </p>
      )}
    </button>
  );
}

function Capability({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={on ? 'text-neutral-400' : 'text-neutral-700 line-through'}>
      {label}
    </span>
  );
}

function PersonaScoreChip({
  persona,
}: {
  persona: RunSummary['personas'][number];
}) {
  const tone = persona.score === null ? bandTone(persona.band) : scoreTone(persona.score);

  return (
    <span
      title={
        [
          persona.name,
          persona.band ?? 'not judged',
          persona.endReason ? `ended: ${persona.endReason}` : null,
        ]
          .filter(Boolean)
          .join(' · ')
      }
      className={`inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs ${
        persona.isControl
          ? 'border-sky-500/40 bg-sky-500/[0.07]'
          : 'border-neutral-800 bg-neutral-900/60'
      }`}
    >
      {persona.isControl ? (
        <span className="text-[9px] font-bold uppercase tracking-widest text-sky-300">
          Ctrl
        </span>
      ) : null}
      <span className="text-neutral-400">{persona.name}</span>
      <span className={`font-semibold tabular-nums ${toneText(tone)}`}>
        {persona.score === null ? 'n/a' : persona.score.toFixed(1)}
      </span>
    </span>
  );
}

/* --------------------------------------------------------------- helpers */

export interface UseRunsResult {
  runs: RunSummary[];
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Optional client-side loader for `GET /api/runs`, so RunPicker can be dropped in
 * without a server component doing the fetch. Server-rendered pages should pass
 * `runs` directly instead.
 */
export function useRuns(endpoint = '/api/runs'): UseRunsResult {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(endpoint)
      .then(async (res) => {
        if (!res.ok) throw new Error(`${endpoint} returned ${res.status}`);
        return (await res.json()) as RunSummary[] | { runs: RunSummary[] };
      })
      .then((body) => {
        if (cancelled) return;
        setRuns(Array.isArray(body) ? body : (body.runs ?? []));
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [endpoint, nonce]);

  return { runs, loading, error, reload: () => setNonce((n) => n + 1) };
}
