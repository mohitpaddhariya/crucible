/**
 * Shared visual primitives. Dark, calm, generous. No component here fetches or
 * owns state — they exist so the five real components stay readable.
 */
import type { CSSProperties, ReactNode } from 'react';

/**
 * Transcript type stack. Transcripts are frequently Devanagari, so:
 *  - never monospace (Devanagari conjuncts break badly in mono faces),
 *  - explicit Indic-capable families first,
 *  - line-height well above a Latin default, because matras sit above and below
 *    the baseline and collide at 1.4.
 */
export const TRANSCRIPT_TEXT: CSSProperties = {
  fontFamily:
    '"Noto Sans Devanagari", "Nirmala UI", "Kohinoor Devanagari", "Devanagari Sangam MN", "Mangal", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
  lineHeight: 1.85,
  fontSize: '1.0625rem',
  letterSpacing: '0.005em',
};

/** Same face, one step larger — for evidence quotes meant to read from a distance. */
export const QUOTE_TEXT: CSSProperties = {
  ...TRANSCRIPT_TEXT,
  fontSize: '1.375rem',
  lineHeight: 1.7,
};

export type BandTone = 'good' | 'ok' | 'warn' | 'bad' | 'unknown';

export function bandTone(band: string | null | undefined): BandTone {
  switch ((band ?? '').toLowerCase()) {
    case 'production-ready':
      return 'good';
    case 'ships with known gaps':
      return 'ok';
    case 'will generate support tickets':
      return 'warn';
    case 'do not ship':
      return 'bad';
    default:
      return 'unknown';
  }
}

const TONE_CLASSES: Record<BandTone, string> = {
  good: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  ok: 'border-lime-500/40 bg-lime-500/10 text-lime-300',
  warn: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  bad: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
  unknown: 'border-neutral-700 bg-neutral-800/50 text-neutral-400',
};

const TONE_TEXT: Record<BandTone, string> = {
  good: 'text-emerald-300',
  ok: 'text-lime-300',
  warn: 'text-amber-300',
  bad: 'text-rose-300',
  unknown: 'text-neutral-400',
};

export function toneClass(tone: BandTone): string {
  return TONE_CLASSES[tone];
}

export function toneText(tone: BandTone): string {
  return TONE_TEXT[tone];
}

/** Score → tone, on the same 0–100 thresholds the rubric uses for bands. */
export function scoreTone(score: number | null | undefined): BandTone {
  if (score === null || score === undefined || Number.isNaN(score)) return 'unknown';
  if (score >= 80) return 'good';
  if (score >= 60) return 'ok';
  if (score >= 40) return 'warn';
  return 'bad';
}

export function Badge({
  children,
  tone = 'unknown',
  className = '',
  title,
}: {
  children: ReactNode;
  tone?: BandTone;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${TONE_CLASSES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export function Panel({
  children,
  className = '',
  as: Tag = 'section',
}: {
  children: ReactNode;
  className?: string;
  as?: 'section' | 'div' | 'article';
}) {
  return (
    <Tag
      className={`rounded-2xl border border-neutral-800 bg-neutral-900/40 ${className}`}
    >
      {children}
    </Tag>
  );
}

export function SectionHeading({
  title,
  sub,
  right,
}: {
  title: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h2 className="text-lg font-medium tracking-tight text-neutral-100">
          {title}
        </h2>
        {sub ? <p className="mt-1 text-sm text-neutral-500">{sub}</p> : null}
      </div>
      {right}
    </div>
  );
}

/** Honest empty state. Never apologise, never fake data — just say what is missing. */
export function EmptyState({
  title,
  detail,
  className = '',
}: {
  title: string;
  detail?: string;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl border border-dashed border-neutral-800 bg-neutral-900/20 px-6 py-10 text-center ${className}`}
    >
      <p className="text-sm font-medium text-neutral-400">{title}</p>
      {detail ? (
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-neutral-600">
          {detail}
        </p>
      ) : null}
    </div>
  );
}

/** A small monospaced turn reference that can jump into the transcript. */
export function TurnRef({
  turn,
  onJump,
  className = '',
}: {
  turn: number;
  onJump?: (turn: number) => void;
  className?: string;
}) {
  const label = `turn ${turn}`;
  if (!onJump) {
    return (
      <span
        className={`font-mono text-xs text-neutral-500 ${className}`}
      >
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onJump(turn)}
      className={`rounded border border-neutral-700 px-1.5 py-0.5 font-mono text-xs text-neutral-400 transition hover:border-neutral-500 hover:text-neutral-100 ${className}`}
    >
      {label} →
    </button>
  );
}

export function formatSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return '—';
  const total = Math.max(0, Math.round(s));
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/** "already-switched" → "Already switched"; "goal_outcome" → "Goal outcome". */
export function humanise(key: string): string {
  const s = key.replace(/[_-]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}
