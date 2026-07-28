# voice-spar — Requirements

**One line:** Many synthetic customer personas hold real conversations with a live voice agent, and one synthesizer turns all those conversations into a single evaluation report.

Status: **spec only.** Nothing built yet.

---

## 1. The setup

| | |
|---|---|
| **X** — the target | `retention-agent-winback-recovery`, an ElevenLabs hosted agent. We did not build it. We do not modify it. |
| **Y₁…Yₙ** — the personas | Synthetic customers we build on Sarvam. Each one is a YAML file. |
| **The synthesizer** | Reads every conversation and produces one evaluation report. |

### The critical asymmetry

X and Y are **not the same kind of thing**, and the design depends on knowing this:

- **X is hosted.** ElevenLabs runs the loop. We get a socket. Black box.
- **Y is not an agent anyone hosts.** Sarvam sells building blocks only — no "create agent" API exists. Saaras v3 (ears), Sarvam-30B/105B (brain), Bulbul v3 (mouth).

So Y is **our program**, not a remote service. That is why Y can be fully defined in YAML: we own the runner, so the schema is whatever we say it is.

> One program, wearing persona Yₙ as a costume, phoning X.

---

## 2. Data flow

```
personas/*.yaml   (Y1 … Yn)
      │
      ▼
   RUNNER  ──► n conversations against X, in parallel
      │         concurrency cap · retries · budget cap
      ▼
   runs/<run_id>/conversations/<persona_id>.json
      │         transcript · per-turn latency · end reason · cost
      ▼
   JUDGE   ──► scores each conversation INDEPENDENTLY
      │         runs/<run_id>/scorecards/<persona_id>.json
      ▼
   SYNTHESIZER ──► reads all scorecards, finds cross-persona patterns
      │
      ▼
   runs/<run_id>/report.md  +  report.html
```

### Why judging is two stages, not one

Do **not** dump all n transcripts into one LLM call.

- **Per-conversation judge** — each conversation scored on its own, blind to the others. Keeps scores comparable and stops one bad call colouring the rest.
- **Synthesizer** — never re-judges. Its only job is to find **patterns across** conversations: *"replied in English to Hinglish in 4 of 6 calls."*

A pattern across personas is the actual product. A single score is not.

---

## 3. Build ladder

Each level ships on its own. Do not skip.

### Level 0 — text only ← **start here**

- X reached over WebSocket in **Chat Mode**: send `{"type":"user_message","text":"..."}`, receive `agent_response`
- `textOnly` set as a **runtime override** — so we test the real unmodified Tara, not a special test build
- Y = Sarvam chat completion + persona YAML
- No audio anywhere. No sample rates, no timing, no base64.

**Point:** get personas, turn loop, end conditions, judge and report correct while every bug is a text bug.

#### Level 0 is a hard gate

If `textOnly` cannot be driven from a plain Python WebSocket, **stop and report. Do not work around it.**

Specifically, **never** fall back to ElevenLabs' `simulate-conversation` endpoint. It runs the simulated user on ElevenLabs' own model, which would:

- remove Sarvam from the loop entirely — Y stops being a Sarvam agent
- reduce the persona YAML to a prompt string handed to someone else's simulator
- delete our referee — their simulator decides when the conversation ends
- make it **ElevenLabs grading ElevenLabs**: same model family, same blind spots, structurally blind to exactly the code-switching failures this project exists to find

A vendor's own simulator cannot be the customer in a test of that vendor's agent.

### Level 1 — half-duplex audio

Same WebSocket, same target code — audio frames instead of text frames.

1. X speaks → collect audio until it stops
2. → Saaras STT → text
3. → Sarvam LLM + persona → reply text
4. → Bulbul TTS → audio
5. → stream to X, **paced at real-world speed**
6. repeat

Strict ping-pong. Nobody interrupts.

### Level 2 — full duplex (optional, flagged)

Barge-in, both sides talking at once. Behind a feature flag. If it wobbles, flag off and Level 1 is unaffected.

---

## 4. The persona YAML

Full schema in [`personas/_SCHEMA.md`](../personas/_SCHEMA.md).

### The one rule that matters: actor vs referee

Every persona file has two halves:

- **What the model sees** — identity, goal, tactics, tone → becomes the system prompt. The model **acts**.
- **What the model never sees** — turn caps, time limits, exit rules → checked by the runner. The code **decides**.

Why: told *"end when satisfied,"* an LLM either never stops or stops on turn two. Ending a conversation is a rule we enforce, not a request we make.

> The persona is the wrestler. The stopping rule is the referee. Never let the wrestler count their own pin.

### End conditions come in two kinds

- **Hard** — plain counters. Free, instant, never wrong. `turns_over`, `seconds_over`.
- **Soft** — need judgement. `goal_reached`, `agent_offers_human_handoff`. Evaluated by a **separate small LLM call** after each turn, never by the persona itself.

`hard_stop` always wins. Two bots left alone will talk forever.

---

## 5. Judge rules

Non-negotiable:

- **The judge never sees the persona's system prompt.** Otherwise it grades *"did the persona win"* instead of *"was X any good."*
- **No score without a verbatim quote** — every dimension cites turn number and exact words. A score with no quote is unfalsifiable.
- **Judge is a separate call** from the persona, ideally a different model.

### Starter rubric — edit freely in `config.yaml`

Weights tuned for a winback/retention agent.

| Dimension | Weight | Catches |
|---|---|---|
| Goal outcome | 25 | Did it retain the customer, or correctly let them go? |
| Hallucination | 20 | Invented offers, prices, plan names, dates |
| Instruction adherence | 15 | Blew past its own discount ceiling or scope |
| Language handling | 15 | Code-switching, replying in the caller's language |
| Objection handling | 10 | Recovery when pushed back on |
| Escalation & safety | 10 | Abuse handling, knowing when to hand off |
| Conversation flow | 5 | Dead air, talking over, awkward turns |

Hallucination is weighted second on purpose: a winback agent that invents a discount creates a real liability.

---

## 6. Guardrails

- `max_parallel` — cap concurrent conversations (both APIs rate limit)
- `hard_stop.turns` — mandatory on every persona
- `budget_inr` — abort the run before it overspends
- Every run writes a full artifact directory; nothing is only in stdout

---

## 7. Explicitly not building

| Not building | Why |
|---|---|
| Telephony / SIP | Not needed — WebSocket reaches X directly |
| Hosting Y anywhere | Y is a local loop, not a service |
| Auth, accounts, dashboards | Zero value to the eval |
| Modifying X | The whole point is testing the agent as it actually ships |

---

## 8. Open decisions

- Which Sarvam model for the persona brain vs the judge — should differ
- Report format: Markdown only, or self-contained HTML with embedded audio at Level 1
- Whether personas are hand-written only, or generated from a one-line objective
