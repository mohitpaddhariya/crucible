'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AudioTurnMeta, Turn } from './types';
import { EmptyState, TRANSCRIPT_TEXT, formatSeconds } from './ui';

export interface PlayerControls {
  /** Seek to the start of a turn and play it. */
  playTurn: (turnIdx: number) => void;
  play: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
}

export interface ConversationPlayerProps {
  runId: string;
  personaId: string;
  personaName?: string;
  agentLabel?: string;
  /** Transcript turns — used only for the hover/preview text on each segment. */
  turns?: Turn[];
  /** Fires whenever the spoken turn changes (null between/after turns). */
  onActiveTurnChange?: (turnIdx: number | null) => void;
  /** Handed the transport once the timeline has loaded, so a parent can drive it. */
  onReady?: (controls: PlayerControls) => void;
  /** Override for a non-default API mount point. */
  audioBase?: string;
  className?: string;
}

type Status = 'loading' | 'ready' | 'unavailable' | 'error';

/**
 * Plays the whole conversation as one WAV and highlights the turn currently
 * being spoken, driven by the `?meta=1` timeline. Two AI agents actually
 * talking — so the transport gets real estate and the controls are obvious.
 */
export default function ConversationPlayer({
  runId,
  personaId,
  personaName = 'Customer',
  agentLabel = 'Agent',
  turns,
  onActiveTurnChange,
  onReady,
  audioBase = '/api/audio',
  className = '',
}: ConversationPlayerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const [timeline, setTimeline] = useState<AudioTurnMeta[]>([]);
  const [status, setStatus] = useState<Status>('loading');
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [rate, setRate] = useState(1);

  const fullUrl = `${audioBase}/${encodeURIComponent(runId)}/${encodeURIComponent(personaId)}/full`;

  const textByTurn = useMemo(() => {
    const m = new Map<number, string>();
    for (const t of turns ?? []) m.set(t.idx, t.text);
    return m;
  }, [turns]);

  /* ---------------------------------------------------- load the timeline */

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    setError(null);
    setTimeline([]);

    fetch(`${fullUrl}?meta=1`)
      .then(async (res) => {
        if (res.status === 404) {
          if (!cancelled) setStatus('unavailable');
          return null;
        }
        if (!res.ok) throw new Error(`audio timeline returned ${res.status}`);
        return (await res.json()) as AudioTurnMeta[] | { turns: AudioTurnMeta[] };
      })
      .then((body) => {
        if (cancelled || body === null) return;
        const rows = Array.isArray(body) ? body : (body.turns ?? []);
        const clean = rows
          .filter((r) => Number.isFinite(r.startS) && Number.isFinite(r.durationS))
          .sort((a, b) => a.startS - b.startS);
        setTimeline(clean);
        setStatus(clean.length ? 'ready' : 'unavailable');
        const last = clean[clean.length - 1];
        if (last) setDuration(last.startS + last.durationS);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setStatus('error');
      });

    return () => {
      cancelled = true;
    };
  }, [fullUrl]);

  /* -------------------------------------------- which turn is speaking now */

  const activeTurn = useMemo(() => {
    for (const row of timeline) {
      if (current >= row.startS && current < row.startS + row.durationS) {
        return row.turnIdx;
      }
    }
    return null;
  }, [current, timeline]);

  const onActiveTurnChangeRef = useRef(onActiveTurnChange);
  onActiveTurnChangeRef.current = onActiveTurnChange;
  const lastReported = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    if (lastReported.current === activeTurn) return;
    lastReported.current = activeTurn;
    onActiveTurnChangeRef.current?.(activeTurn);
  }, [activeTurn]);

  /* ------------------------------------------------------------- controls */

  const seek = useCallback((seconds: number) => {
    const el = audioRef.current;
    if (!el) return;
    el.currentTime = Math.max(0, seconds);
    setCurrent(Math.max(0, seconds));
  }, []);

  const play = useCallback(() => {
    void audioRef.current?.play().catch(() => {
      /* autoplay policy — the user has to press play, which is what they did */
    });
  }, []);

  const pause = useCallback(() => {
    audioRef.current?.pause();
  }, []);

  const playTurn = useCallback(
    (turnIdx: number) => {
      const row = timeline.find((r) => r.turnIdx === turnIdx);
      if (!row) return;
      seek(row.startS + 0.01);
      play();
    },
    [timeline, seek, play]
  );

  const controls = useMemo<PlayerControls>(
    () => ({ playTurn, play, pause, seek }),
    [playTurn, play, pause, seek]
  );

  // Held in a ref so an inline `onReady={c => ...}` from the parent cannot turn
  // this into a render loop.
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  useEffect(() => {
    if (status === 'ready') onReadyRef.current?.(controls);
  }, [status, controls]);

  useEffect(() => {
    const el = audioRef.current;
    if (el) el.playbackRate = rate;
  }, [rate]);

  /* ---------------------------------------------------------- empty paths */

  if (status === 'error') {
    return (
      <EmptyState
        className={className}
        title="Audio timeline failed to load"
        detail={error ?? undefined}
      />
    );
  }

  if (status === 'unavailable') {
    return (
      <EmptyState
        className={className}
        title="No audio for this conversation"
        detail="This run has no per-turn WAVs — it was a Level 0 text run, or the audio was not kept. The transcript below is the complete record."
      />
    );
  }

  const total = duration || 0;
  const pct = total > 0 ? Math.min(100, (current / total) * 100) : 0;
  const activeRow = timeline.find((r) => r.turnIdx === activeTurn) ?? null;
  const activeText = activeTurn === null ? null : textByTurn.get(activeTurn) ?? null;

  return (
    <div
      className={`rounded-2xl border border-neutral-800 bg-neutral-900/50 p-6 sm:p-8 ${className}`}
    >
      <audio
        ref={audioRef}
        src={fullUrl}
        preload="metadata"
        onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => {
          const d = e.currentTarget.duration;
          if (Number.isFinite(d) && d > 0) setDuration(d);
        }}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          setCurrent(0);
        }}
      />

      <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
        <button
          type="button"
          onClick={() => (playing ? pause() : play())}
          disabled={status !== 'ready'}
          aria-label={playing ? 'Pause conversation' : 'Play conversation'}
          className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-neutral-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {playing ? (
            <svg viewBox="0 0 12 14" className="h-5 w-5" aria-hidden="true">
              <rect x="0" y="0" width="4" height="14" fill="currentColor" />
              <rect x="8" y="0" width="4" height="14" fill="currentColor" />
            </svg>
          ) : (
            <svg viewBox="0 0 12 14" className="ml-1 h-5 w-5" aria-hidden="true">
              <path d="M0 0 L12 7 L0 14 Z" fill="currentColor" />
            </svg>
          )}
        </button>

        <div>
          <p className="text-sm font-medium text-neutral-200">
            {agentLabel}{' '}
            <span className="text-neutral-600">calling</span> {personaName}
          </p>
          <p className="mt-0.5 text-xs text-neutral-500">
            {status === 'loading'
              ? 'loading timeline…'
              : `${timeline.length} recorded turns · full conversation audio`}
          </p>
        </div>

        <div className="ml-auto flex items-center gap-4">
          <span className="font-mono text-sm tabular-nums text-neutral-400">
            {formatSeconds(current)}
            <span className="mx-1 text-neutral-700">/</span>
            {formatSeconds(total)}
          </span>
          <div className="flex overflow-hidden rounded-lg border border-neutral-800">
            {[1, 1.25, 1.5].map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRate(r)}
                className={`px-2.5 py-1 text-xs tabular-nums transition ${
                  rate === r
                    ? 'bg-neutral-700 text-neutral-100'
                    : 'text-neutral-500 hover:text-neutral-300'
                }`}
              >
                {r}×
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ------------------------------------------------- scrub + segments */}

      <div className="mt-7">
        <div className="relative">
          {/* Per-turn segments: each one is a play button for that turn. */}
          <div className="flex h-14 w-full items-stretch gap-[2px] overflow-hidden rounded-lg bg-neutral-950/60">
            {timeline.map((row) => {
              const isActive = row.turnIdx === activeTurn;
              const isAgent = row.speaker === 'agent';
              const width = total > 0 ? (row.durationS / total) * 100 : 0;
              const preview = textByTurn.get(row.turnIdx);
              return (
                <button
                  key={`${row.turnIdx}-${row.startS}`}
                  type="button"
                  onClick={() => playTurn(row.turnIdx)}
                  title={
                    preview
                      ? `Turn ${row.turnIdx} · ${isAgent ? agentLabel : personaName}\n${preview}`
                      : `Play turn ${row.turnIdx}`
                  }
                  aria-label={`Play turn ${row.turnIdx}`}
                  style={{ width: `${width}%`, minWidth: 6 }}
                  className={`group relative flex items-end justify-center pb-1 transition ${
                    isActive
                      ? isAgent
                        ? 'bg-sky-400'
                        : 'bg-emerald-400'
                      : isAgent
                        ? 'bg-sky-500/25 hover:bg-sky-500/50'
                        : 'bg-emerald-500/25 hover:bg-emerald-500/50'
                  }`}
                >
                  <span
                    className={`font-mono text-[9px] leading-none ${
                      isActive ? 'text-neutral-950' : 'text-neutral-500 group-hover:text-neutral-200'
                    }`}
                  >
                    {row.turnIdx}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Playhead */}
          <div
            className="pointer-events-none absolute top-0 h-14 w-px bg-neutral-100/90"
            style={{ left: `${pct}%` }}
          />
        </div>

        <input
          type="range"
          min={0}
          max={total || 0}
          step={0.05}
          value={Math.min(current, total || 0)}
          onChange={(e) => seek(Number(e.target.value))}
          aria-label="Seek"
          className="mt-3 h-1 w-full cursor-pointer appearance-none rounded bg-neutral-800 accent-emerald-400"
        />

        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] uppercase tracking-wider text-neutral-600">
          <LegendDot className="bg-sky-500/60" label={agentLabel} />
          <LegendDot className="bg-emerald-500/60" label={personaName} />
          <span className="normal-case tracking-normal text-neutral-600">
            click any segment to play that turn
          </span>
        </div>
      </div>

      {/* ----------------------------------------------------- now speaking */}

      <div className="mt-6 min-h-[5.5rem] rounded-xl border border-neutral-800 bg-neutral-950/50 px-5 py-4">
        {activeRow ? (
          <>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest">
              <span
                className={
                  activeRow.speaker === 'agent'
                    ? 'text-sky-400'
                    : 'text-emerald-400'
                }
              >
                {activeRow.speaker === 'agent' ? agentLabel : personaName}
              </span>
              <span className="ml-3 font-mono normal-case tracking-normal text-neutral-600">
                turn {activeRow.turnIdx}
              </span>
            </p>
            {activeText ? (
              <p style={TRANSCRIPT_TEXT} className="text-neutral-200">
                {activeText}
              </p>
            ) : (
              <p className="text-sm text-neutral-600">
                No transcript text was passed for this turn.
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-neutral-600">
            {playing ? 'silence between turns' : 'press play to hear the call'}
          </p>
        )}
      </div>
    </div>
  );
}

function LegendDot({ className, label }: { className: string; label: string }) {
  return (
    <span className="flex items-center gap-2">
      <span className={`h-2 w-2 rounded-sm ${className}`} />
      {label}
    </span>
  );
}
