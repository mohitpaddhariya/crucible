# voice-spar

**Many synthetic customer personas spar with a live voice agent. One synthesizer turns every conversation into a single evaluation report.**

> **Status: Level 0 (text only) runs end to end.** `spar run` → `spar judge` → `spar report`.

---

## The idea

```
personas/*.yaml  ─►  RUNNER  ─►  n conversations with the target agent
                                        │        runs/<id>/conversations/*.json
                                        ▼
                                     JUDGE  (each conversation, independently)
                                        │        runs/<id>/scorecards/*.json
                                        ▼
                                  SYNTHESIZER  ─►  runs/<id>/report.md
```

The three stages talk to each other **only through files**. Nothing is held in memory
between them, which is why the last two are re-runnable for free.

- **X** — the target. Currently `jiohotstar-tara-winback-recovery`, an ElevenLabs hosted agent. We don't modify it.
- **Y₁…Yₙ** — the personas. Built on Sarvam. Each one is a YAML file.

## The two ideas the design rests on

**1. There is no such thing as a Sarvam agent.**
Sarvam sells building blocks — ears, brain, mouth. No hosted agent product, no create-agent API. So Y isn't a service we call, it's **our own loop**. Which is exactly why Y can live entirely in a YAML file: we own the runner, so the schema is whatever we say it is.

**2. The persona acts. The code decides when to stop.**
Every persona file is split in half — the part the model sees (identity, goal, tactics) and the part it never sees (`end_when`). Tell an LLM "end when you're satisfied" and it will either never stop or stop on turn two.

> The persona is the wrestler. The stopping rule is the referee. Never let the wrestler count their own pin.

## Build ladder

| Level | What | Status |
|---|---|---|
| **0** | Text only — ElevenLabs Chat Mode, no audio anywhere | ← start here |
| **1** | Half-duplex audio — Saaras in, Bulbul out, strict ping-pong | later |
| **2** | Full duplex — barge-in, behind a flag | optional |

Level 0 and Level 1 use the **same WebSocket and the same target code**. Only the frame type changes.

## Layout

```
personas/     Y1…Yn — one YAML per persona, plus _SCHEMA.md
agent/        the Y runtime — ears, brain, mouth, referee
targets/      X adapters — elevenlabs first, swappable
runner/       turn loop, concurrency, pacing, capture
judge/        per-conversation scoring, evidence enforcement
synth/        cross-persona patterns, final report
runs/         per-run artifacts (gitignored)
```

## Getting started

```bash
cp config.example.yaml config.yaml
# fill in agent_id + keys
```

## Web UI

The product site lives in `website/` and the run dashboard lives in `web/`.
Start both behind one local origin:

```bash
./scripts/dev_ui.sh
```

Open `http://localhost:4173` for the product site and
`http://localhost:4173/dashboard` for the dashboard. Vite forwards
`/dashboard`, `/_next`, and `/api` to the Next.js process on port 3000, so
navigation and run data stay on one browser origin.

Install dependencies with `npm ci` in both `website/` and `web/`. Their
production builds remain independent:

```bash
npm --prefix website run build
npm --prefix web run build
```

The landing animation is rendered live by `website/src/components/AsciiVideo.tsx`.
Add another WebM/MP4 source to `heroAnimations` in `website/src/App.tsx`; when
more than one source is configured, the animation selector appears automatically.

## The three stages

```bash
./spar run                       # hold the conversations, write the transcripts
./spar judge                     # score the newest run's transcripts
./spar report                    # synthesise every scorecard into one report.md
./spar report --no-llm           # ...deterministic narrative only, zero LLM calls
```

`judge` and `report` default to the newest run and take an explicit run id when you want an
older one (`./spar judge 20260725-185028-f99e33`). All three accept
`--personas price-haggler,angry-churner` — but note what it means on `report`: it narrows the
**rows of the report**, never the analysis. The control gate and the cross-conversation bleed
scan are always computed over every persona in the run, because uniqueness measured over a
subset is a different and wrong question, and a filtered report that quietly skipped the gate
would launder an invalid run.

| Stage | Talks to the target? | Reads | Writes |
|---|---|---|---|
| `run` | **yes** — the only stage that does | `personas/*.yaml`, `config.yaml` | `conversations/`, `raw/`, `prompts/`, `run.json` |
| `judge` | no | `conversations/*.json` | `scorecards/*.json` |
| `report` | no | `scorecards/*.json`, `conversations/*.json`, `run.json` | `report.md`, `synthesis.json` |

Because only `run` opens a socket, **`judge` and `report` cost zero ElevenLabs quota** and
can be re-run as often as you like against byte-identical input — which is what makes the
scoring and the report reproducible rather than merely repeatable.

`report` is the only stage that sees across conversations, so it is the only one that can
say what no single scorecard contains: values that bled between personas, failures that
recur, dimensions that never discriminated, and whether the control persona held (if it
didn't, the run is reported as invalid instead of being averaged away). Its narrative
sentences are LLM-written but citation-audited; `./spar report --no-llm` skips the model
entirely and still writes a complete, deterministic report.

## Docs

- **[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)** — full spec, data flow, judge rules, rubric
- **[docs/INTERFACES.md](docs/INTERFACES.md)** — the artifact contracts between the stages
- **[docs/SYNTH_SPEC.md](docs/SYNTH_SPEC.md)** — the synthesizer contract (`spar report`)
- **[docs/CALIBRATION.md](docs/CALIBRATION.md)** — what the first real run actually showed
- **[personas/_SCHEMA.md](personas/_SCHEMA.md)** — persona YAML schema and how to write a good one
