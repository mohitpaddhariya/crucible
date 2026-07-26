/**
 * Presentation surface for voice-spar.
 *
 *   import { RunPicker, ConversationView, ConversationPlayer, ScoreCard, DefectList }
 *     from '@/app/components';
 *
 * Every component takes typed props and assumes nothing about where it is
 * mounted. Only ConversationPlayer and RunPicker's optional `useRuns` hook touch
 * the network; everything else is pure rendering.
 */

export { default as RunPicker, useRuns } from './RunPicker';
export type { RunPickerProps, UseRunsResult } from './RunPicker';

export { default as ConversationView } from './ConversationView';
export type { ConversationViewProps } from './ConversationView';

export { default as ConversationPlayer } from './ConversationPlayer';
export type {
  ConversationPlayerProps,
  PlayerControls,
} from './ConversationPlayer';

export { default as ScoreCard } from './ScoreCard';
export type { ScoreCardProps } from './ScoreCard';

export { default as DefectList, defectsFromScorecard } from './DefectList';
export type { DefectListProps } from './DefectList';

export { default as ConversationPanel } from './ConversationPanel';
export type { ConversationPanelProps } from './ConversationPanel';

export { wordDiff, skeleton } from './diff';
export type { WordDiff, DiffOp } from './diff';

export {
  Badge,
  EmptyState,
  Panel,
  SectionHeading,
  TurnRef,
  bandTone,
  scoreTone,
  formatSeconds,
  formatDate,
  humanise,
  TRANSCRIPT_TEXT,
  QUOTE_TEXT,
} from './ui';

export type * from './types';
