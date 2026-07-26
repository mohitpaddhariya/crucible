'use client';

import { useMemo, useState } from 'react';
import type { ControlGate, Dimension, Scorecard } from './types';
import {
  Badge,
  EmptyState,
  TRANSCRIPT_TEXT,
  TurnRef,
  bandTone,
  humanise,
  scoreTone,
  toneText,
} from './ui';

/** Rubric labels, so the UI does not show raw snake_case keys. */
const DIMENSION_LABELS: Record<string, string> = {
  goal_outcome: 'Goal outcome',
  hallucination: 'Hallucination',
  instruction_adherence: 'Instruction adherence',
  language_handling: 'Language handling',
  objection_handling: 'Objection handling',
  escalation_safety: 'Escalation & safety',
  conversation_flow: 'Conversation flow',
};

export interface ScoreCardProps {
  scorecard: Scorecard | null | undefined;
  personaName?: string;
  /** Marks this card as the control persona. */
  isControl?: boolean;
  /** Run-level control gate from synthesis.json — stated plainly, pass or fail. */
  controlGate?: ControlGate | null;
  /** Turn links in the evidence quotes call this. */
  onJumpToTurn?: (turn: number) => void;
  className?: string;
}

/**
 * One persona's verdict: weighted score, band, coverage, and all seven rubric
 * dimensions expandable to their reasoning and verbatim evidence.
 * Failing dimensions start open — the weakness is the point of the screen.
 */
export default function ScoreCard({
  scorecard,
  personaName,
  isControl = false,
  controlGate = null,
  onJumpToTurn,
  className = '',
}: ScoreCardProps) {
  const dimensions = useMemo(() => {
    const entries = Object.entries(scorecard?.dimensions ?? {});
    entries.sort((a, b) => (b[1].weight ?? 0) - (a[1].weight ?? 0));
    return entries;
  }, [scorecard]);

  const [open, setOpen] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    for (const [key, dim] of Object.entries(scorecard?.dimensions ?? {})) {
      if (dim.verdict === 'fail' || dim.scored === false) initial.add(key);
    }
    return initial;
  });

  if (!scorecard) {
    return (
      <EmptyState
        className={className}
        title={`No scorecard for ${personaName ?? 'this persona'}`}
        detail="The judge did not produce a verdict for this conversation, so there is no score to show."
      />
    );
  }

  const score = scorecard.weighted_score;
  const tone = score === null ? bandTone(scorecard.band) : scoreTone(score);
  const coverage = scorecard.coverage?.scored_weight_pct ?? null;
  const unscored = scorecard.coverage?.unscored_dimensions ?? [];
  const name = personaName ?? scorecard.persona_id;

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <article
      className={`rounded-2xl border border-neutral-800 bg-neutral-900/40 ${className}`}
    >
      {/* ------------------------------------------------------- headline */}
      <header className="border-b border-neutral-800 px-6 py-7 sm:px-8">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-lg font-medium tracking-tight text-neutral-100">
                {name}
              </h3>
              {isControl ? (
                <span className="rounded border border-sky-500/40 bg-sky-500/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-widest text-sky-300">
                  Control persona
                </span>
              ) : null}
            </div>
            <p className="mt-1 font-mono text-xs text-neutral-600">
              {scorecard.persona_id}
            </p>
          </div>

          <div className="text-right">
            <div className={`text-6xl font-semibold tabular-nums leading-none ${toneText(tone)}`}>
              {score === null ? '—' : score.toFixed(1)}
              <span className="ml-1 text-xl text-neutral-700">/100</span>
            </div>
            <div className="mt-3 flex justify-end">
              <Badge tone={tone}>{scorecard.band ?? 'not judged'}</Badge>
            </div>
          </div>
        </div>

        {/* --------------------------------------------------- coverage */}
        <div className="mt-6 flex flex-wrap items-center gap-x-8 gap-y-3">
          <div className="min-w-[14rem] flex-1">
            <div className="flex items-baseline justify-between text-xs">
              <span className="uppercase tracking-wider text-neutral-500">
                Rubric coverage
              </span>
              <span
                className={`font-semibold tabular-nums ${
                  coverage !== null && coverage < 100
                    ? 'text-amber-300'
                    : 'text-neutral-300'
                }`}
              >
                {coverage === null ? 'unknown' : `${coverage.toFixed(0)}%`}
              </span>
            </div>
            <div className="mt-2 h-1 w-full overflow-hidden rounded bg-neutral-800">
              <div
                className={`h-full ${
                  coverage !== null && coverage < 100 ? 'bg-amber-400' : 'bg-neutral-400'
                }`}
                style={{ width: `${Math.max(0, Math.min(100, coverage ?? 0))}%` }}
              />
            </div>
          </div>

          {scorecard.conversation?.end_reason ? (
            <div className="text-xs">
              <span className="uppercase tracking-wider text-neutral-500">
                Ended
              </span>
              <p className="mt-1 font-mono text-neutral-300">
                {scorecard.conversation.end_reason}
              </p>
            </div>
          ) : null}
        </div>

        {coverage !== null && coverage < 100 ? (
          <p className="mt-3 text-xs leading-relaxed text-amber-200/70">
            The score is renormalised over the {coverage.toFixed(0)}% of rubric
            weight that could be scored
            {unscored.length
              ? ` — ${unscored.map((k) => DIMENSION_LABELS[k] ?? humanise(k)).join(', ')} ${
                  unscored.length === 1 ? 'was' : 'were'
                } not scored`
              : ''}
            . Unscored weight skews toward failures, so treat this headline as
            optimistic.
          </p>
        ) : null}

        <ControlGateLine gate={controlGate} isControl={isControl} />
      </header>

      {/* ----------------------------------------------------- dimensions */}
      <div className="divide-y divide-neutral-800/70">
        {dimensions.length === 0 ? (
          <p className="px-6 py-8 text-sm text-neutral-600 sm:px-8">
            This scorecard contains no dimensions.
          </p>
        ) : (
          dimensions.map(([key, dim]) => (
            <DimensionRow
              key={key}
              dimensionKey={key}
              dim={dim}
              open={open.has(key)}
              onToggle={() => toggle(key)}
              onJumpToTurn={onJumpToTurn}
            />
          ))
        )}
      </div>

      {scorecard.overall_note ? (
        <footer className="border-t border-neutral-800 px-6 py-5 sm:px-8">
          <p style={TRANSCRIPT_TEXT} className="text-sm text-neutral-400">
            {scorecard.overall_note}
          </p>
        </footer>
      ) : null}
    </article>
  );
}

/* ------------------------------------------------------------ control gate */

function ControlGateLine({
  gate,
  isControl,
}: {
  gate: ControlGate | null;
  isControl: boolean;
}) {
  if (!gate) {
    return (
      <p className="mt-5 border-t border-neutral-800/70 pt-4 text-xs text-neutral-600">
        Control gate: not evaluated for this run — no synthesis was produced.
      </p>
    );
  }

  const passed = (gate.status ?? '').toLowerCase() === 'pass';

  return (
    <div
      className={`mt-5 rounded-xl border px-4 py-3 ${
        passed
          ? 'border-emerald-500/30 bg-emerald-500/[0.06]'
          : 'border-rose-500/40 bg-rose-500/[0.07]'
      }`}
    >
      <p className="flex flex-wrap items-center gap-3 text-sm">
        <span
          className={`text-[11px] font-bold uppercase tracking-widest ${
            passed ? 'text-emerald-300' : 'text-rose-300'
          }`}
        >
          Control gate {passed ? 'passed' : 'failed'}
        </span>
        <span className="text-neutral-400">
          {passed
            ? 'The easy customer was handled correctly, so the failures on the hard personas are real, not a broken harness.'
            : 'The control persona itself failed — every other score on this run is suspect until that is fixed.'}
        </span>
      </p>
      {gate.summary ? (
        <p className="mt-2 text-xs leading-relaxed text-neutral-500">
          {gate.summary}
        </p>
      ) : null}
      {gate.reasons?.length ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-rose-200/80">
          {gate.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : null}
      {isControl ? (
        <p className="mt-2 text-xs text-neutral-600">
          This persona is the control the gate was computed from.
        </p>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------- dimension */

function DimensionRow({
  dimensionKey,
  dim,
  open,
  onToggle,
  onJumpToTurn,
}: {
  dimensionKey: string;
  dim: Dimension;
  open: boolean;
  onToggle: () => void;
  onJumpToTurn?: (turn: number) => void;
}) {
  const label = DIMENSION_LABELS[dimensionKey] ?? humanise(dimensionKey);
  const scored = dim.scored !== false && dim.score !== null && dim.score !== undefined;
  const pct = scored ? Math.round((dim.score as number) * 100) : null;
  const verdict = (dim.verdict ?? '').toLowerCase();
  const tone = !scored
    ? 'unknown'
    : verdict === 'fail'
      ? 'bad'
      : verdict === 'partial'
        ? 'warn'
        : 'good';
  const evidence = dim.evidence ?? [];

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-5 px-6 py-4 text-left transition hover:bg-neutral-800/30 sm:px-8"
      >
        <span
          className={`w-5 shrink-0 text-center text-xs text-neutral-600 transition ${open ? 'rotate-90' : ''}`}
          aria-hidden="true"
        >
          ▸
        </span>

        <span className="w-48 shrink-0 text-sm font-medium text-neutral-200">
          {label}
        </span>

        <span className="hidden w-12 shrink-0 text-right font-mono text-xs text-neutral-600 sm:block">
          {(dim.weight ?? 0).toFixed(0)}%
        </span>

        <span className="h-1.5 min-w-[4rem] flex-1 overflow-hidden rounded bg-neutral-800">
          {scored ? (
            <span
              className={`block h-full ${
                tone === 'bad'
                  ? 'bg-rose-500'
                  : tone === 'warn'
                    ? 'bg-amber-400'
                    : 'bg-emerald-500'
              }`}
              style={{ width: `${pct}%` }}
            />
          ) : (
            <span className="unscored-fill block h-full w-full" />
          )}
        </span>

        <span
          className={`w-16 shrink-0 text-right text-sm font-semibold tabular-nums ${
            scored ? toneText(tone) : 'text-neutral-600'
          }`}
        >
          {scored ? `${pct}%` : 'n/a'}
        </span>

        <Badge tone={tone} className="hidden shrink-0 sm:inline-flex">
          {scored ? (dim.verdict ?? 'scored') : 'unscored'}
        </Badge>
      </button>

      {open ? (
        <div className="space-y-5 px-6 pb-7 pl-[3.25rem] sm:px-8 sm:pl-[4.5rem]">
          {!scored ? (
            <p className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-4 py-3 text-sm text-amber-200/90">
              Not scored
              {dim.unscored_reason ? ` — ${dim.unscored_reason}` : '.'} This
              dimension&rsquo;s weight was removed from the denominator rather
              than counted as a zero.
            </p>
          ) : null}

          {dim.reasoning ? (
            <p
              style={{ ...TRANSCRIPT_TEXT, fontSize: '0.9375rem' }}
              className="max-w-3xl text-neutral-300"
            >
              {dim.reasoning}
            </p>
          ) : (
            <p className="text-sm text-neutral-600">
              The judge recorded no reasoning for this dimension.
            </p>
          )}

          {evidence.length ? (
            <div>
              <p className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-neutral-600">
                Evidence — verbatim from the transcript
              </p>
              <ul className="space-y-3">
                {evidence.map((ev, i) => (
                  <li
                    key={`${ev.turn}-${i}`}
                    className="rounded-xl border-l-2 border-neutral-700 bg-neutral-950/40 py-3 pl-5 pr-4"
                  >
                    <p
                      style={{ ...TRANSCRIPT_TEXT, fontSize: '1rem' }}
                      className="text-neutral-200"
                    >
                      &ldquo;{ev.quote}&rdquo;
                    </p>
                    <div className="mt-2">
                      <TurnRef turn={ev.turn} onJump={onJumpToTurn} />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-neutral-600">
              No evidence quotes survived the verbatim audit for this dimension.
            </p>
          )}

          {dim.ground_truth_audit?.valid?.length ? (
            <p className="text-xs text-rose-300/80">
              {dim.ground_truth_audit.valid.length} confirmed ground-truth
              breach
              {dim.ground_truth_audit.valid.length === 1 ? '' : 'es'} — listed in
              full below the scorecard.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
