'use client';

import { useEffect, useMemo, useRef } from 'react';
import type { Conversation, Turn } from './types';
import { wordDiff, type DiffOp, type WordDiff } from './diff';
import {
  Badge,
  EmptyState,
  TRANSCRIPT_TEXT,
  formatSeconds,
} from './ui';

export interface ConversationViewProps {
  conversation: Conversation | null | undefined;
  /** Turn index currently being spoken by ConversationPlayer. Scrolls into view. */
  activeTurn?: number | null;
  /** Turn to scroll to and flash — used by DefectList / ScoreCard evidence links. */
  focusTurn?: number | null;
  /** Renders a play button on each turn. Wire to ConversationPlayer's `playTurn`. */
  onPlayTurn?: (turnIdx: number) => void;
  /** Agent turn indices with a confirmed ground-truth breach — marked in the margin. */
  defectTurns?: number[];
  /** Label for the right-hand column. Defaults to the conversation's persona name. */
  customerLabel?: string;
  agentLabel?: string;
  className?: string;
}

/**
 * The transcript. Agent on the left, customer on the right.
 *
 * For an audio run every persona turn carries three facts, and the third is the
 * whole point of the product:
 *   1. what the persona SAID          — turn.text
 *   2. what the agent's ASR HEARD     — turn.meta.tara_heard.text
 *   3. whether the ASR cut off early  — turn.meta.tara_heard.truncation_suspect
 *
 * When (1) and (2) differ, both are shown, adjacent, always open, with a
 * word-level diff painting exactly which words never reached the agent. There is
 * no toggle and no expander: the failure is the finding.
 *
 * A Level 0 (text) conversation has none of those keys. It renders as a plain
 * two-column transcript — no empty "heard" slots, no apology.
 */
export default function ConversationView({
  conversation,
  activeTurn = null,
  focusTurn = null,
  onPlayTurn,
  defectTurns,
  customerLabel,
  agentLabel = 'Agent',
  className = '',
}: ConversationViewProps) {
  const turnRefs = useRef(new Map<number, HTMLDivElement | null>());

  const defectSet = useMemo(
    () => new Set(defectTurns ?? []),
    [defectTurns]
  );

  const scrollTo = (idx: number | null) => {
    if (idx === null || idx === undefined) return;
    const el = turnRefs.current.get(idx);
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  };

  useEffect(() => {
    scrollTo(activeTurn);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTurn]);

  useEffect(() => {
    scrollTo(focusTurn);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusTurn]);

  if (!conversation || !conversation.turns?.length) {
    return (
      <EmptyState
        className={className}
        title="No transcript for this conversation"
        detail="The run wrote no turns for this persona. Nothing is being hidden — there is simply nothing here."
      />
    );
  }

  const label =
    customerLabel ?? conversation.persona_name ?? conversation.persona_id;

  /** Does *any* persona turn carry ASR data? Decides whether the run is audio. */
  const hasAsr = conversation.turns.some(
    (t) => t.speaker === 'persona' && t.meta?.tara_heard?.text
  );

  const asrStats = hasAsr ? summariseAsr(conversation.turns) : null;

  return (
    <div className={className}>
      <TranscriptHeader
        conversation={conversation}
        agentLabel={agentLabel}
        customerLabel={label}
        asrStats={asrStats}
      />

      <div className="mt-8 space-y-8">
        {conversation.turns.map((turn) => (
          <div
            key={turn.idx}
            ref={(el) => {
              turnRefs.current.set(turn.idx, el);
            }}
          >
            {turn.speaker === 'agent' ? (
              <AgentTurn
                turn={turn}
                label={agentLabel}
                active={turn.idx === activeTurn}
                focused={turn.idx === focusTurn}
                isDefect={defectSet.has(turn.idx)}
                onPlayTurn={onPlayTurn}
              />
            ) : (
              <CustomerTurn
                turn={turn}
                label={label}
                active={turn.idx === activeTurn}
                focused={turn.idx === focusTurn}
                onPlayTurn={onPlayTurn}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- the header */

interface AsrStats {
  turnsWithAsr: number;
  turnsChanged: number;
  turnsTruncated: number;
  wordsSaid: number;
  wordsHeard: number;
}

function summariseAsr(turns: Turn[]): AsrStats {
  const stats: AsrStats = {
    turnsWithAsr: 0,
    turnsChanged: 0,
    turnsTruncated: 0,
    wordsSaid: 0,
    wordsHeard: 0,
  };
  for (const t of turns) {
    const heard = t.meta?.tara_heard;
    if (t.speaker !== 'persona' || !heard?.text) continue;
    stats.turnsWithAsr += 1;
    if (heard.truncation_suspect) stats.turnsTruncated += 1;
    const d = wordDiff(t.text, heard.text);
    if (!d.identical) stats.turnsChanged += 1;
    stats.wordsSaid += d.saidCount;
    stats.wordsHeard += d.matchedCount;
  }
  return stats;
}

function TranscriptHeader({
  conversation,
  agentLabel,
  customerLabel,
  asrStats,
}: {
  conversation: Conversation;
  agentLabel: string;
  customerLabel: string;
  asrStats: AsrStats | null;
}) {
  const endReason =
    typeof conversation.end_reason === 'string'
      ? conversation.end_reason
      : conversation.end_reason?.code ?? null;

  const total =
    conversation.turn_count?.total ?? conversation.turns.length;

  const captured =
    asrStats && asrStats.wordsSaid > 0
      ? asrStats.wordsHeard / asrStats.wordsSaid
      : null;

  return (
    <header className="border-b border-neutral-800 pb-6">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-neutral-500">
        <span className="text-neutral-300">
          <span className="text-sky-400">{agentLabel}</span>
          <span className="mx-2 text-neutral-700">vs</span>
          <span className="text-emerald-300">{customerLabel}</span>
        </span>
        <span className="tabular-nums">{total} turns</span>
        <span className="tabular-nums">
          {formatSeconds(conversation.duration_s)}
        </span>
        {endReason ? (
          <span className="font-mono text-xs text-neutral-500">
            {endReason}
          </span>
        ) : null}
      </div>

      {asrStats ? (
        <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3">
          <p className="text-sm text-neutral-400">
            The agent&rsquo;s speech recognition captured{' '}
            <strong
              className={`tabular-nums ${
                captured !== null && captured < 0.8
                  ? 'text-rose-300'
                  : 'text-neutral-200'
              }`}
            >
              {captured === null ? '—' : `${Math.round(captured * 100)}%`}
            </strong>{' '}
            of the words this customer spoke.
          </p>
          {asrStats.turnsChanged > 0 ? (
            <Badge tone="bad">
              {asrStats.turnsChanged} of {asrStats.turnsWithAsr} turns misheard
            </Badge>
          ) : (
            <Badge tone="good">every turn heard verbatim</Badge>
          )}
          {asrStats.turnsTruncated > 0 ? (
            <Badge tone="warn">
              {asrStats.turnsTruncated} truncated mid-sentence
            </Badge>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}

/* -------------------------------------------------------------- the turns */

function TurnChrome({
  side,
  label,
  turn,
  onPlayTurn,
  extra,
}: {
  side: 'left' | 'right';
  label: string;
  turn: Turn;
  onPlayTurn?: (idx: number) => void;
  extra?: React.ReactNode;
}) {
  return (
    <div
      className={`mb-2 flex items-center gap-3 text-[11px] uppercase tracking-widest ${
        side === 'right' ? 'justify-end' : ''
      }`}
    >
      {onPlayTurn ? (
        <button
          type="button"
          onClick={() => onPlayTurn(turn.idx)}
          title={`Play turn ${turn.idx}`}
          aria-label={`Play turn ${turn.idx}`}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-neutral-700 text-neutral-400 transition hover:border-emerald-400 hover:text-emerald-300"
        >
          <PlayGlyph className="h-2.5 w-2.5" />
        </button>
      ) : null}
      <span
        className={
          side === 'left' ? 'font-semibold text-sky-400' : 'font-semibold text-emerald-400'
        }
      >
        {label}
      </span>
      <span className="font-mono text-neutral-600 normal-case tracking-normal">
        turn {turn.idx}
      </span>
      {extra}
    </div>
  );
}

function AgentTurn({
  turn,
  label,
  active,
  focused,
  isDefect,
  onPlayTurn,
}: {
  turn: Turn;
  label: string;
  active: boolean;
  focused: boolean;
  isDefect: boolean;
  onPlayTurn?: (idx: number) => void;
}) {
  return (
    <div className="flex">
      <div className="w-full max-w-[86%] md:max-w-[74%]">
        <TurnChrome
          side="left"
          label={label}
          turn={turn}
          onPlayTurn={onPlayTurn}
          extra={
            isDefect ? (
              <Badge tone="bad" className="normal-case">
                ground-truth breach
              </Badge>
            ) : null
          }
        />
        <div
          className={`rounded-2xl rounded-tl-sm border px-5 py-4 transition ${
            isDefect
              ? 'border-rose-500/40 bg-rose-500/[0.06]'
              : 'border-neutral-800 bg-neutral-900/50'
          } ${active ? 'ring-2 ring-emerald-400/70' : ''} ${
            focused ? 'ring-2 ring-amber-400/70' : ''
          }`}
        >
          <p style={TRANSCRIPT_TEXT} className="text-neutral-200">
            {turn.text}
          </p>
        </div>
      </div>
    </div>
  );
}

function CustomerTurn({
  turn,
  label,
  active,
  focused,
  onPlayTurn,
}: {
  turn: Turn;
  label: string;
  active: boolean;
  focused: boolean;
  onPlayTurn?: (idx: number) => void;
}) {
  const heard = turn.meta?.tara_heard ?? null;
  const heardText = heard?.text ?? '';
  const hasHeard = heard !== null && heardText.length > 0;
  const diff = hasHeard ? wordDiff(turn.text, heardText) : null;
  const misheard = !!diff && !diff.identical;
  const truncated = heard?.truncation_suspect === true;

  return (
    <div className="flex justify-end">
      <div className="w-full max-w-[86%] md:max-w-[74%]">
        <TurnChrome
          side="right"
          label={label}
          turn={turn}
          onPlayTurn={onPlayTurn}
          extra={
            truncated ? (
              <Badge tone="bad" className="normal-case">
                ASR truncated
              </Badge>
            ) : null
          }
        />

        <div
          className={`overflow-hidden rounded-2xl rounded-tr-sm border transition ${
            misheard
              ? 'border-rose-500/30 bg-rose-500/[0.04]'
              : 'border-emerald-500/25 bg-emerald-500/[0.05]'
          } ${active ? 'ring-2 ring-emerald-400/70' : ''} ${
            focused ? 'ring-2 ring-amber-400/70' : ''
          }`}
        >
          {/* ---------------------------------------------------- 1. SAID */}
          <div className="px-5 py-4">
            {hasHeard ? (
              <SlotLabel tone="said">Said</SlotLabel>
            ) : null}
            <p style={TRANSCRIPT_TEXT} className="text-neutral-100">
              {diff ? renderSaid(diff) : turn.text}
            </p>
          </div>

          {/* ------------------------------------- 2 & 3. HEARD BY THE AGENT */}
          {hasHeard && diff ? (
            <div
              className={`border-t px-5 py-4 ${
                misheard
                  ? 'border-rose-500/25 bg-rose-950/25'
                  : 'border-emerald-500/20 bg-emerald-950/15'
              }`}
            >
              <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                <SlotLabel tone={misheard ? 'lost' : 'heard'}>
                  Agent&rsquo;s ASR heard
                </SlotLabel>
                {misheard ? (
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-rose-300 tabular-nums">
                    {Math.round(diff.captured * 100)}% captured ·{' '}
                    {diff.droppedCount} word
                    {diff.droppedCount === 1 ? '' : 's'} never reached it
                  </span>
                ) : (
                  <span className="text-[11px] uppercase tracking-wider text-emerald-400/80">
                    word-for-word
                  </span>
                )}
              </div>

              <p style={TRANSCRIPT_TEXT} className="text-neutral-100">
                {renderHeard(diff)}
              </p>

              {diff.hasUndecodableBytes ? (
                <p className="mt-3 text-xs text-rose-300/90">
                  The transcript contains <span className="font-mono">U+FFFD</span> —
                  bytes the recogniser could not decode at all.
                </p>
              ) : null}

              {truncated ? (
                <p className="mt-3 text-xs leading-relaxed text-rose-200/80">
                  <strong className="font-semibold text-rose-300">
                    truncation_suspect
                  </strong>{' '}
                  — the recogniser closed this utterance while the customer was
                  still speaking. Everything after the cut was never available to
                  the agent when it composed its reply.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SlotLabel({
  children,
  tone,
}: {
  children: React.ReactNode;
  tone: 'said' | 'heard' | 'lost';
}) {
  const cls =
    tone === 'said'
      ? 'text-emerald-400/80'
      : tone === 'heard'
        ? 'text-emerald-400/80'
        : 'text-rose-400';
  return (
    <span
      className={`mb-1.5 block text-[11px] font-semibold uppercase tracking-widest ${cls}`}
    >
      {children}
    </span>
  );
}

/* ------------------------------------------------------- diff rendering */

/**
 * The SAID line. Words the ASR never produced are painted red and struck
 * through — that is literally what the agent did not get.
 */
function renderSaid(diff: WordDiff) {
  return diff.ops.map((op, i) => {
    if (op.type === 'add') return null;
    if (op.type === 'equal') {
      return <span key={i}>{joinWords(op.said)} </span>;
    }
    return (
      <span key={i}>
        <span
          title="the agent's speech recognition never produced these words"
          className="rounded-[3px] bg-rose-500/20 px-1 text-rose-200 decoration-rose-400/70 decoration-2 [text-decoration-line:line-through]"
        >
          {joinWords((op as Extract<DiffOp, { type: 'drop' }>).said)}
        </span>{' '}
      </span>
    );
  });
}

/**
 * The HEARD line. Words the customer never said are painted amber — the
 * recogniser invented or garbled them.
 */
function renderHeard(diff: WordDiff) {
  return diff.ops.map((op, i) => {
    if (op.type === 'drop') {
      // Show the hole where words went missing, so the two lines stay readable
      // as a pair rather than silently closing up.
      return (
        <span
          key={i}
          title={`${op.said.length} word${op.said.length === 1 ? '' : 's'} missing here`}
          className="mx-1 inline-block translate-y-[-1px] rounded-[3px] border border-dashed border-rose-500/50 px-1.5 text-[11px] font-semibold uppercase tracking-wider text-rose-400"
        >
          {op.said.length} word{op.said.length === 1 ? '' : 's'} lost
        </span>
      );
    }
    if (op.type === 'equal') {
      return <span key={i}>{joinWords(op.heard)} </span>;
    }
    return (
      <span key={i}>
        <span
          title="the customer never said this — the recogniser produced it"
          className="rounded-[3px] bg-amber-500/20 px-1 text-amber-200 decoration-amber-400/60 decoration-dotted decoration-2 underline underline-offset-4"
        >
          {joinWords(op.heard)}
        </span>{' '}
      </span>
    );
  });
}

function joinWords(words: string[]): string {
  return words.join(' ');
}

function PlayGlyph({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 8 10" className={className} aria-hidden="true">
      <path d="M0 0 L8 5 L0 10 Z" fill="currentColor" />
    </svg>
  );
}
