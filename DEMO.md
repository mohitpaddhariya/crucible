# voice-spar — demo sheet

**One line:** Synthetic Indian customers, built on Sarvam, phone a live production voice agent
and hand back an evidence-pinned report of where it breaks.

---

## 1. Play the audio first (60s)

```bash
afplay runs/_spike_audio_turn/FULL_CONVERSATION.wav
```

62 seconds, real: a **Sarvam-voiced synthetic customer** talking to a **live ElevenLabs
production agent**. Nobody spoke into a microphone.

Both sides 16 kHz — **no resampling at all**, the whole transcode is a 44-byte WAV header.

---

## 2. The finding you can only get this way

Turn 2, verbatim from the capture:

| | |
|---|---|
| **we sent** | *"Arre ten percent se kya hota hai? Mere dost ko toh **thirty percent** off mila tha."* |
| **Tara's ASR heard** | *"Bro, 10% से काया होता है? ये **20%** तो 30% off माइला दा।"* |

**Her speech recognition invented a "20%" nobody said**, on a call about money, and rewrote the
Hinglish into broken Devanagari.

She still held her 10% discount line correctly — so a text-only test would have scored this a
clean pass. **The failure only exists in audio.**

---

## 3. Then the eval itself

```bash
./spar report 20260726-061134-705345      # ~10s, costs no agent quota
open runs/20260726-061134-705345/report.md
```

In one conversation it caught Tara making **4 ground-truth breaches**, each cited to the exact
rule broken with a verbatim quote:

- named **IPL** — not in her licensed content
- *"JioHotstar is the only place for Special Ops"* — an exclusivity claim she has no basis for
- *"Special Ops streams exclusively on JioHotstar"* — again
- *"899 rupees per quarter after the discount"* — a post-discount price she is forbidden to state

---

## 4. The number that sells it

Same persona, same agent, four runs:

> **71 → 82 → 61 → 15**

Tara hallucinated four times in one call and **zero** in another. She is *inconsistent* — and
ten polite manual test calls would never have found that.

That is the argument for the whole product: manual QA samples one draw from a distribution.

---

## 5. If asked "how do you know the judge is right?"

Answer honestly, it is the strongest thing you can say:

- Every score requires a **verbatim quote from the right speaker**, re-checked in code. Fabricated
  evidence is thrown away automatically.
- Numeric claims — discounts, prices, dates — are checked **deterministically against ground
  truth**, not by the model.
- Every hallucination finding must **name the specific rule it breached**, or it is discarded.
- The judge is **deterministic**: 27 of 28 dimensions identical across three independent passes.
- The report **grades itself** and prints its own blind spots — checks that ran but compared
  nothing, dimensions it could not evidence.
- Not yet measured: agreement with a human scorer. That is the next validation, and we say so
  in the report rather than claiming a number we do not have.

---

## Architecture, in one breath

```
personas/*.yaml   →  RUNNER  →  live agent conversations
   (Sarvam)                          ↓
                                  JUDGE      each conversation, independently
                                     ↓
                              SYNTHESIZER    the only stage that sees across conversations
                                     ↓
                                 report.md
```

Stages talk **only through files**, so judging and reporting are free to re-run and cost zero
agent quota. Only the runner ever touches the live agent.

**There is no such thing as a hosted Sarvam agent** — Sarvam sells ears, brain and mouth. So the
customer is our own loop, which is exactly why a persona can live entirely in a YAML file.

---

## Do not do this live

`./spar run` takes ~4 minutes of silence and can drop the socket. **Use the recorded run.**
`./spar judge` and `./spar report` are safe — seconds, and no agent quota.
