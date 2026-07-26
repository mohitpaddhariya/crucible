'use client';

import { useEffect, useMemo, useState } from 'react';
import type {
  Persona,
  Conversation,
  Scorecard,
  Defect,
} from '../lib/data';

const STEPS = ['Connect', 'Personas', 'Run', 'Report'];

export default function Demo(props: {
  runId: string;
  personas: Persona[];
  conversations: Conversation[];
  scorecards: Scorecard[];
  defects: Defect[];
}) {
  const { runId, personas, conversations, scorecards, defects } = props;
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState<string[]>(() =>
    personas.map((p) => p.id)
  );

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <Header step={step} setStep={setStep} />

      <main className="mt-12">
        {step === 0 && <Connect onNext={() => setStep(1)} />}
        {step === 1 && (
          <Personas
            personas={personas}
            selected={selected}
            setSelected={setSelected}
            onNext={() => setStep(2)}
          />
        )}
        {step === 2 && (
          <Run
            runId={runId}
            conversations={conversations}
            onNext={() => setStep(3)}
          />
        )}
        {step === 3 && (
          <Report
            runId={runId}
            personas={personas}
            scorecards={scorecards}
            defects={defects}
          />
        )}
      </main>
    </div>
  );
}

/* ---------------------------------------------------------------- chrome */

function Header({
  step,
  setStep,
}: {
  step: number;
  setStep: (n: number) => void;
}) {
  return (
    <header>
      <div className="flex items-baseline gap-3">
        <h1 className="text-xl font-semibold tracking-tight">voice-spar</h1>
        <p className="text-sm text-neutral-500">
          Synthetic Indian customers that phone your voice agent and find where
          it breaks.
        </p>
      </div>

      <nav className="mt-6 flex flex-wrap gap-2">
        {STEPS.map((label, i) => {
          const active = i === step;
          return (
            <button
              key={label}
              onClick={() => setStep(i)}
              className={`rounded-full border px-4 py-1.5 text-sm transition ${
                active
                  ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300'
                  : 'border-neutral-800 text-neutral-500 hover:border-neutral-700 hover:text-neutral-300'
              }`}
            >
              <span className="mr-2 opacity-50">{i + 1}</span>
              {label}
            </button>
          );
        })}
      </nav>
    </header>
  );
}

function NextButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-lg bg-emerald-500 px-5 py-2.5 text-sm font-medium text-neutral-950 transition hover:bg-emerald-400"
    >
      {children}
    </button>
  );
}

function SectionTitle({
  title,
  sub,
}: {
  title: string;
  sub?: string;
}) {
  return (
    <div className="mb-6">
      <h2 className="text-lg font-medium">{title}</h2>
      {sub && <p className="mt-1 text-sm text-neutral-500">{sub}</p>}
    </div>
  );
}

/* ------------------------------------------------------------- 1. connect */

const AGENT = {
  name: 'jiohotstar-tara-winback-recovery',
  id: 'agent_9801kv9rahs8fzaa0dj6x85aq6dc',
  llm: 'qwen35-397b-a17b',
  language: 'en',
};

function Connect({ onNext }: { onNext: () => void }) {
  return (
    <section>
      <SectionTitle
        title="Target agent"
        sub="The live ElevenLabs voice agent under test."
      />

      <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-6">
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400" />
          </span>
          <span className="text-sm font-medium text-emerald-300">Connected</span>
        </div>

        <dl className="mt-6 grid gap-x-8 gap-y-4 sm:grid-cols-2">
          <Field label="Agent name" value={AGENT.name} mono />
          <Field label="Agent ID" value={AGENT.id} mono />
          <Field label="LLM" value={AGENT.llm} mono />
          <Field label="Language" value={AGENT.language} mono />
        </dl>
      </div>

      <div className="mt-8">
        <NextButton onClick={onNext}>Continue</NextButton>
      </div>
    </section>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wider text-neutral-500">
        {label}
      </dt>
      <dd
        className={`mt-1 text-sm break-all ${
          mono ? 'font-mono text-neutral-200' : 'text-neutral-200'
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

/* ------------------------------------------------------------ 2. personas */

function Personas({
  personas,
  selected,
  setSelected,
  onNext,
}: {
  personas: Persona[];
  selected: string[];
  setSelected: (v: string[]) => void;
  onNext: () => void;
}) {
  const toggle = (id: string) =>
    setSelected(
      selected.includes(id)
        ? selected.filter((x) => x !== id)
        : [...selected, id]
    );

  return (
    <section>
      <SectionTitle
        title="Personas"
        sub="Each one is built to break a different dimension of the agent."
      />

      <div className="grid gap-4 sm:grid-cols-2">
        {personas.map((p) => {
          const on = selected.includes(p.id);
          return (
            <button
              key={p.id}
              onClick={() => toggle(p.id)}
              className={`rounded-xl border p-5 text-left transition ${
                on
                  ? 'border-emerald-500/40 bg-emerald-500/[0.06]'
                  : 'border-neutral-800 bg-neutral-900/30 opacity-55 hover:opacity-80'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-medium">{p.name}</h3>
                  <p className="mt-0.5 font-mono text-xs text-neutral-500">
                    {p.id}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {p.control && (
                    <span className="rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-sky-300">
                      CONTROL
                    </span>
                  )}
                  <span
                    className={`flex h-4 w-4 items-center justify-center rounded border text-[10px] ${
                      on
                        ? 'border-emerald-400 bg-emerald-400 text-neutral-950'
                        : 'border-neutral-600'
                    }`}
                  >
                    {on ? '✓' : ''}
                  </span>
                </div>
              </div>

              <p className="mt-3 text-sm leading-relaxed text-neutral-300">
                {p.who}
              </p>

              <dl className="mt-4 space-y-1.5 text-xs">
                <Row label="Stresses">
                  <span className="font-mono text-amber-300">{p.stresses}</span>
                </Row>
                <Row label="Language">
                  <span className="text-neutral-300">{p.language}</span>
                </Row>
                <Row label="Offer">
                  <span className="text-neutral-300">{p.offer_text}</span>
                </Row>
              </dl>
            </button>
          );
        })}
      </div>

      <div className="mt-8 flex items-center gap-4">
        <NextButton onClick={onNext}>
          Run {selected.length} persona{selected.length === 1 ? '' : 's'}
        </NextButton>
        <span className="text-sm text-neutral-500">
          {selected.length} of {personas.length} selected
        </span>
      </div>
    </section>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-2">
      <dt className="w-16 shrink-0 text-neutral-500">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

/* ----------------------------------------------------------------- 3. run */

function Run({
  runId,
  conversations,
  onNext,
}: {
  runId: string;
  conversations: Conversation[];
  onNext: () => void;
}) {
  const [active, setActive] = useState(
    conversations.find((c) => c.persona_id === 'already-switched')?.persona_id ??
      conversations[0]?.persona_id ??
      ''
  );

  const conv = conversations.find((c) => c.persona_id === active);
  const total = conv?.turns.length ?? 0;
  const [shown, setShown] = useState(total);

  // Restart the reveal whenever the selected conversation changes.
  useEffect(() => {
    setShown(0);
  }, [active]);

  useEffect(() => {
    if (shown >= total) return;
    const t = setTimeout(() => setShown((n) => n + 1), 250);
    return () => clearTimeout(t);
  }, [shown, total]);

  if (!conv) {
    return (
      <section>
        <SectionTitle title="Run" />
        <p className="text-sm text-neutral-500">
          No conversations found for run {runId}.
        </p>
      </section>
    );
  }

  const done = shown >= total;
  const pct = total ? Math.round((shown / total) * 100) : 100;

  return (
    <section>
      {/* Honesty chip — this is a replay, not a live call. */}
      <div className="mb-6 flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-4 py-2.5">
        <span className="text-xs font-semibold tracking-wide text-amber-300">
          REPLAY
        </span>
        <span className="text-sm text-amber-100/80">
          Replaying recorded run{' '}
          <span className="font-mono">{runId}</span> — this is not calling the
          live agent right now.
        </span>
      </div>

      <div className="mb-5 flex flex-wrap gap-2">
        {conversations.map((c) => (
          <button
            key={c.persona_id}
            onClick={() => setActive(c.persona_id)}
            className={`rounded-lg border px-3 py-1.5 text-sm transition ${
              c.persona_id === active
                ? 'border-neutral-600 bg-neutral-800 text-neutral-100'
                : 'border-neutral-800 text-neutral-500 hover:text-neutral-300'
            }`}
          >
            {c.persona_name}
          </button>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-neutral-500">
        <span>
          {shown} / {total} turns
        </span>
        <span>{conv.duration_s.toFixed(1)}s call</span>
        <span className="font-mono">{conv.end_reason}</span>
        {!done && <span className="text-emerald-400">replaying…</span>}
      </div>

      <div className="mb-6 h-0.5 w-full overflow-hidden rounded bg-neutral-800">
        <div
          className="h-full bg-emerald-500 transition-all duration-200"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="space-y-3">
        {conv.turns.slice(0, shown).map((t) => {
          const agent = t.speaker === 'agent';
          return (
            <div
              key={t.idx}
              className={`flex ${agent ? 'justify-start' : 'justify-end'}`}
            >
              <div
                className={`max-w-[78%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                  agent
                    ? 'rounded-tl-sm bg-neutral-800/70 text-neutral-200'
                    : 'rounded-tr-sm bg-emerald-500/15 text-emerald-50'
                }`}
              >
                <div
                  className={`mb-1 text-[10px] uppercase tracking-wider ${
                    agent ? 'text-neutral-500' : 'text-emerald-400/70'
                  }`}
                >
                  {agent ? 'Agent' : 'Synthetic customer'} · turn {t.idx}
                </div>
                {t.text}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-8 flex items-center gap-4">
        <NextButton onClick={onNext}>See the report</NextButton>
        {!done && (
          <button
            onClick={() => setShown(total)}
            className="text-sm text-neutral-500 hover:text-neutral-300"
          >
            Skip to end
          </button>
        )}
      </div>
    </section>
  );
}

/* -------------------------------------------------------------- 4. report */

function bandStyle(band: string) {
  const b = band.toLowerCase();
  if (b.includes('do not ship'))
    return {
      text: 'text-rose-300',
      border: 'border-rose-500/40',
      bg: 'bg-rose-500/[0.07]',
      bar: 'bg-rose-500',
    };
  if (b.includes('production'))
    return {
      text: 'text-emerald-300',
      border: 'border-emerald-500/40',
      bg: 'bg-emerald-500/[0.07]',
      bar: 'bg-emerald-500',
    };
  return {
    text: 'text-amber-300',
    border: 'border-amber-500/40',
    bg: 'bg-amber-500/[0.07]',
    bar: 'bg-amber-500',
  };
}

function Report({
  runId,
  personas,
  scorecards,
  defects,
}: {
  runId: string;
  personas: Persona[];
  scorecards: Scorecard[];
  defects: Defect[];
}) {
  const byId = useMemo(
    () => Object.fromEntries(personas.map((p) => [p.id, p])),
    [personas]
  );
  const control = scorecards.find((s) => byId[s.persona_id]?.control);

  return (
    <section className="space-y-14">
      {/* ---- a. scorecards ---- */}
      <div>
        <SectionTitle
          title="Scorecards"
          sub={`Run ${runId} · every number below is read from the run's scorecard files.`}
        />

        {control && (
          <div className="mb-5 rounded-lg border border-sky-500/30 bg-sky-500/[0.07] px-4 py-2.5 text-sm text-sky-100/85">
            <span className="font-semibold text-sky-300">CONTROL GATE PASSED</span>{' '}
            — {byId[control.persona_id]?.name} scored{' '}
            {control.weighted_score.toFixed(1)} ({control.band}). The harness
            works, so the failures below are the agent&apos;s, not ours.
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          {scorecards.map((s) => (
            <ScoreCard
              key={s.persona_id}
              sc={s}
              persona={byId[s.persona_id]}
            />
          ))}
        </div>
      </div>

      {/* ---- b. confirmed defects ---- */}
      <div>
        <SectionTitle
          title={`Confirmed defects (${defects.length})`}
          sub="Ground-truth breaches from already-switched — each one audited against the scenario's own rules."
        />

        <div className="space-y-3">
          {defects.map((d, i) => (
            <div
              key={i}
              className="rounded-xl border border-rose-500/30 bg-rose-500/[0.05] p-5"
            >
              <div className="mb-3 flex items-center gap-3">
                <span className="rounded bg-rose-500/20 px-2 py-0.5 font-mono text-[11px] text-rose-300">
                  turn {d.turn}
                </span>
                <span className="text-[11px] uppercase tracking-wider text-rose-400/70">
                  Confirmed breach
                </span>
              </div>

              <blockquote className="border-l-2 border-rose-500/50 pl-4 text-[15px] leading-relaxed text-neutral-100">
                &ldquo;{d.quote}&rdquo;
              </blockquote>

              <div className="mt-4">
                <div className="text-[11px] uppercase tracking-wider text-neutral-500">
                  Rule breached
                </div>
                <p className="mt-1 text-sm text-neutral-300">{d.entry}</p>
              </div>
            </div>
          ))}
          {defects.length === 0 && (
            <p className="text-sm text-neutral-500">No defects found.</p>
          )}
        </div>
      </div>

      {/* ---- c. audio ---- */}
      <AudioSection />
    </section>
  );
}

function ScoreCard({
  sc,
  persona,
}: {
  sc: Scorecard;
  persona?: Persona;
}) {
  const [open, setOpen] = useState(false);
  const st = bandStyle(sc.band);

  return (
    <div className={`rounded-xl border ${st.border} ${st.bg} p-5`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate font-medium">
              {persona?.name ?? sc.persona_id}
            </h3>
            {persona?.control && (
              <span className="shrink-0 rounded border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-sky-300">
                CONTROL
              </span>
            )}
          </div>
          <p className="mt-0.5 font-mono text-xs text-neutral-500">
            {sc.persona_id}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <div className={`text-3xl font-semibold tabular-nums ${st.text}`}>
            {sc.weighted_score.toFixed(1)}
          </div>
          <div className={`text-xs font-medium ${st.text}`}>{sc.band}</div>
        </div>
      </div>

      <div className="mt-4 h-1 w-full overflow-hidden rounded bg-neutral-800">
        <div
          className={`h-full ${st.bar}`}
          style={{ width: `${Math.max(0, Math.min(100, sc.weighted_score))}%` }}
        />
      </div>

      <p className="mt-2 text-xs text-neutral-500">
        {sc.coverage_pct}% of scoring weight covered
      </p>

      <button
        onClick={() => setOpen(!open)}
        className="mt-4 text-xs text-neutral-400 hover:text-neutral-200"
      >
        {open ? 'Hide' : 'Show'} 7 dimensions
      </button>

      {open && (
        <div className="mt-3 space-y-2.5 border-t border-neutral-800 pt-3">
          {sc.dimensions.map((d) => (
            <div key={d.key} className="text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-neutral-300">{d.key}</span>
                <span className="shrink-0 text-neutral-500">
                  <span
                    className={
                      d.verdict === 'fail'
                        ? 'text-rose-400'
                        : d.verdict === 'partial'
                          ? 'text-amber-400'
                          : 'text-emerald-400'
                    }
                  >
                    {d.verdict}
                  </span>
                  {' · '}
                  {d.score === null ? 'unscored' : d.score.toFixed(2)}
                  {' · w'}
                  {d.weight}
                </span>
              </div>
              {d.reasoning && (
                <p className="mt-1 leading-relaxed text-neutral-500">
                  {d.reasoning}
                </p>
              )}
              {d.evidence.slice(0, 2).map((e, i) => (
                <p
                  key={i}
                  className="mt-1 border-l border-neutral-700 pl-2 text-neutral-400 italic"
                >
                  turn {e.turn}: &ldquo;{e.quote}&rdquo;
                </p>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AudioSection() {
  return (
    <div>
      <SectionTitle
        title="The part you can only hear"
        sub="A real captured call between a Sarvam-voiced synthetic customer and the live agent."
      />

      <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-6">
        <audio controls preload="metadata" className="w-full">
          <source src="/FULL_CONVERSATION.wav" type="audio/wav" />
          Your browser does not support the audio element.
        </audio>

        <p className="mt-3 text-sm text-neutral-400">
          62s — Sarvam-voiced synthetic customer vs the live ElevenLabs agent.
          Nobody spoke into a microphone.
        </p>

        <div className="mt-7 overflow-hidden rounded-lg border border-neutral-800">
          <div className="border-b border-neutral-800 bg-neutral-900/60 px-4 py-3">
            <div className="text-[11px] uppercase tracking-wider text-neutral-500">
              we sent
            </div>
            <p className="mt-1.5 text-[15px] leading-relaxed text-neutral-100">
              &ldquo;Arre ten percent se kya hota hai? Mere dost ko toh thirty
              percent off mila tha.&rdquo;
            </p>
          </div>
          <div className="bg-rose-500/[0.06] px-4 py-3">
            <div className="text-[11px] uppercase tracking-wider text-rose-400/80">
              agent&apos;s ASR heard
            </div>
            <p className="mt-1.5 text-[15px] leading-relaxed text-neutral-100">
              &ldquo;Bro, 10% से काया होता है? ये 20% तो 30% off माइला दा।&rdquo;
            </p>
          </div>
        </div>

        <p className="mt-4 text-sm leading-relaxed text-neutral-400">
          The agent&apos;s own speech recognition invented a 20% nobody said — on
          a call about money. It only shows up in audio.
        </p>
      </div>
    </div>
  );
}
