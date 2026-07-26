'use client';

/**
 * The demo shell.
 *
 * Four steps, one job each: prove the target is real, show who calls it, show the
 * conversations that actually happened, then show what was found.
 *
 * Everything on screen comes from `/api/**`, which reads `runs/` off disk. There is no
 * fixture, no seed, no placeholder — if a field is missing the UI says so rather than
 * inventing a plausible value. That rule is the whole point: this is a tool for telling
 * people uncomfortable things about their agent, and it has no standing to do that if any
 * part of it is decorative.
 *
 * Two rules this file keeps that the components cannot keep for it:
 *
 *  1. NOTHING INTERNAL REACHES THE SCREEN. No run ids, no level numbers, no
 *     `end_reason` codes, no persona slugs. Where a technical value carries real
 *     meaning it is translated once, here, at the seam (see `END_REASON`).
 *     The run is chosen silently — a viewer never learns that "runs" are a concept.
 *
 *  2. ONE CONVERSATION ON SCREEN AT A TIME. Four transcripts × ~15 turns × per-word
 *     diff spans is a DOM large enough to stutter; it was also simply unreadable.
 *     PersonaTabs mounts exactly one.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ConversationPanel,
  ScoreCard,
  DefectList,
  defectsFromScorecard,
  Badge,
  EmptyState,
  formatDate,
  humanise,
} from './components';
import PersonaTabs, { type PersonaTab } from './components/PersonaTabs';

type Step = 0 | 1 | 2 | 3;

const STEPS: { n: Step; label: string; hint: string }[] = [
  { n: 0, label: 'Connect', hint: 'the agent under test' },
  { n: 1, label: 'Personas', hint: 'who calls it' },
  { n: 2, label: 'Conversations', hint: 'what was actually said' },
  { n: 3, label: 'Report', hint: 'what broke' },
];

/**
 * The persona library, as a human reads it. Ids and display names come from
 * `personas/*.yaml`; the one-liners are that file's `identity.who` and the comment
 * beside `stresses`, condensed.
 *
 * This is COPY, not data. It never carries a score, a turn or an outcome — every one
 * of those comes from the API or is absent. A persona the current run did not
 * exercise shows exactly this and nothing more.
 */
const PERSONA_LIBRARY: {
  id: string;
  name: string;
  who: string;
  asks: string;
  isControl?: boolean;
}[] = [
  {
    id: 'price-haggler',
    name: 'Price Haggler',
    who: 'Kunal, 21, engineering student in Pune. Cancelled his plan, wants it back for the cricket, and has decided he is not paying full price.',
    asks: 'Does the agent hold the discount it is allowed to give?',
  },
  {
    id: 'happy-path',
    name: 'Happy Path',
    who: 'Divya, 29, software engineer in Bengaluru. Her bank reissued her card and the payment quietly lapsed. Glad to be reminded.',
    asks: 'The easy case. If the agent cannot do this one, no other result means anything.',
    isControl: true,
  },
  {
    id: 'already-switched',
    name: 'Already Switched',
    who: 'Vikram, 34, marketing manager in Gurgaon. Already pays for a competitor his family watches every evening. Polite, and quietly hard to move.',
    asks: 'Does the agent invent plans or prices to compete?',
  },
  {
    id: 'angry-churner',
    name: 'Angry Churner',
    who: 'Mahesh, 45, shop owner in Indore. The stream buffered through an entire India match he had invited people over to watch, and nobody made it right.',
    asks: 'Does the agent calm him down, and does it know when to hand over to a human?',
  },
];

/**
 * `end_reason.code` → the sentence a person would say.
 *
 * These codes are the runner's vocabulary, not the viewer's. An unrecognised code
 * renders as NOTHING rather than leaking a new slug onto the screen — a missing line
 * is a smaller lie than `seconds_over`.
 */
const END_REASON: Record<string, string> = {
  seconds_over: 'ended on the time limit',
  goal_reached: 'the customer got what they called about',
  persona_walked_away: 'the customer ended the call',
  target_disconnected: 'the agent dropped the call',
  agent_offers_human_handoff: 'the agent handed over to a human',
};

function endReasonText(code: unknown): string | null {
  return typeof code === 'string' ? (END_REASON[code] ?? null) : null;
}

/**
 * THE SEAM between two conventions, and the reason it exists.
 *
 * The API layer camelCases everything it serves. The presentation components were written
 * against the RAW ARTIFACT shape, which is snake_case — that is the project's real contract
 * (docs/INTERFACES.md), and it is what the components were verified against by reading
 * `runs/` straight off disk. Both choices are defensible; they were just made by different
 * hands at the same time.
 *
 * The mismatch was invisible until the two halves were wired together, because `turn.meta`
 * is passed through RAW — so said-vs-heard rendered perfectly while every top-level field
 * silently resolved to undefined. The visible symptom was an audio URL reading
 * `/api/audio/<run>/undefined/full`.
 *
 * Reconciling it here, once, beats editing four component files to chase a naming choice.
 * `dimensions` also changes SHAPE, not just case: the API sorts it into an array by weight,
 * ScoreCard does Object.entries over a map.
 *
 * It is also the only place that can keep rule 1 without editing a component: the
 * translated `end_reason` and the blanked `persona_id` below are what stop
 * ConversationView and ScoreCard printing a raw code and a raw slug.
 */
function toArtifactShape(p: any) {
  const c = p?.conversation ?? null;
  const s = p?.scorecard ?? null;

  const ended = endReasonText(c?.endReason?.code);

  const conversation = c && {
    ...c,
    persona_id: c.personaId ?? p.id,
    persona_name: c.personaName ?? p.name ?? p.id,
    persona_is_control: c.isControl ?? p.isControl,
    duration_s: c.durationS,
    turn_count: c.turnCount,
    // Plain English, or nothing at all. ConversationView prints this verbatim.
    end_reason: ended,
  };

  const dimensions: Record<string, any> = {};
  for (const d of s?.dimensions ?? []) {
    dimensions[d.key] = { ...d, ground_truth_audit: d.groundTruthAudit };
  }

  const scorecard = s && {
    ...s,
    // ScoreCard prints `persona_id` as a slug beneath the name. It already has the
    // display name via `personaName`, so this is deliberately empty.
    persona_id: '',
    weighted_score: s.weightedScore,
    dimensions,           // array -> map, keyed by dimension name
    conversation: ended ? { end_reason: ended } : undefined,
  };

  return { ...p, conversation, scorecard };
}

/**
 * Which run the viewer is shown, decided for them.
 *
 * Newest run that is BOTH audio and has at least one conversation on disk — a non-null
 * `mode` is the API's tell that a conversation artifact parsed. An in-flight run (PCM on
 * disk, transcript not written yet) is therefore skipped rather than opened onto an empty
 * screen, and is adopted automatically on the next load once its transcript lands. Falls
 * back to the newest run with any conversation, then to the newest run at all, so this
 * never returns nothing when there is something.
 */
function pickRun(runs: any[]): string | null {
  const audio = runs.find((r) => r.hasAudio && r.mode === 'audio');
  const anyConversation = runs.find((r) => r.mode);
  return (audio ?? anyConversation ?? runs[0])?.id ?? null;
}

/**
 * The shell carries `dashboard-*` classes so the light theme in globals.css can restyle
 * this app WITHOUT any component being edited. Those rules map our dark Tailwind
 * utilities onto the palette by attribute selector, e.g.
 *     .dashboard-shell [class~="bg-neutral-950"] { background: var(--dashboard-paper) }
 * which is why the structure below still reads as the dark original. Keep the utility
 * classes: they are the hooks the theme selects on, not leftovers.
 */
export default function App() {
  const [step, setStep] = useState<Step>(0);
  const [runId, setRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ persona: string; turn: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/runs')
      .then((r) => r.json())
      .then((rs) => {
        if (cancelled) return;
        const list = Array.isArray(rs) ? rs : (rs?.runs ?? []);
        const best = pickRun(list);
        setRunId(best);
        if (!best) setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setErr(String(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setDetail(null);
    setLoading(true);
    fetch(`/api/runs/${runId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setErr(String(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  /**
   * The library, reconciled with what this run actually did.
   *
   * Every persona in `personas/` appears, in a fixed order, whether or not it ran. One
   * the run exercised carries its conversation and scorecard; one it did not carries
   * nothing at all. Anything the run contains that the library does not know about is
   * appended rather than dropped.
   */
  const personas = useMemo(() => {
    const fromRun = new Map<string, any>();
    for (const p of detail?.personas ?? []) fromRun.set(p.id, toArtifactShape(p));

    const known = PERSONA_LIBRARY.map((lib) => {
      const run = fromRun.get(lib.id);
      fromRun.delete(lib.id);
      return {
        ...lib,
        name: run?.name && run.name !== lib.id ? run.name : lib.name,
        isControl: run?.isControl ?? lib.isControl ?? false,
        ran: Boolean(run?.conversation),
        run: run ?? null,
      };
    });

    const extra = [...fromRun.values()].map((run) => ({
      id: run.id as string,
      name: run.name && run.name !== run.id ? run.name : humanise(run.id),
      who: '',
      asks: '',
      isControl: Boolean(run.isControl),
      ran: Boolean(run.conversation),
      run,
    }));

    return [...known, ...extra];
  }, [detail]);

  const live = useMemo(() => personas.filter((p) => p.ran), [personas]);

  // Default the tabs to the first persona that has something to show, and correct the
  // selection if a newer run changes which personas are live.
  useEffect(() => {
    setActive((cur) => (cur && live.some((p) => p.id === cur) ? cur : (live[0]?.id ?? null)));
  }, [live]);

  const current = useMemo(
    () => live.find((p) => p.id === active) ?? live[0] ?? null,
    [live, active]
  );

  const tabs: PersonaTab[] = useMemo(
    () =>
      personas.map((p) => ({
        id: p.id,
        name: p.name,
        available: p.ran,
        isControl: p.isControl,
        note: p.ran
          ? `${p.run?.conversation?.turn_count?.total ?? p.run?.conversation?.turns?.length ?? 0} turns`
          : 'not run yet',
      })),
    [personas]
  );

  const jump = useCallback((persona: string, turn: number) => {
    setActive(persona);
    setFocus({ persona, turn });
    setStep(2);
  }, []);

  const openPersona = useCallback((id: string) => {
    setActive(id);
    setFocus(null);
    setStep(2);
  }, []);

  const recordedAt = detail?.startedAt ? formatDate(detail.startedAt) : null;

  return (
    <main className="dashboard-shell min-h-screen bg-neutral-950 text-neutral-100">
      <header className="dashboard-header border-b border-neutral-800/80 px-8 py-5">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-6">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">voice-spar</h1>
            <p className="text-sm text-neutral-500">
              Synthetic customers call a live voice agent, and report where it breaks.
            </p>
          </div>
          {recordedAt ? (
            <p className="hidden text-sm text-neutral-500 sm:block">Recorded {recordedAt}</p>
          ) : null}
        </div>
      </header>

      <nav className="dashboard-steps border-b border-neutral-800/80 px-8">
        <div className="mx-auto flex max-w-6xl gap-1">
          {STEPS.map((s) => (
            <button
              key={s.n}
              onClick={() => setStep(s.n)}
              className={`border-b-2 px-4 py-3 text-left text-sm transition ${
                step === s.n
                  ? 'border-neutral-100 text-neutral-100'
                  : 'border-transparent text-neutral-500 hover:text-neutral-300'
              }`}
            >
              <span className="font-medium">{s.label}</span>
              <span className="ml-2 hidden text-xs text-neutral-600 sm:inline">{s.hint}</span>
            </button>
          ))}
        </div>
      </nav>

      <div className="dashboard-content mx-auto max-w-6xl px-8 py-8">
        {err ? <EmptyState title="Could not read the conversations" detail={err} /> : null}

        {/* ── 0. Connect ────────────────────────────────────────────────────── */}
        {step === 0 && (
          <section className="space-y-6">
            <Panelish title="Agent under test">
              <dl className="grid grid-cols-1 gap-x-10 gap-y-3 sm:grid-cols-2">
                <Row k="Agent" v={detail?.agentName ?? (loading ? '…' : '—')} />
                <Row k="Platform" v="ElevenLabs" />
                <Row
                  k="Channel"
                  v={!detail ? '…' : detail.hasAudio ? 'Live voice call' : 'Text'}
                />
                <Row
                  k="Customers who called"
                  v={loading && !detail ? '…' : String(live.length)}
                />
              </dl>
              <p className="mt-5 text-sm leading-relaxed text-neutral-400">
                The agent is never modified. It is called the way a customer would call it,
                with no change to its prompt, its model or its language — so what happens
                next is what would happen in production.
              </p>
            </Panelish>
            <Next onClick={() => setStep(1)} label="Meet the customers" />
          </section>
        )}

        {/* ── 1. Personas ───────────────────────────────────────────────────── */}
        {step === 1 && (
          <section className="space-y-6">
            <p className="max-w-2xl text-sm leading-relaxed text-neutral-400">
              Four customers, written to break the agent in four different ways. Each one is
              played by an LLM that stays in character for the whole call.
            </p>

            {loading && !detail ? (
              <EmptyState title="Loading the latest conversations…" />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {personas.map((p) => (
                  <PersonaCard
                    key={p.id}
                    persona={p}
                    onOpen={p.ran ? () => openPersona(p.id) : undefined}
                  />
                ))}
              </div>
            )}

            {live.length ? (
              <Next onClick={() => setStep(2)} label="Open the conversations" />
            ) : null}
          </section>
        )}

        {/* ── 2. Conversations ──────────────────────────────────────────────── */}
        {step === 2 && (
          <section className="space-y-8">
            {live.length ? (
              <>
                <PersonaTabs
                  tabs={tabs}
                  active={current?.id ?? null}
                  onSelect={(id) => {
                    setActive(id);
                    setFocus(null);
                  }}
                  label="Conversations"
                />
                {current ? (
                  /* Keyed on the persona: exactly one transcript is mounted, and
                     switching unmounts the previous one instead of stacking it. */
                  <ConversationPanel
                    key={current.id}
                    runId={runId!}
                    conversation={current.run.conversation}
                    hasAudio={current.run.hasAudio}
                    defectTurns={defectsFromScorecard(current.run.scorecard).defects.map(
                      (d: any) => d.turn
                    )}
                    focusTurn={focus && focus.persona === current.id ? focus.turn : undefined}
                  />
                ) : null}
                <Next onClick={() => setStep(3)} label="See the report" />
              </>
            ) : (
              <EmptyState
                title={loading ? 'Loading the latest conversations…' : 'No conversations yet'}
                detail={
                  loading
                    ? undefined
                    : 'The most recent call has not finished recording. It will appear here once it has.'
                }
              />
            )}
          </section>
        )}

        {/* ── 3. Report ─────────────────────────────────────────────────────── */}
        {step === 3 && (
          <section className="space-y-8">
            {live.length ? (
              <>
                <PersonaTabs
                  tabs={tabs}
                  active={current?.id ?? null}
                  onSelect={setActive}
                  label="Report"
                />
                {current ? <ReportFor key={current.id} persona={current} onJump={jump} /> : null}
              </>
            ) : (
              <EmptyState
                title={loading ? 'Loading…' : 'Nothing has been scored yet'}
                detail={
                  loading
                    ? undefined
                    : 'The report appears once a conversation has been recorded and judged.'
                }
              />
            )}
          </section>
        )}
      </div>
    </main>
  );
}

/** One persona's verdict: the scorecard, plus any confirmed breaches under it. */
function ReportFor({
  persona,
  onJump,
}: {
  persona: any;
  onJump: (personaId: string, turn: number) => void;
}) {
  const { defects, audited } = defectsFromScorecard(persona.run.scorecard);

  // A conversation that has been recorded but not yet judged is the normal state of
  // a run that has only just finished. Say that, rather than letting the scorecard's
  // "the judge produced no verdict" read like the judge failed.
  if (!persona.run.scorecard) {
    return (
      <EmptyState
        title={`${persona.name} has not been scored yet`}
        detail="The conversation is recorded and readable. Its verdict appears here once it has been judged."
      />
    );
  }

  return (
    <div className="space-y-4">
      <ScoreCard
        scorecard={persona.run.scorecard}
        personaName={persona.name}
        isControl={persona.isControl}
        onJumpToTurn={(t: number) => onJump(persona.id, t)}
      />
      {defects.length ? (
        <DefectList
          defects={defects}
          audited={audited}
          personaName={persona.name}
          onJumpToTurn={(t: number) => onJump(persona.id, t)}
        />
      ) : null}
    </div>
  );
}

/**
 * A persona card — the same card whether or not the persona has been run. A run one
 * gains an outcome line and opens; an unrun one says so and does not. Nothing is
 * invented for the unrun ones, and nothing is hidden either: the library is as much of
 * the story as the results are.
 */
function PersonaCard({ persona, onOpen }: { persona: any; onOpen?: () => void }) {
  const conv = persona.run?.conversation;
  const ended = typeof conv?.end_reason === 'string' ? conv.end_reason : null;
  const turns = conv?.turn_count?.total ?? conv?.turns?.length ?? null;

  const body = (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-base font-medium text-neutral-100">{persona.name}</h3>
        {persona.isControl ? <Badge tone="ok">Control</Badge> : null}
        {!persona.ran ? (
          <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-600">
            Not run yet
          </span>
        ) : null}
      </div>

      {persona.who ? (
        <p className="text-sm leading-relaxed text-neutral-400">{persona.who}</p>
      ) : null}
      {persona.asks ? (
        <p className="mt-3 text-sm leading-relaxed text-neutral-500">{persona.asks}</p>
      ) : null}

      <p className="mt-4 text-sm text-neutral-500">
        {persona.ran ? (
          <>
            {turns === null ? 'Called the agent' : `${turns} turns`}
            {ended ? ` · ${ended}` : ''}
            <span className="ml-2 text-neutral-600">— open the conversation →</span>
          </>
        ) : (
          <span className="text-neutral-600">
            Written and ready. This one has not called the agent yet.
          </span>
        )}
      </p>
    </>
  );

  const shell =
    'rounded-xl border p-6 text-left transition ' +
    (persona.ran
      ? 'border-neutral-800 bg-neutral-900/40 hover:border-neutral-700 hover:bg-neutral-900/70'
      : 'border-dashed border-neutral-800/70 bg-neutral-900/10 opacity-60');

  if (!onOpen) return <div className={shell}>{body}</div>;
  return (
    <button type="button" onClick={onOpen} className={`${shell} w-full`}>
      {body}
    </button>
  );
}

function Panelish({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-6">
      <h2 className="mb-4 text-sm font-medium uppercase tracking-wider text-neutral-500">
        {title}
      </h2>
      {children}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-6 border-b border-neutral-800/60 pb-2">
      <dt className="text-sm text-neutral-500">{k}</dt>
      <dd className="text-sm text-neutral-200">{v}</dd>
    </div>
  );
}

function Next({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className="rounded-lg bg-neutral-100 px-5 py-2.5 text-sm font-medium text-neutral-900 transition hover:bg-white"
    >
      {label} →
    </button>
  );
}
