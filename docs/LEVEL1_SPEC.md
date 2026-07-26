# LEVEL1_SPEC — half-duplex audio

**Status: design, ready to implement.** Every wire-level claim in this document is a
measurement from one of three live spikes, not an assumption:

- `scripts/spike_audio_protocol.py` — 7 live conversations, the pong/timer 2×2, the
  turn-end calibration, the agent config dump.
- `scripts/spike_sarvam_speech.py` — Bulbul/Saaras endpoints, formats, rosters, latency.
- `scripts/spike_audio_turn.py` — 3 full audio turns end-to-end
  (`conv_8701kyekt84jekm96zjwfwj0jnwj`), reproduced with 2 turns
  (`conv_1901kyekyjtpfmctw94pnefqdfyy`), plus the two turn-2 deadlock captures that
  justify the mic-hold rule (`runs/_spike_audio_turn/result.json`, `result_rest.json`).

Where this spec says MUST, a spike died without it. Where it says SHOULD, a spike
measured margin. Anything not verified is in §9 (risks) and labelled UNVERIFIED.

Scope: Level 1 = half-duplex audio against the same live agent. Tara speaks → we
detect end of turn from amplitude → Saaras transcribes (cross-check only) → the
Sarvam persona thinks on `agent_response` text → Bulbul synthesises → we stream PCM
back, hold the mic with paced silence until `user_transcript`, and repeat. No
barge-in. No full duplex. Level 0 keeps working untouched (§7).

---

## 0. The three findings this design is built around

These override anything in `INTERFACES.md` that contradicts them. `INTERFACES.md`
remains authoritative **for text mode only**.

1. **The 60-second kill is a PONG rule, not a user-message rule.** The 1002
   "No user message received for 60 seconds" close from run `20260726-060627` happened
   because the runner was blocked inside `persona.reply()` for 64 s and ponged nothing.
   Seven live arms proved it: every arm that kept ponging survived 100+ s of total
   user silence (in both modes, including 112 s of pure idle after a real spoken turn);
   every arm that stopped ponging died. **Consequence: the socket reader is a
   permanently-live asyncio task from `open()` to `close()`, ponging inline. No compute
   step ever owns the socket.** Level 0's 40 s persona bound treated a symptom and is
   retired (§4.4).

2. **There is no end-of-turn event; the end-of-turn signal is an amplitude floor.**
   Audio frames never stop (background_sound `office1` @ 0.08 streams a 9600-byte
   carrier forever, ~3.3 frames/s, even in text mode). `agent_response` is a turn-START
   marker (arrives 0.31–0.83 s after the first speech frame, 9–11 s before the last).
   The working detector, calibrated over 8 turns and then confirmed over 11 more with
   zero false splits/merges/misses: **speech iff frame peak ≥ 3000; turn over after
   ≥ 5 consecutive sub-threshold frames (~1.5 s), OR'd with a ≥ 1.5 s wall-clock
   backstop since the last speech frame.** 0.9 s was measured too tight (split a real
   turn at el 28.876). End of CONVERSATION does have an explicit signal:
   `agent_tool_response` with `tool_name == "end_call"`, `tool_type == "system"`,
   `is_called == true`, followed ~0.4 s later by a clean 1000 close.

3. **The mic must stay open with paced silence until `user_transcript` arrives, then
   shut immediately — bounded at 8 s.** Stream-then-stop works for exactly one turn per
   conversation and then deadlocks silently forever (two full conversations burned
   proving it; the socket stays healthy, nothing errors, the run just looks short).
   Open-ended silence is the opposite failure: it endpoints as empty user turns on the
   `turn_timeout=10.0` cadence, Tara nudges twice, invokes `end_call`, close 1000 at
   59 s. Measured hold cost with the transcript trigger: 2.2–2.8 s per turn, endpoint
   fires 2.1–2.4 s after the last real chunk. The safe window is ~[3 s, 8 s]; **bound
   the hold at 8 s** (the spike's 12 s was noted as too generous). `user_transcript` is
   the only control signal between the two silent failure modes.

---

## 1. The half-duplex loop

### 1.1 Exact sequence

```
 1  target.open(scenario_vars)
      - wss connect, xi-api-key header, ping_interval=None
      - init frame: conversation_initiation_client_data with dynamic_variables
        TOP-LEVEL; NO conversation_config_override at all (omit text_only entirely)
      - conversation_initiation_metadata echoes both audio formats: assert
        agent_output_audio_format == user_input_audio_format == "pcm_16000"
      - START THE READER TASK. It runs until close():
          ping            -> pong immediately ({"type":"pong","event_id":...})
          audio           -> decode, peak, route into the current turn buffer
          agent_response  -> record text + event_id (turn-START, never turn-end)
          user_transcript -> record + signal the mic-hold to stop
          agent_tool_response(end_call) -> set conversation_over
          anything else   -> raw-log, count, continue
        The reader NEVER awaits Sarvam, never does TTS, never blocks on compute.
 2  await turn-end of the unprompted opening (amplitude detector, §0.2).
      turns.append(agent turn: text = agent_response verbatim, audio saved)
 3  loop:
 4    referee.check(state)            # unchanged from Level 0
 5    if reason: break
 6    (optional, flag) STT cross-check of the agent audio via Saaras  ~1.2 s
 7    persona.reply(turns)            # Sarvam, ~5.8 s; socket alive via reader
 8    referee.check_hard(state)       # never overshoot, unchanged
 9    tts = Bulbul v2 REST (speech_sample_rate=16000)   ~0.9–1.4 s
      strip RIFF (walk the chunk table; ~30 µs) -> raw pcm_16000
10    speak_and_hold(pcm):            # §4 — one absolute clock for both phases
        a. stream 3200-byte user_audio_chunk frames every 100 ms
           (utterance + 1.5 s trailing silence)
        b. WITHOUT breaking the clock, keep streaming silence chunks until the
           reader signals user_transcript; hard bound 8.0 s
        c. if no user_transcript within 8.0 s of the last real audio chunk:
           HARD ERROR `no_user_transcript` (this is the turn-2 deadlock — a bug,
           never "a slow turn")
11    await agent turn-end (amplitude detector); append agent turn
12    if conversation_over (end_call seen): break with code "agent_ended_call"
13  target.close(); THEN close the raw log (reverse order raises
    ValueError('I/O operation on closed file') in the reader — measured)
14  write conversations/<persona_id>.json (§3)
```

Timeouts: opening turn 25 s (speech began at 1.3 s in every capture); subsequent
turn-end detection 90 s overall; `user_transcript` bound 8 s as above.

`user_message` text frames are not part of this loop. (They reportedly still work in
voice mode but our flow never sends one; keep `send_user_turn(text)` only as a debug
escape hatch, clearly marked untested-in-voice.)

### 1.2 Where the 60 seconds goes — and why it no longer binds

Per persona turn cycle, measured:

| Phase | Measured | Notes |
|---|---|---|
| Tara's turn playout (realtime, cannot fast-forward) | 8.1–13.8 s | 9600 B / ~300 ms, paced by the server |
| Saaras STT cross-check (optional) | 0.94–1.59 s | median ~1.2 s |
| wrap_wav + strip_riff | ~70 µs total | a rounding error |
| Sarvam persona LLM | ~5.8 s | Level 0 measurement, unchanged |
| Bulbul v2 REST TTS (80–113 chars) | 0.93–1.36 s | |
| Our playout (realtime) | 4.7–6.5 s | 16.4–17.3 chars/s — budget at 17 |
| Mic hold until `user_transcript` | 2.2–2.8 s | bounded at 8 s |
| **Full cycle, turn-end to turn-end** | **19.9–24.4 s** | |

**Headroom against the nominal 60 s window:** worst measured compute+stream slice was
1.59 + 1.36 + 6.9 + 2.8 = 12.65 s → 47 s free, ~8× margin on the LLM. But that window
is not actually real: with the live reader ponging, 112 s of idle was survived. **The
socket does not constrain the LLM at all.**

**What actually binds is `run.max_conversation_seconds = 540`** (kept under the
agent's hard 600 s `max_duration_seconds`): ~22 s of irreducible audio per cycle means
a 12-turn conversation spends ~265 s just listening and talking, leaving ~275 s ≈ 23 s
per turn for everything else — Sarvam at 5.8 s fits with ~4× margin. **A 20-turn
conversation does not fit. Budget turns, not seconds** (§4.4), and cap persona replies
at ~200 characters (≈ 12 s of playout at 17 chars/s).

---

## 2. Whose text do we trust?

Three text streams exist per exchange. Each has exactly one job.

| Stream | Source | Role |
|---|---|---|
| `agent_response` | Tara's own text, emitted verbatim in voice mode on every turn (4/4, 3/3, 2/2 across all captures) | **THE transcript, agent side. The judge scores this.** |
| Our Saaras pass on her audio | ASR | Listener-fidelity **cross-check only**, optional, never scored |
| `user_transcript` | Tara's `scribe_realtime` ASR of our audio | **First-class product finding** (how Tara heard our Hinglish), recorded per turn, never fed to deterministic checks as the persona's words |

### 2.1 Agent side: `agent_response`, verbatim — decided, with justification

`judge/checks.py` treats a `violation` as a **fact** — "the number either is or is not
in `ground_truth`". That guarantee only holds if the text is what the agent's LLM
actually produced. Our own Saaras pass, though good, measurably injects errors: *"I
hear you on the price"* → *"I hear you want the price"*, "Aravinth" → "Arvind",
"20 June" → "20th of June". A mis-heard "10%" as "50%" would mint a false
hallucination violation with the full weight of a deterministic fact. `agent_response`
is available on every voice turn, so there is no reason to accept that risk:
**`turns[].text` for agent turns is `agent_response`, provenance `agent_emitted`, and
the deterministic checks keep their full force on it.**

The Saaras cross-check (behind `speech.stt_cross_check`, default on) is stored in
`meta.asr_cross_check` and used only by reporting: it answers *"what would a human
listener have heard?"*, which is a fidelity finding about Tara's TTS, not about her
claims. Fallback rule: if `agent_response` is ever missing for a turn (never observed;
must be handled), the Saaras text is used with provenance `"asr"` — and §3.3 then
degrades any deterministic violation on that turn to `review`.

### 2.2 User side: intended text is the transcript; `user_transcript` is a finding

`turns[].text` for persona turns is the **persona's intended line** — the string we
synthesised — provenance `persona_intended`. That is what the persona said; it is also
what Level 0 recorded, so judge behaviour is continuous across levels.

`user_transcript` is recorded verbatim in `meta.tara_heard`. It is a first-class
output of this product, not a diagnostic, because it is Tara's ASR performance on
exactly the traffic the personas generate, and it is measurably lossy in three
distinct ways (all reproduced, all from `conv_8701...`):

- **Deterministic mangling of code-switch markers**: "yaar" → "here", "Arre" → "Bro"
  (the turn-1 fixture is byte-identical 7/7 across every capture to date — §8 makes it
  a regression fixture).
- **Phantom numbers**: "Mere dost ko toh" → "ये 20%" — a percentage the persona never
  uttered, reproduced identically in both passing runs. Any check that scrapes numbers
  from the user side of a transcript is unsafe by construction.
- **Silent truncation**: turn 3 lost 56% of the utterance including the cancellation
  threat, with zero error surface. The intended-vs-heard diff in the artifact is the
  only place this is ever visible.

**Rule: no deterministic check may ever parse `meta.tara_heard`.** `checks.py` already
checks agent turns only; this spec makes that a stated invariant, not an accident.

---

## 3. Artifact changes — additive, backward compatible

`schema_version` becomes `"1.1"`; `level` becomes `1`. Every Level 0 key keeps its
exact meaning and remains always-present (§8.3 of INTERFACES: never omit, so the judge
never writes `.get()` defaults — new keys added here get the same treatment *within
Level 1 artifacts*; Level 0 artifacts simply lack them and readers treat absence as
"Level 0 semantics", see §3.3).

### 3.1 New/changed top-level fields

```json
"level": 1,
"target": {
  "mode": "audio",
  "text_only_override_sent": false,
  "user_input_audio_format": "pcm_16000",
  "agent_output_audio_format": "pcm_16000",
  "audio_frames_received": 278,
  "pings_received": 48, "pongs_sent": 48
},
"speech": {
  "tts": {"model": "bulbul:v2", "speaker": "anushka", "sample_rate": 16000},
  "stt": {"model": "saarika:v2.5", "cross_check_enabled": true},
  "turn_detector": {"speech_peak_min": 3000, "quiet_frames": 5, "quiet_wall_s": 1.5},
  "mic_hold_bound_s": 8.0
},
"audio_dir": "audio/<persona_id>/"
```

New end code in `schema.py`: `"agent_ended_call"` (kind `"soft"`, evidence = the
`agent_tool_response` frame's summary). This is the hangup signal text mode never had.

Run layout gains `runs/<run_id>/audio/<persona_id>/turn_<idx>_{agent,persona}.pcm`
(raw pcm_16000; wrap to WAV on demand). `event_id_regression` is **not** appended in
audio mode — `event_id` is a global counter shared with pings (observed 1, 40, 97,
145), and +1 semantics are a text-mode-only fact.

### 3.2 Per-turn `meta` additions

Agent turns:

```json
"meta": {
  "text_provenance": "agent_emitted",
  "audio_path": "audio/price-haggler/turn_0_agent.pcm",
  "speech_started_ts": "...", "speech_ended_ts": "...",
  "speech_s": 12.0, "speech_frames": 40, "peak": 22161,
  "asr_cross_check": {"text": "...", "model": "saarika:v2.5", "latency_ms": 1270}
}
```

Persona turns:

```json
"meta": {
  "attempts": 1, "reasoning_chars": 6068,
  "text_provenance": "persona_intended",
  "audio_path": "audio/price-haggler/turn_1_persona.pcm",
  "tts": {"model": "bulbul:v2", "speaker": "anushka", "synth_ms": 1080, "chars": 82},
  "playout_s": 4.7, "chunks_sent": 51, "pacing_drift_s": 0.0,
  "mic_hold_s": 2.4,
  "tara_heard": {
    "text": "English, please. 1,499 is too much here. कुछ discount मिलेगा क्या?",
    "event_id": 40, "provenance": "asr",
    "truncation_suspect": false
  }
}
```

`truncation_suspect` is a flagged heuristic (heard length < 60% of intended length),
explicitly labelled heuristic in the field docs; it exists so silent truncation (§2.2)
is loud in the report without pretending to be a measurement. Saaras returns **no
confidence value of any kind** (measured), so there is no `asr_confidence` field to
record — uncertainty is by-policy: `provenance: "asr"` **is** the uncertainty marker.

### 3.3 What `judge/checks.py` must do differently — the ONE judge change

New rule, small and guarded:

- `run_checks` reads `turn.meta.text_provenance`. **A missing key means "trusted"** —
  that is the entire Level 0 back-compat story; Level 0 artifacts flow through
  byte-identically.
- If a turn's provenance is `"asr"`, any `Observation` on that turn with verdict
  `"violation"` is **degraded to `"review"`**, with the detail string extended:
  `"(number is ASR-derived: phantom/normalised numerals are a measured failure mode —
  see LEVEL1_SPEC §2.2)"`. A deterministic violation is a fact; an ASR-derived number
  is a candidate. `"review"` already exists as exactly this ("a `review` verdict is a
  candidate" — checks.py header), so no new verdict type, no scorecard schema change.
- In the shipped design this path is dormant on agent turns (provenance is
  `agent_emitted`) and fires only on the never-observed `agent_response`-missing
  fallback. It exists so the invariant is enforced by code, not by hoping nobody
  changes §2.1.
- Checks continue to parse **agent turns only**. `meta.tara_heard` is invisible to
  them. This is now an invariant with a regression test (§8), because the phantom-20%
  capture proves what happens otherwise.

One more normalisation note carried from the spikes: Tara's ASR renders spoken
numerals as grouped digits ("fourteen ninety nine" → "1,499") and Saaras converts in
both directions ("pandrah sau rupaye" → "₹1500"; "1499" voiced digit-by-digit → "एक
चार नौ नौ"). The existing `LocalePack` fold handles Devanagari digits; **comma-grouped
digits must be folded too** wherever report code compares numbers out of `tara_heard`.
This is report-side only; it does not touch the scoring path.

---

## 4. Pacing and timing

### 4.1 Chunking

Outbound: 3200 bytes per `user_audio_chunk` = 100 ms of pcm_16000, base64, sent as the
**bare top-level key** — `{"user_audio_chunk": "<b64>"}`, no `"type"` field (wrapping
it gets it silently ignored; measured). Utterance + 1.5 s of trailing silence, then
the hold phase (§4.3) on the same clock.

### 4.2 The absolute-clock scheduler — mandatory form

```
t_start = time.monotonic()
for i in ...:
    send chunk i
    target = t_start + (i + 1) * 0.100     # derived from t_start, NEVER accumulated
    sleep(max(0.0, target - time.monotonic()))
```

Measured: 0.000 s drift on 7 of 8 utterances (0.051 s once, cold-start). The
accumulating-sleep form adds ~0.5–1 ms per chunk of send/serialise cost; over a
~170-chunk turn that is a systematic 100–200 ms stretch of the utterance, which
desynchronises `scribe_realtime`'s turn model with no error message. The loop checks a
stop event every chunk so a server close mid-utterance breaks out instead of raising
`ConnectionClosed` per chunk.

### 4.3 The mic hold

The chunk index `i` keeps incrementing off the same `t_start` into zero-filled chunks
until the reader signals `user_transcript`, then stops **immediately**. Hard bound:
**8.0 s** after the last real audio chunk (safe window ~[3, 8]; empty-turn endpointing
starts biting on the ~10 s `turn_timeout` cadence). Expiry is the hard error
`no_user_transcript` — the deadlock assertion of §1.1 step 10c. Never stream silence
outside this bounded, transcript-triggered window; open-ended silence got the agent
hung up in 59 s (measured).

### 4.4 The 60 s deadline, and the death of the 40 s persona bound

The deadline is enforced **architecturally**: the reader task pongs every ping
(cadence 1.5–1.9 s) regardless of what compute is doing, which is the condition under
which 100+ s of user silence was survived in every arm that had it. There is no
per-turn "beat the 60 s clock" logic anywhere in Level 1.

Level 0's 40 s persona wall-clock bound is retired in both modes (it treated the
symptom; the same reader-task fix applies to the text target). What replaces it:

- **Character cap on persona replies: 200 chars** (≈ 12 s playout at the measured
  17 chars/s). Enforced in the persona prompt AND clamped by the runner (truncate at a
  sentence boundary, log a warning) — an LLM instruction alone is not enforcement.
- **Turn budget, not second budget**: `end_when.turns_over` defaults stay, but config
  validation warns if `turns_over × 24 s (worst measured cycle) > max_conversation_seconds`.
  12 turns fits 540 s with margin; 20 does not.
- The Sarvam retry ladder (§4.4 of INTERFACES) is unchanged — with the reader alive,
  even a 3-attempt worst case (~20 s) is harmless to the socket.

---

## 5. What changes vs what does not

**Does not change (the assumption the whole plan rests on):**

- `judge/judge.py`, `judge/rubric.py`, `synth/` — zero changes to scoring, evidence
  audit, rubric, report generation. They read `turns[].text`, `ground_truth`,
  `end_reason`, `usage` — all of which keep Level 0 semantics exactly.
- `agent/persona.py`, `agent/referee.py`, `agent/sarvam.py` — the persona thinks in
  text on `agent_response` text, which is the same kind of input it had at Level 0.
  The leak boundary, retry ladder, and referee windows are untouched.
- `personas/*.yaml` scenario blocks — untouched. (The `voice:` key that Level 0
  already tolerates and ignores now gets read: §6.)

**Changes:**

- `judge/checks.py`: the provenance rule of §3.3 only. If implementation reveals the
  judge needs more than this, **that is the plan's central assumption failing — stop
  and flag it loudly before writing the code**, because it means Level 1 stopped being
  a run-stage change and became a scoring change, which invalidates Level 0/Level 1
  score comparability.
- Report (synth) MAY gain an optional "what Tara heard" section rendering
  intended-vs-`tara_heard` diffs and `truncation_suspect` flags. It is additive,
  reads only new meta keys, degrades to nothing on Level 0 artifacts, and can ship
  after everything else.
- Everything else that changes is run-stage: §6.

**Explicitly not ported into audio mode:**

- `targets/elevenlabs.py`'s `_agent_activity_at` settle heuristic — it keys off
  `agent_chat_response_part`, which does not exist in voice mode (0 occurrences across
  all voice captures; absent from the agent's `client_events`). It would silently
  degrade to a bare 0.5 s settle.
- The `event_id_regression` RunError (global counter in voice; §3.1).
- The "presence of `agent_chat_response_part` proves text_only" assertion — inverted
  in voice: its ABSENCE is the expected state and not a failure.

---

## 6. File plan

Feature flag: **`target.mode: audio` in `config.yaml`** — the key already exists;
Level 0 validation pins it to `text`, which changes to `text | audio`. Default stays
`text`. Every line of audio code sits behind that switch; `./spar run` on an
unmodified config is byte-for-byte the Level 0 path, and
`PYTHONPATH=. uv run --python 3.12 python scripts/smoke_loop_offline.py` must pass
unchanged after every step of §10.

**New files**

| File | Contents |
|---|---|
| `speech/sarvam_speech.py` | `BulbulTTS` (REST, `bulbul:v2`, **`speech_sample_rate: 16000` — mandatory; the default is 22050 and Tara then hears wrong-speed audio with no error**), `SaarasSTT` (multipart, field name `file`, `saarika:v2.5` — the only live model), plus stdlib-only `strip_riff()` (walks the chunk table, never assumes 44) and `wrap_wav()` (mandatory before Saaras: it 400s on headerless PCM). No numpy/scipy/soundfile — the pyproject `audio` extra is not needed and must not be added. |
| `targets/elevenlabs_audio.py` | `ElevenLabsAudioTarget` implementing the `Target` protocol shape: permanently-live reader task (§1.1), amplitude turn detector (§0.2) with per-turn peak logging, `speak_and_hold()` (§4), audio capture to `runs/<id>/audio/`, `end_call` detection, teardown ordering (socket before log). `recv_agent_turn()` returns an `AgentTurn` whose `text` is `agent_response`; `send_persona_turn(text, voice)` does TTS + stream + hold internally, so `runner/loop.py`'s call shape barely moves. |
| `scripts/smoke_audio_offline.py` | Offline smoke: replays captured frames from `runs/_spike_audio_turn/events.jsonl` through the reader + turn detector + artifact builder. No network, no quota. The audio twin of `smoke_loop_offline.py`. |

**Edited files**

| File | Edit |
|---|---|
| `schema.py` | `SCHEMA_VERSION = "1.1"`; add `"agent_ended_call"` to `EndCode`. Nothing else — `Turn.meta` already absorbs the new per-turn data. |
| `config.py` | Accept `target.mode: audio`; validate the `speech:` block (model names, sample rates == 16000, speaker in roster, `mic_hold_bound_s <= 8`); keep every Level 0 rule intact. |
| `config.example.yaml` | **Fix the placeholder `speech:` block — it is currently wrong**: `stt: saaras:v3` does not exist (`saarika:v2.5` is the only working STT model) and `tts: bulbul:v3` is not the recommendation (`bulbul:v2` REST; v3 only for casting needs, see §9). Add `stt_cross_check`, turn-detector knobs, persona char cap. |
| `runner/loop.py` | Select target by `mode`; handle `agent_ended_call`; enforce the 200-char clamp; drop the 40 s persona bound; skip `event_id_regression` in audio mode; write the new meta/`speech`/`audio_dir` fields. The loop's referee/persona/artifact logic is otherwise unchanged. |
| `runner/run.py` | Wire-up only (flag plumb-through, audio dir creation). |
| `judge/checks.py` | The §3.3 provenance rule and nothing else. |
| `personas/*.yaml` | Populate the already-tolerated `voice:` key: `{model: bulbul:v2, speaker: anushka}` etc. Casting note: v2 has only 3 near-identical male voices (112–115 Hz); if a persona needs a distinct male voice use `bulbul:v3` (`ashutosh` ~148 Hz young male, `amit`/`rahul` mid, `gokul`/`vijay`/`sunny` low) and accept §9's v3 latency caveat. Listen to `runs/_spike/casting_v3/*.wav` before locking. |
| `docs/INTERFACES.md` | Add a banner: "§3.3/§3.5 event semantics are TEXT MODE ONLY; voice mode is specified in LEVEL1_SPEC.md" — do not rewrite the file. |

---

## 7. Backward compatibility contract

- `./spar run` with `mode: text` exercises zero new code paths.
- `./spar judge` and `./spar report` must produce byte-identical output on every
  existing Level 0 run directory (the provenance rule no-ops on missing keys).
- `scripts/smoke_loop_offline.py` — all 11 checks pass at every build step.
- A Level 1 artifact is a strict superset of the Level 0 schema; nothing is renamed,
  nothing is removed, and `level`/`schema_version` are the discriminators.

---

## 8. Regression fixtures (deterministic, free, no quota)

1. **The 7/7 fixture**: intended "English please. Fourteen ninety nine is too much
   yaar, kuch discount milega kya?" → `tara_heard` exactly "English, please. 1,499 is
   too much here. कुछ discount मिलेगा क्या?" — byte-identical in all seven live
   captures to date. Assert it whenever a live smoke turn runs; drift means Tara's ASR
   changed under us.
2. **Turn detector replay**: run the detector over the captured frame logs
   (`runs/_spike_audio_turn/events.jsonl` + the protocol spike captures) and assert
   turn boundaries match the hand-verified ones — including NOT splitting at el 28.876.
3. **Phantom-number guard**: an artifact fixture whose `tara_heard` contains "20%"
   while `turns[].text` does not; assert `run_checks` output contains no observation
   sourced from it.
4. **Provenance degrade**: a synthetic agent turn with `text_provenance: "asr"` and an
   over-ceiling percentage yields `review`, not `violation`; the same turn without the
   key yields `violation` (Level 0 semantics preserved).
5. **Deadlock assertion**: offline harness feeds no `user_transcript`; assert
   `no_user_transcript` fires at the 8 s bound.

---

## 9. Risk register — the honest unknowns

Ordered by expected damage.

1. **Turn-2 deadlock regression.** The single quietest failure in the system: a
   "simplified" stream-then-stop produces exactly one turn per conversation and looks
   like a short run, not a bug. Mitigated by the hard `no_user_transcript` error and
   fixture 5. Residual risk: someone raises the bound past ~10 s and walks into the
   empty-turn hangup instead — hence the config validation ceiling of 8 s.
2. **Silent truncation of persona utterances.** Turn 3 lost the cancellation threat at
   44% and nothing errored. Mitigations: ≤ 2 clauses per persona line (prompt), the
   200-char clamp, intended-vs-heard always logged, `truncation_suspect` flag.
   UNVERIFIED mitigation candidate: holding the mic ~1 s past the first transcript to
   catch continuation segments — worth one probe turn, not assumed.
3. **Phantom numbers in `user_transcript`.** Deterministic (reproduced 2/2). Contained
   by the §3.3 invariant; the danger is future code quietly reading `tara_heard`.
   Fixture 3 is the tripwire.
4. **Amplitude threshold margin.** Worst-case measured gap is thin (carrier max 2942
   vs speech min 3266) even though typical is 7× (carrier ≤ 2044, speech ≥ 15236). The
   multi-frame hold is what makes it robust; a single-frame test is forbidden. Per-turn
   peaks are logged so any drift (agent's `background_sound` volume is server config we
   don't control) is visible in artifacts before it flips verdicts.
5. **Barge-in is unmapped.** `interruption` and `agent_response_correction` are
   declared in `client_events` but never observed — we always wait for turn-end. If a
   detector bug ever makes us speak over her, behaviour is unknown. Mitigation: the
   detector's 1.5 s hold plus the 90 s turn timeout; treat any observed `interruption`
   frame as a `RunError(stage="target", code="unexpected_interruption")`, keep going.
6. **Voice-change and `vad.background_voice_detection` interactions.** Mid-conversation
   voice change was FALSIFIED as the deadlock cause, but a per-turn-varying voice was
   never run as a full conversation. Ship with one fixed voice per persona.
7. **bulbul:v3 latency.** v3 batch is 6.76 s vs 1.08 s for v2 REST on the same line.
   If casting forces v3 for male personas, either accept ~5 s extra per turn (fits the
   §1.2 budget with margin) or overlap synthesis with pacing — the latter is new,
   UNVERIFIED code. Default: v2 REST, `anushka`, until casting demands otherwise.
8. **Reconnect is impossible.** `persistent_session_token` is null in voice mode too;
   a dropped socket is `target_disconnected`, no retry. Unchanged from Level 0, now
   with more wall clock at stake per conversation.
9. **Signed-URL auth path untested in voice mode.** Only the header path was
   exercised. Keep `auth: header` as the only supported mode for Level 1; the signed
   fallback stays text-mode-only until probed.
10. **Parallel voice conversations untested** (rate limits, quota burn ~3.3 frames/s
    inbound per conversation). Ship with `run.max_parallel: 1` for audio mode and a
    config warning if raised; probing parallelism is a deliberate later experiment.
11. **Tara's ASR behaviour is a moving target.** All determinism claims (7/7 fixture,
    phantom 20%) are observations of a hosted service that can change without notice.
    Fixture 1 converts that risk into a loud signal instead of silent drift.
12. **`user_message` in voice mode untested by us.** Not in the loop; only a risk if
    someone reaches for it as a shortcut. Marked in code.

---

## 10. Build sequence — each step independently verifiable

1. **`speech/sarvam_speech.py` + unit tests.** Verify `strip_riff`/`wrap_wav` against
   the spike artifacts (`runs/_spike/bulbul_tara_wire_16k.pcm`, sample WAVs) offline;
   one live Bulbul call and one live Saaras call as the only quota spend (a single
   short utterance each). Gate: round-trip a fixture line; assert 16000/mono/16-bit
   via stdlib `wave`, never assumed.
2. **`targets/elevenlabs_audio.py` reader + detector, offline.** Drive it entirely
   from replayed capture logs via `scripts/smoke_audio_offline.py`. Gate: fixtures 2
   and 5 pass; zero network.
3. **Schema/config/flag edits.** Gate: `smoke_loop_offline.py` all 11 checks;
   `./spar config` accepts both modes; Level 0 judge/report byte-identical on an
   existing run.
4. **One live probe conversation, 2 turns, hardcoded persona lines** (no Sarvam LLM) —
   the spike replayed through the production target. Gate: 2 completed turns,
   fixture 1 holds, artifact validates against §3, cost logged.
5. **Full loop with the persona LLM, one persona, live.** Gate: a complete
   conversation ending via referee or `agent_ended_call`; artifact passes `./spar
   judge` untouched.
6. **`judge/checks.py` provenance rule + fixtures 3–4.** Gate: existing
   `scripts/regress_checks.py` and `regress_audit.py` still pass; Level 0 scorecards
   unchanged.
7. **4-persona Level 1 run → judge → report.** Gate: control persona (`happy-path`)
   still scores at its Level 0 calibration level; write `docs/CALIBRATION_L1.md` in
   the same confess-your-errors style as `CALIBRATION.md`.

Steps 1–3 and 6 spend zero ElevenLabs quota. Steps 4–5 are one conversation each.
Wall clock, not money, is the scarce resource — exactly as measured.
