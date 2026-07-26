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
type ConfigTab = 'general' | 'voice' | 'evaluation';

const STEPS: { n: Step; label: string; hint: string }[] = [
  { n: 0, label: 'Connect', hint: 'the agent under test' },
  { n: 1, label: 'Personas', hint: 'who calls it' },
  { n: 2, label: 'Conversations', hint: 'what was actually said' },
  { n: 3, label: 'Report', hint: 'what broke' },
];

const CONFIG_TABS: { id: ConfigTab; label: string; description: string }[] = [
  { id: 'general', label: 'General', description: 'Target and deployment' },
  { id: 'voice', label: 'Voice', description: 'Transport and overrides' },
  { id: 'evaluation', label: 'Evaluation', description: 'Runs and evidence' },
];

export default function App() {
  const [step, setStep] = useState<Step>(0);
  const [configTab, setConfigTab] = useState<ConfigTab>('general');
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
    <main className="dashboard-shell min-h-screen bg-neutral-950 text-neutral-100">
      <header className="dashboard-header border-b border-neutral-800/80 px-4 py-6 sm:px-8">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
          <a className="flex min-w-0 items-center gap-3" href="/" aria-label="Back to Crucible">
            <img
              className="h-10 w-10"
              src="/lotus-logo-transparent.svg"
              alt=""
              width="40"
              height="40"
            />
            <div>
              <h1 className="text-2xl font-medium">Crucible</h1>
              <p className="text-sm text-neutral-500">Voice-agent evaluation console</p>
            </div>
          </a>
          <div className="flex shrink-0 items-center justify-end gap-4">
            {summary ? (
              <div className="hidden items-center gap-2 sm:flex">
                <Badge tone={isAudio ? 'good' : 'unknown'}>
                  {isAudio ? 'AUDIO' : 'TEXT'}
                </Badge>
                <span className="font-mono text-xs text-neutral-500">{summary.id}</span>
              </div>
            ) : null}
            <a
              className="whitespace-nowrap border-l border-neutral-800 pl-4 text-sm text-neutral-400 transition hover:text-neutral-100"
              href="/"
            >
              Back to site
            </a>
          </div>
        </div>
      </header>

      <nav className="dashboard-steps border-b border-neutral-800/80 px-4 sm:px-8">
        <div className="mx-auto grid max-w-7xl grid-cols-4 sm:flex sm:gap-1">
          {STEPS.map((s) => (
            <button
              key={s.n}
              onClick={() => setStep(s.n)}
              className={`border-b-2 px-1 py-3 text-center text-xs transition sm:px-4 sm:text-left sm:text-sm ${
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

      <div className="dashboard-content mx-auto max-w-7xl px-4 py-10 sm:px-8 sm:py-14">
        {err ? (
          <EmptyState title="Could not read the runs on disk" detail={err} />
        ) : null}

        {/* ── 0. Connect ────────────────────────────────────────────────────── */}
        {step === 0 && (
          <section className="space-y-8">
            <div className="config-surface">
              <header className="config-surface-header">
                <div>
                  <h2>Agent configuration</h2>
                  <p>Read-only deployment context for the selected evaluation run.</p>
                </div>
                <span className="config-provider">ElevenLabs</span>
              </header>

              <div className="config-surface-body">
                <nav className="config-nav" aria-label="Agent configuration sections">
                  {CONFIG_TABS.map((tab) => (
                    <button
                      className={configTab === tab.id ? 'active' : ''}
                      key={tab.id}
                      type="button"
                      aria-pressed={configTab === tab.id}
                      onClick={() => setConfigTab(tab.id)}
                    >
                      <strong>{tab.label}</strong>
                      <span>{tab.description}</span>
                    </button>
                  ))}
                </nav>

                <div className="config-pane">
                  {configTab === 'general' ? (
                    <>
                      <ConfigHeading
                        title="General"
                        description="The target deployment Crucible evaluates without modification."
                      />
                      <dl>
                        <ConfigRow
                          label="Agent"
                          value={detail?.agentName ?? 'Not available in the current run'}
                        />
                        <ConfigRow label="Platform" value="ElevenLabs" />
                        <ConfigRow
                          label="Connection mode"
                          value={isAudio ? 'Audio / half-duplex' : 'Text conversation'}
                        />
                        <ConfigRow label="Selected run" value={runId ?? 'No run selected'} mono />
                      </dl>
                    </>
                  ) : null}

                  {configTab === 'voice' ? (
                    <>
                      <ConfigHeading
                        title="Voice"
                        description="Transport behavior is inherited from the run and target deployment."
                      />
                      <dl>
                        <ConfigRow
                          label="Transport"
                          value={isAudio ? 'WebSocket audio' : 'Text-only conversation'}
                        />
                        <ConfigRow
                          label="Synthetic caller"
                          value={isAudio ? 'Sarvam speech pipeline' : 'Inactive for text runs'}
                        />
                        <ConfigRow label="Target voice" value="Preserved from deployed agent" />
                        <ConfigRow
                          label="Per-conversation override"
                          value={isAudio ? 'None' : 'text_only'}
                          mono={!isAudio}
                        />
                      </dl>
                    </>
                  ) : null}

                  {configTab === 'evaluation' ? (
                    <>
                      <ConfigHeading
                        title="Evaluation"
                        description="Evidence availability for the currently selected run."
                      />
                      <dl>
                        <ConfigRow label="Runs on disk" value={runs ? String(runs.length) : 'Loading'} />
                        <ConfigRow
                          label="Personas"
                          value={runId ? String(personas.length) : 'No run selected'}
                        />
                        <ConfigRow
                          label="Evidence"
                          value={isAudio ? 'Audio and transcript' : 'Transcript'}
                        />
                        <ConfigRow
                          label="Report"
                          value={
                            summary
                              ? summary.hasReport
                                ? 'Available'
                                : 'Not generated'
                              : 'No run selected'
                          }
                        />
                      </dl>
                    </>
                  ) : null}

                  <p className="config-note">
                    Crucible never changes the deployed prompt, model, language, or voice settings.
                  </p>
                </div>
              </div>
            </div>
            <Next onClick={() => setStep(1)} label="Choose a run" />
          </section>
        )}

        {/* ── 1. Personas + run selection ───────────────────────────────────── */}
        {step === 1 && (
          <section className="space-y-6">
            <RunPicker runs={runs ?? []} selectedRunId={runId ?? undefined} onSelect={setRunId}
                       loading={runs === null} />
            {runId ? (
              <>
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
              </>
            ) : null}
          </section>
        )}

        {/* ── 2. Conversations ──────────────────────────────────────────────── */}
        {step === 2 && (
          <section className="space-y-10">
            {personas.length === 0 ? (
              <EmptyState
                title={runId ? 'Nothing to show yet' : 'Choose a run to open its conversations'}
              />
            ) : null}
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
            {runId ? <Next onClick={() => setStep(3)} label="See the report" /> : null}
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
                title={runId ? 'This run has not been judged yet' : 'Choose a run to see its report'}
                detail={
                  runId
                    ? `Run  ./spar judge ${runId}  to score these conversations.`
                    : undefined
                }
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

function ConfigHeading({ title, description }: { title: string; description: string }) {
  return (
    <div className="config-pane-heading">
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}

function ConfigRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="config-row">
      <dt>{label}</dt>
      <dd className={mono ? 'font-mono' : ''}>{value}</dd>
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
