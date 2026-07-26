'use client';

/**
 * One row of personas, one conversation on screen.
 *
 * Two problems, one control. Four transcripts stacked on a single page was
 * unreadable AND slow — every conversation is ~15 turns of per-word diff spans,
 * so four of them is a DOM the browser rebuilds on every paint. Mounting one at
 * a time fixes the reading and the jank together.
 *
 * A persona the library defines but no run has exercised yet still gets a tab.
 * It is dimmed and unselectable, because hiding it would misrepresent the
 * library and faking a score for it would misrepresent the agent.
 */

import { useRef } from 'react';

export type PersonaTab = {
  id: string;
  name: string;
  /** False when there is no conversation to open — the tab renders inert. */
  available: boolean;
  /** Quiet line under the name: "11 turns", "not run yet", a band, … */
  note?: string;
  /** Marks the control persona, in the same sky tone ScoreCard uses. */
  isControl?: boolean;
};

export interface PersonaTabsProps {
  tabs: PersonaTab[];
  active: string | null;
  onSelect: (id: string) => void;
  /** Accessible name for the tablist. */
  label?: string;
  className?: string;
}

export default function PersonaTabs({
  tabs,
  active,
  onSelect,
  label = 'Customers',
  className = '',
}: PersonaTabsProps) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  /** ← / → move between the tabs that can actually be opened. */
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const open = tabs.filter((t) => t.available);
    if (open.length < 2) return;
    const i = open.findIndex((t) => t.id === active);
    const next =
      open[(Math.max(0, i) + (e.key === 'ArrowRight' ? 1 : open.length - 1)) % open.length];
    if (!next) return;
    e.preventDefault();
    onSelect(next.id);
    refs.current[next.id]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={`flex flex-wrap gap-2 border-b border-neutral-800/80 pb-4 ${className}`}
    >
      {tabs.map((t) => {
        const selected = t.available && t.id === active;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            role="tab"
            type="button"
            aria-selected={selected}
            aria-disabled={!t.available}
            disabled={!t.available}
            tabIndex={selected || (!active && t.available) ? 0 : -1}
            onClick={() => t.available && onSelect(t.id)}
            className={`rounded-xl border px-4 py-2.5 text-left transition ${
              selected
                ? 'border-neutral-600 bg-neutral-800/70 text-neutral-100'
                : t.available
                  ? 'border-neutral-800 bg-neutral-900/30 text-neutral-400 hover:border-neutral-700 hover:text-neutral-200'
                  : 'cursor-not-allowed border-dashed border-neutral-800/70 bg-transparent text-neutral-600'
            }`}
          >
            <span className="flex items-center gap-2 text-sm font-medium">
              {t.name}
              {t.isControl ? (
                <span
                  aria-hidden="true"
                  className={`h-1.5 w-1.5 rounded-full ${
                    t.available ? 'bg-sky-400' : 'bg-neutral-700'
                  }`}
                />
              ) : null}
            </span>
            {t.note ? (
              <span
                className={`mt-0.5 block text-xs ${
                  selected ? 'text-neutral-400' : 'text-neutral-600'
                }`}
              >
                {t.note}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
