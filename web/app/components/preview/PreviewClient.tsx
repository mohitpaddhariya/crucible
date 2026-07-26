'use client';

import { useMemo, useState } from 'react';
import RunPicker from '../RunPicker';
import ConversationPanel from '../ConversationPanel';
import ScoreCard from '../ScoreCard';
import DefectList, { defectsFromScorecard } from '../DefectList';
import { EmptyState, SectionHeading } from '../ui';
import type {
  ControlGate,
  Conversation,
  RunSummary,
  Scorecard,
} from '../types';

export interface RunBundle {
  summary: RunSummary;
  conversations: Conversation[];
  scorecards: Scorecard[];
  controlGate: ControlGate | null;
}

/**
 * Dev harness only. Mounts every component against real run data so the output
 * can be inspected. The real page composes these itself.
 */
export default function PreviewClient({
  bundles,
  initialRunId = null,
  initialPersonaId = null,
}: {
  bundles: RunBundle[];
  /** `?run=` override, so the harness can be driven without clicking. */
  initialRunId?: string | null;
  /** `?persona=` override. */
  initialPersonaId?: string | null;
}) {
  const [runId, setRunId] = useState(
    initialRunId ?? bundles[0]?.summary.id ?? ''
  );
  const bundle = useMemo(
    () => bundles.find((b) => b.summary.id === runId) ?? bundles[0] ?? null,
    [bundles, runId]
  );

  const [personaId, setPersonaId] = useState<string | null>(initialPersonaId);
  const [focusTurn, setFocusTurn] = useState<number | null>(null);

  const conversation = useMemo(() => {
    if (!bundle) return null;
    return (
      bundle.conversations.find((c) => c.persona_id === personaId) ??
      bundle.conversations[0] ??
      null
    );
  }, [bundle, personaId]);

  const scorecard = useMemo(() => {
    if (!bundle || !conversation) return null;
    return (
      bundle.scorecards.find(
        (s) => s.persona_id === conversation.persona_id
      ) ?? null
    );
  }, [bundle, conversation]);

  const { defects, audited } = defectsFromScorecard(scorecard);
  const defectTurns = defects.map((d) => d.turn);

  const personaSummary = bundle?.summary.personas.find(
    (p) => p.id === conversation?.persona_id
  );

  if (!bundles.length) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-20">
        <EmptyState
          title="No runs with conversations found"
          detail="Nothing under runs/ has a conversations/ directory yet."
        />
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-14 sm:px-8">
      <p className="mb-10 rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-4 py-2.5 text-xs text-amber-200/80">
        Component preview harness — reads runs/ from disk. Not the product page.
      </p>

      <SectionHeading
        title="RunPicker"
        sub="Pick a real run. Level, date, persona count, and every persona's score."
      />
      <RunPicker
        runs={bundles.map((b) => b.summary)}
        selectedRunId={bundle?.summary.id ?? null}
        onSelect={(id) => {
          setRunId(id);
          setPersonaId(null);
          setFocusTurn(null);
        }}
      />

      {bundle && conversation ? (
        <>
          <div className="mt-20">
            <SectionHeading
              title="ConversationPlayer + ConversationView"
              sub="Said vs heard, word-level. Player highlights the spoken turn."
              right={
                <div className="flex flex-wrap gap-2">
                  {bundle.conversations.map((c) => (
                    <button
                      key={c.persona_id}
                      type="button"
                      onClick={() => {
                        setPersonaId(c.persona_id);
                        setFocusTurn(null);
                      }}
                      className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                        c.persona_id === conversation.persona_id
                          ? 'border-neutral-600 bg-neutral-800 text-neutral-100'
                          : 'border-neutral-800 text-neutral-500 hover:text-neutral-300'
                      }`}
                    >
                      {c.persona_name ?? c.persona_id}
                    </button>
                  ))}
                </div>
              }
            />
            <ConversationPanel
              runId={bundle.summary.id}
              conversation={conversation}
              hasAudio={bundle.summary.hasAudio}
              agentLabel="Tara"
              defectTurns={defectTurns}
              focusTurn={focusTurn}
            />
          </div>

          <div className="mt-20">
            <SectionHeading title="ScoreCard" />
            <ScoreCard
              scorecard={scorecard}
              personaName={personaSummary?.name}
              isControl={personaSummary?.isControl ?? false}
              controlGate={bundle.controlGate}
              onJumpToTurn={setFocusTurn}
            />
          </div>

          <div className="mt-20 pb-24">
            <SectionHeading title="DefectList" />
            <DefectList
              defects={defects}
              audited={audited}
              personaName={personaSummary?.name}
              onJumpToTurn={setFocusTurn}
            />
          </div>
        </>
      ) : null}
    </main>
  );
}
