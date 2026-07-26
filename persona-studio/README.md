# persona-studio

Drop an MP3 of a real customer call, get an evaluation persona out — described in
English, not YAML.

```
pnpm install
pnpm dev
```

## What is real and what is not

The MP3 is real: whatever file you drop is loaded into an object URL and played back in
the browser. It is never uploaded anywhere.

The transcript and the persona are **fixtures**. There is no speech-to-text service and
no generator behind the UI yet. Both fixtures are the StreamNest / Dhruvil win-back call
that already lives at the repo root.

## Wiring in the real pipeline

`src/data/index.ts` is the only module that knows the data is static. It exports two
values, and each has an obvious replacement:

| Export             | Fixture today                              | Replace with                          |
| ------------------ | ------------------------------------------ | ------------------------------------- |
| `transcript`       | `src/data/streamnest-dhruvil.transcript.txt` | speech-to-text on the uploaded audio  |
| `generatedPersona` | `src/data/generated-persona.yaml`          | the persona generator's YAML output   |

`src/hooks/usePipeline.ts` already models the work as asynchronous stages that can fail,
so swapping the fixtures for `await`ed calls does not change any component.

## Layout

```
src/
  data/         the swap point — fixtures in, parsed domain objects out
  lib/
    persona.ts    untyped YAML -> Persona, the only boundary that sees `unknown`
    transcript.ts the `[ 15s] SPEAKER: text` format -> turns
    narrate.ts    Persona -> English sentences (pure, no markup)
  hooks/
    usePipeline.ts  the upload -> transcribe -> generate state machine
  components/
    studio/       the screens
    ui/           shadcn primitives, unmodified
```

Two rules the code holds to:

- **The YAML is never rendered.** `PersonaReport` answers "who is this, what will they
  do, what is the agent allowed to say back". The file itself is a download.
- **Illegal states are unrepresentable.** The pipeline stage carries its own payload, so
  there is no `transcript: Transcript | null` to guard against; `end_when` is parsed into
  a discriminated union rather than left as a list of one-key maps.

## Notes on rendering

Transcripts are set in the `transcript-type` utility (`src/index.css`), not in a
monospace face. These calls are Hindi and Hinglish — Devanagari conjuncts break in mono,
and matras collide above the line at anything tighter than ~1.8 line-height.
