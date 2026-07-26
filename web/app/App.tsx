'use client';

/**
 * The demo shell.
 *
 * Four steps, one job each: prove the target is real, show who attacks it, show the
 * conversations that actually happened, then show what was found.
 *
 * Everything on screen comes from `/api/**`, which reads `runs/` off disk. There is no
 * fixture, no seed, no placeholder — if a field is missing the UI says so rather than
 * inventing a plausible value. That rule is the whole point: this is a tool for telling
 * people uncomfortable things about their agent, and it has no standing to do that if any
 * part of it is decorative.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  RunPicker,
  ConversationPanel,
  ScoreCard,
  DefectList,
  defectsFromScorecard,
  Badge,
  EmptyState,
} from './components';

type Step = 0 | 1 | 2 | 3;

const STEPS: { n: Step; label: string; hint: string }[] = [
  { n: 0, label: 'Connect', hint: 'the agent under test' },
  { n: 1, label: 'Personas', hint: 'who calls it' },
  { n: 2, label: 'Conversations', hint: 'what was actually said' },
  { n: 3, label: 'Report', hint: 'what broke' },
];

export default function App() {
  const [step, setStep] = useState<Step>(0);
  const [runs, setRuns] = useState<any[] | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ persona: string; turn: number } | null>(null);

  useEffect(() => {
    fetch('/api/runs')
      .then((r) => r.json())
      .then((rs) => {
        const list = Array.isArray(rs) ? rs : rs?.runs ?? [];
        setRuns(list);
        // Default to the newest run that actually has audio — the audio conversations are
        // the ones worth opening on. Fall back to the newest of anything.
        const best = list.find((r: any) => r.hasAudio) ?? list[0];
        if (best) setRunId(best.id);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    if (!runId) return;
    setDetail(null);
    fetch(`/api/runs/${runId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setDetail)
      .catch((e) => setErr(String(e)));
  }, [runId]);

  const personas: any[] = detail?.personas ?? [];
  const summary = useMemo(() => runs?.find((r) => r.id === runId), [runs, runId]);
  const isAudio = (detail?.level ?? summary?.level) === 1;

  const jump = useCallback((persona: string, turn: number) => {
    setFocus({ persona, turn });
    setStep(2);
  }, []);

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <header className="border-b border-neutral-800/80 px-8 py-5">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-6">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">voice-spar</h1>
            <p className="text-sm text-neutral-500">
              Synthetic customers call a live voice agent, and report where it breaks.
            </p>
          </div>
          {summary ? (
            <div className="flex items-center gap-2">
              <Badge tone={isAudio ? 'good' : 'unknown'}>
                {isAudio ? 'AUDIO' : 'TEXT'}
              </Badge>
              <span className="font-mono text-xs text-neutral-500">{summary.id}</span>
            </div>
          ) : null}
        </div>
      </header>

      <nav className="border-b border-neutral-800/80 px-8">
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

      <div className="mx-auto max-w-6xl px-8 py-8">
        {err ? (
          <EmptyState title="Could not read the runs on disk" detail={err} />
        ) : null}

        {/* ── 0. Connect ────────────────────────────────────────────────────── */}
        {step === 0 && (
          <section className="space-y-6">
            <Panelish title="Agent under test">
              <dl className="grid grid-cols-1 gap-x-10 gap-y-3 sm:grid-cols-2">
                <Row k="Agent" v={detail?.agentName ?? summary?.personas?.[0]?.name ?? '—'} />
                <Row k="Platform" v="ElevenLabs" />
                <Row k="Mode" v={isAudio ? 'audio (half-duplex)' : 'text'} />
                <Row k="Runs on disk" v={runs ? String(runs.length) : '…'} />
              </dl>
              <p className="mt-5 text-sm leading-relaxed text-neutral-400">
                The agent is never modified. Voice mode sends no override at all; text mode
                sends <code className="text-neutral-300">text_only</code> per-conversation, so
                its prompt, model and language stay exactly as deployed.
              </p>
            </Panelish>
            <Next onClick={() => setStep(1)} label="Choose a run" />
          </section>
        )}

        {/* ── 1. Personas + run selection ───────────────────────────────────── */}
        {step === 1 && (
          <section className="space-y-6">
            <RunPicker runs={runs ?? []} selectedRunId={runId ?? undefined} onSelect={setRunId}
                       loading={runs === null} />
            {personas.length ? (
              <div className="grid gap-4 sm:grid-cols-2">
                {personas.map((p) => (
                  <Panelish key={p.id} title={p.name ?? p.id}>
                    <div className="mb-3 flex flex-wrap gap-2">
                      {p.isControl ? <Badge tone="ok">CONTROL</Badge> : null}
                      {p.stresses ? <Badge tone="unknown">{p.stresses}</Badge> : null}
                      {p.hasAudio ? <Badge tone="good">audio</Badge> : null}
                    </div>
                    <p className="text-sm text-neutral-400">
                      {p.conversation?.turnCount?.total ?? '—'} turns ·{' '}
                      {p.conversation?.endReason?.code ?? 'in flight'}
                    </p>
                  </Panelish>
                ))}
              </div>
            ) : (
              <EmptyState title="No conversations in this run yet" />
            )}
            <Next onClick={() => setStep(2)} label="Open the conversations" />
          </section>
        )}

        {/* ── 2. Conversations ──────────────────────────────────────────────── */}
        {step === 2 && (
          <section className="space-y-10">
            {personas.length === 0 ? <EmptyState title="Nothing to show yet" /> : null}
            {personas.map((p) => (
              <ConversationPanel
                key={p.id}
                runId={runId!}
                conversation={p.conversation}
                hasAudio={p.hasAudio}
                defectTurns={defectsFromScorecard(p.scorecard).defects.map((d: any) => d.turn)}
                focusTurn={focus && focus.persona === p.id ? focus.turn : undefined}
              />
            ))}
            <Next onClick={() => setStep(3)} label="See the report" />
          </section>
        )}

        {/* ── 3. Report ─────────────────────────────────────────────────────── */}
        {step === 3 && (
          <section className="space-y-8">
            {personas.map((p) => {
              const { defects, audited } = defectsFromScorecard(p.scorecard);
              return (
                <div key={p.id} className="space-y-4">
                  <ScoreCard
                    scorecard={p.scorecard}
                    personaName={p.name ?? p.id}
                    isControl={p.isControl}
                    onJumpToTurn={(t: number) => jump(p.id, t)}
                  />
                  {defects.length ? (
                    <DefectList
                      defects={defects}
                      audited={audited}
                      personaName={p.name ?? p.id}
                      onJumpToTurn={(t: number) => jump(p.id, t)}
                    />
                  ) : null}
                </div>
              );
            })}
            {personas.every((p) => !p.scorecard) ? (
              <EmptyState
                title="This run has not been judged yet"
                detail={`Run  ./spar judge ${runId}  to score these conversations.`}
              />
            ) : null}
          </section>
        )}
      </div>
    </main>
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
