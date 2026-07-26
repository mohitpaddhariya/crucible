'use client';

import { useCallback, useRef, useState } from 'react';
import ConversationPlayer, { type PlayerControls } from './ConversationPlayer';
import ConversationView from './ConversationView';
import type { Conversation, Turn } from './types';

export interface ConversationPanelProps {
  runId: string;
  conversation: Conversation;
  /** Set false for a Level 0 text run — the player is then not mounted at all. */
  hasAudio?: boolean;
  agentLabel?: string;
  /** Agent turn indices with a confirmed ground-truth breach. */
  defectTurns?: number[];
  /** Set from DefectList / ScoreCard to scroll the transcript to a turn. */
  focusTurn?: number | null;
  className?: string;
}

/**
 * Convenience composition: ConversationPlayer wired to ConversationView, so the
 * playhead highlights the spoken turn and every turn gets a working play button.
 * Both components remain independently mountable — this is one line of wiring,
 * not a required container.
 */
export default function ConversationPanel({
  runId,
  conversation,
  hasAudio = true,
  agentLabel = 'Agent',
  defectTurns,
  focusTurn = null,
  className = '',
}: ConversationPanelProps) {
  const [activeTurn, setActiveTurn] = useState<number | null>(null);
  const controls = useRef<PlayerControls | null>(null);

  const handleReady = useCallback((c: PlayerControls) => {
    controls.current = c;
  }, []);

  const playTurn = useCallback((idx: number) => {
    controls.current?.playTurn(idx);
  }, []);

  const personaName = conversation.persona_name ?? conversation.persona_id;
  const turns: Turn[] = conversation.turns ?? [];

  return (
    <div className={className}>
      {hasAudio ? (
        <ConversationPlayer
          runId={runId}
          personaId={conversation.persona_id}
          personaName={personaName}
          agentLabel={agentLabel}
          turns={turns}
          onActiveTurnChange={setActiveTurn}
          onReady={handleReady}
          className="mb-10"
        />
      ) : null}

      <ConversationView
        conversation={conversation}
        activeTurn={activeTurn}
        focusTurn={focusTurn}
        onPlayTurn={hasAudio ? playTurn : undefined}
        defectTurns={defectTurns}
        agentLabel={agentLabel}
      />
    </div>
  );
}
