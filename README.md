# voice-spar

**Many synthetic customer personas spar with a live voice agent. One synthesizer turns every conversation into a single evaluation report.**

> **Status: spec + scaffold only.** No implementation yet. Nothing here has been run.

---

## The idea

```
personas/*.yaml  ─►  RUNNER  ─►  n conversations with the target agent
                                        │
                                        ▼
                                     JUDGE  (each conversation, independently)
                                        │
                                        ▼
                                  SYNTHESIZER  ─►  one eval report
```

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

## Docs

- **[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)** — full spec, data flow, judge rules, rubric
- **[personas/_SCHEMA.md](personas/_SCHEMA.md)** — persona YAML schema and how to write a good one
