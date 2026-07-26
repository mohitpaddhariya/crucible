/**
 * Presentation surface for voice-spar.
 *
 *   import { PersonaTabs, ConversationView, ConversationPlayer, ScoreCard, DefectList }
 *     from '@/app/components';
 *
 * Every component takes typed props and assumes nothing about where it is
 * mounted. Only ConversationPlayer touches the network; everything else is pure
 * rendering.
 *
 * `RunPicker` is deliberately NOT exported here. It lists run ids, level badges and
 * raw scores — the internals the demo is not allowed to show — so it is off the
 * user-facing surface and out of the page bundle. The file is kept for developer use.
 */

export { default as PersonaTabs } from './PersonaTabs';
export type { PersonaTabsProps, PersonaTab } from './PersonaTabs';

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
