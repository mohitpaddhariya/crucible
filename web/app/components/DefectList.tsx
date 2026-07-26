'use client';

import type { GroundTruthBreach, Scorecard } from './types';
import { EmptyState, QUOTE_TEXT, humanise } from './ui';

export interface DefectListProps {
  /** From `dimensions.hallucination.ground_truth_audit.valid[]`. */
  defects: GroundTruthBreach[];
  personaName?: string;
  /**
   * False when the ground-truth audit never ran for this conversation. An empty
   * list then means "not checked", not "clean" — and it says so.
   */
  audited?: boolean;
  /** Clicking a turn number jumps into ConversationView. */
  onJumpToTurn?: (turn: number) => void;
  className?: string;
}

/**
 * Confirmed ground-truth breaches: things the agent said that its own brief
 * forbade. Quote large, the rule it broke underneath, turn number linking back
 * into the transcript.
 */
export default function DefectList({
  defects,
  personaName,
  audited = true,
  onJumpToTurn,
  className = '',
}: DefectListProps) {
  if (!audited) {
    return (
      <EmptyState
        className={className}
        title="No ground-truth breach audit on this conversation"
        detail="The audit only writes a record when the judge claims a breach. This is an absence of claims, not a verified all-clear — read it as 'nothing was flagged', not 'nothing was wrong'."
      />
    );
  }

  if (!defects.length) {
    return (
      <EmptyState
        className={className}
        title="No confirmed ground-truth breaches"
        detail={`Every claim ${personaName ? `made to ${personaName}` : 'the agent made'} survived the audit against the persona's ground truth.`}
      />
    );
  }

  return (
    <div className={className}>
      <div className="mb-6 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h3 className="text-2xl font-semibold tracking-tight text-rose-300">
          {defects.length} confirmed breach{defects.length === 1 ? '' : 'es'}
        </h3>
        <p className="text-sm text-neutral-500">
          {personaName ? `${personaName} — ` : ''}the agent said these things; its
          own ground truth forbade them.
        </p>
      </div>

      <ol className="space-y-5">
        {defects.map((d, i) => (
          <li
            key={`${d.turn}-${i}`}
            className="overflow-hidden rounded-2xl border border-rose-500/30 bg-rose-500/[0.05]"
          >
            <div className="px-6 py-6 sm:px-8 sm:py-7">
              <blockquote
                style={QUOTE_TEXT}
                className="text-neutral-50"
              >
                &ldquo;{d.quote}&rdquo;
              </blockquote>
            </div>

            <div className="border-t border-rose-500/20 bg-rose-950/25 px-6 py-4 sm:px-8">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-rose-400">
                {d.entry_kind ? humanise(d.entry_kind) : 'Rule broken'}
              </p>
              <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-rose-100/90">
                {d.entry}
              </p>

              <div className="mt-4">
                {onJumpToTurn ? (
                  <button
                    type="button"
                    onClick={() => onJumpToTurn(d.turn)}
                    className="rounded-lg border border-rose-500/40 px-3 py-1.5 font-mono text-xs text-rose-200 transition hover:border-rose-400 hover:bg-rose-500/10 hover:text-rose-50"
                  >
                    turn {d.turn} — see it in the transcript →
                  </button>
                ) : (
                  <span className="font-mono text-xs text-rose-300/70">
                    turn {d.turn}
                  </span>
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

/**
 * Pull the confirmed breaches out of a scorecard. Returns `audited: false` when
 * the hallucination dimension carried no ground-truth audit at all.
 */
export function defectsFromScorecard(scorecard: Scorecard | null | undefined): {
  defects: GroundTruthBreach[];
  audited: boolean;
} {
  const audit = scorecard?.dimensions?.hallucination?.ground_truth_audit;
  if (!audit) return { defects: [], audited: false };
  return { defects: audit.valid ?? [], audited: true };
}
