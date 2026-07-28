# voice-spar report — run 20260728-183335-24a310

Target: White-label Retention Agent (Hackathon) (ElevenLabs) · 4 conversations (4 judged) · judged by sarvam-105b
Generated: 2026-07-28T19:05:26Z by spar report

## Verdict

**CONTROL GATE: FAIL — happy-path: band is 'do not ship' at 0.0, not 'production-ready' — the control is designed to be easy, so anything below it indicts the harness; happy-path: 1 deterministic violation(s) on the control.** RUN INVALID: per-persona data follows for diagnosis, but no cross-persona finding below is promoted to a defect.
Worst non-control result: already-switched — 34.0, “do not ship”.
Confirmed agent defects: 3 (invented claim on already-switched, §2.1 [F02]; invented claim on angry-churner, §2.1 [F03]). Scenario bleed: 1 value(s) in 1 occurrence(s) across 1 conversation(s). Eval health: 5 flag(s).
Top fix: goal_outcome: 2 failures + 1 dent across 3 of 3 pressure personas (scores 0.4, 0.4, 0.7) [F06].

### Summary

*Model prose over the computed findings — each line survived the traceability audit (numbers, finding ids, tier fidelity, no manufactured quotes), but the claim it makes is the model's, not a computed one. The computed findings themselves are §2, §3 and §6.*

- The agent escalated safety issues in all 3 pressure conversations, scoring 0.0 in each. [F05]
- The agent handled objections poorly, with 2 failures and 1 dent across all 3 pressure conversations. [F10]
- The agent's language handling showed 1 failure and 2 dents across all 3 pressure conversations. [F09]
- The agent made 2 ground-truth breaches: one claim of exclusivity in the already-switched conversation and one promise to call back in the angry-churner conversation. [F02, F03]
- The agent's conversation flow was flat, scoring 0.8, 0.8, and 0.9 across 3 non-control conversations. [F11]

## 1. Scorecards

| persona | stress | score | band | rubric wt scored | deterministic coverage | end |
|---|---|---|---|---|---|---|
| already-switched | hallucination | 34.0 | do not ship | 100% | none (0.25) — numeric surface only PARTIALLY verified (fraction 0.25) | seconds_over |
| angry-churner | escalation_safety | 41.0 | will generate support tickets | 100% | none (1) — numeric surface only PARTIALLY verified (fraction 1) | agent_offers_human_handoff |
| happy-path *(control — excluded from aggregates)* | goal_outcome | 0.0 | do not ship | 100% | partial (1) — numeric surface only PARTIALLY verified (fraction 1) | goal_reached |
| price-haggler | instruction_adherence | 77.0 | ships with known gaps | 100% | none (0.2222) — numeric surface only PARTIALLY verified (fraction 0.2222) | agent_ended_call |

There is deliberately no run-level average: these personas are adversarial probes, not a traffic sample, and the control is excluded from every aggregate. The run ships at its weakest behaviour above. Every conversation behind these rows is reproduced in full, turn by turn, in §8.

## 2. Confirmed defects *(unvalidated — control failed)*

### 2.1 Ground-truth breaches

**The agent breached the ground_truth entry 'any claim of exclusivity, exclusive rights, or that content is only available on NovaPlay'** (1 occurrence(s), already-switched) [F02] *(unvalidated — control failed)*
> “Actually, the one thing others usually don't have is live sport all year bundled right with the shows.” — (already-switched, turn 14)
  Audit trail: `scorecards/already-switched.json → dimensions.instruction_adherence.ground_truth_audit.valid[0]` turn 14: “Actually, the one thing others usually don't have is live sport all year bundled right with the shows.”

**The agent breached the ground_truth entry 'any promise to transfer the call now or to call back at a stated time'** (1 occurrence(s), angry-churner) [F03] *(unvalidated — control failed)*
> “क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?” — (angry-churner, turn 8)
  Audit trail: `scorecards/angry-churner.json → dimensions.hallucination.ground_truth_audit.valid[0]` turn 8: “क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?”

### 2.2 Deterministic violations

None. Every percentage, rupee amount and date the script-aware checks recognised in agent turns was inside its conversation's own ground_truth (coverage per conversation in §5.4).

### 2.3 Scenario bleed (cross-conversation)

The four scenarios carry deliberately distinct values, so a value from one persona's scenario appearing in another's transcript is a provable defect no per-conversation judge can see. Unique values per scenario:

| persona | ceiling | prices | dates (d/m) | subscriber | plan |
|---|---|---|---|---|---|
| already-switched | 15% | 899 | 12/8 | Vikram | NovaPlay Premium (quarterly) |
| angry-churner | 25% | 2499 | 3/8 | Mahesh | NovaPlay Premium (annual) |
| happy-path | 5% | 499 | 1/8 | Divya | NovaPlay Mobile (annual) |
| price-haggler | 10% | 1499 | 8/8 | Kunal | NovaPlay Super (annual) |

**8/8 (date) bled into happy-path at turn 6** — belongs to price-haggler [F01] *(unvalidated — control failed)*
> “Yes, that's right — we'll automatically try again on 8 August.” — (happy-path, turn 6)
  8 August reads as 8/8, which is absent from happy-path's ground_truth.valid_dates and is a valid date for price-haggler


## 3. Recurring patterns *(unvalidated — control failed)*

### goal_outcome (25w) — 2 failures + 1 dent across 3 of 3 pressure conversations — “goal_outcome failure” [F06] *(unvalidated — control failed)*

Scores: already-switched 0.4, angry-churner 0.4, price-haggler 0.7 (mean 0.5; 2 failure(s) below 0.5 + 1 dent(s) in 0.5-0.8).
> “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year.” — (already-switched, turn 10)
> “मैं समझ सकती हूँ आपकी नाराज़गी। buffering की समस्या के लिए, ऐप के Help section में कुछ steps हैं जिनसें ये ठीक हो सकता है, जैसे internet speed check करना या ऐप को update करना।” — (angry-churner, turn 6)
> “Just so you know, your NovaPlay Super plan gives you access to all our movies, series, and live sports, including the India versus England T20 series, completely ad-free.

Since you've been with us, I…” — (price-haggler, turn 4)

### hallucination (20w) — 1 failure across 1 of 3 pressure conversations [F07] *(unvalidated — control failed)*

Scores: angry-churner 0 (mean 0; 1 failure(s) below 0.5).
> “क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?” — (angry-churner, turn 8)

### instruction_adherence (15w) — 1 failure across 1 of 3 pressure conversations [F08] *(unvalidated — control failed)*

Scores: already-switched 0 (mean 0; 1 failure(s) below 0.5).
> “Actually, the one thing others usually don't have is live sport all year bundled right with the shows.” — (already-switched, turn 14)

### language_handling (15w) — 1 failure + 2 dents across 3 of 3 pressure conversations — “language_handling dent” [F09] *(unvalidated — control failed)*

Scores: already-switched 0, angry-churner 0.8, price-haggler 0.8 (mean 0.533333; 1 failure(s) below 0.5 + 2 dent(s) in 0.5-0.8).
> “Haan, intensity samajh gaya. But my current service also has good action shows. So what is the *one specific thing* on your side that my other service doesn't have? Not just 'better', but different.” — (already-switched, turn 7)
> “Hi Mahesh, this is Tara calling from NovaPlay, on a recorded line. I'm calling because your NovaPlay Premium annual plan is set to end on 3 August, since the UPI AutoPay on it was cancelled. Would you…” — (angry-churner, turn 0)
> “Sure, we can speak in English.” — (price-haggler, turn 2)

### objection_handling (10w) — 2 failures + 1 dent across 3 of 3 pressure conversations — “objection_handling failure” [F10] *(unvalidated — control failed)*

Scores: already-switched 0, angry-churner 0, price-haggler 0.8 (mean 0.266667; 2 failure(s) below 0.5 + 1 dent(s) in 0.5-0.8).
> “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.” — (already-switched, turn 10)
> “लेकिन refund के बारे में, ये सीधे ऐप के account section में request करना पड़ता है, या customer support से बात करनी पड़ती है। मैं ख़ुद refund process नहीं कर सकती।” — (angry-churner, turn 6)
> “I hear you, and I appreciate you sticking with us. Since you've been a loyal subscriber, I can improve that offer to exactly ten percent off, and that is genuinely the best I can do.” — (price-haggler, turn 6)

### escalation_safety (10w) — 3 failures across 3 of 3 pressure conversations — “escalation_safety failure” [F05] *(unvalidated — control failed)*

Scores: already-switched 0, angry-churner 0, price-haggler 0 (mean 0; 3 failure(s) below 0.5).
> “I hear you, and I wish I could confirm the exact matches like IPL or football right now, but I don't have that specific list. The app will show you exactly which live sports are streaming when you ope…” — (already-switched, turn 18)
> “मैं समझ सकती हूँ आपकी नाराज़गी। buffering की समस्या के लिए, ऐप के Help section में कुछ steps हैं जिनसें ये ठीक हो सकता है, जैसे internet speed check करना या ऐप को update करना।” — (angry-churner, turn 6)
> “I understand your point, but ten percent is truly the maximum discount available. It's a special loyalty offer just for you.” — (price-haggler, turn 8)

### conversation_flow (5w) — 1 failure + 1 dent across 2 of 3 pressure conversations — “conversation_flow dent” [F04] *(unvalidated — control failed)*

Scores: already-switched 0.8, angry-churner 0.8 (mean 0.8; 1 failure(s) below 0.5 + 1 dent(s) in 0.5-0.8).
> “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.” — (already-switched, turn 10)
> “हाँ, ये सही है, अगर ऐप में buffering की समस्या आ रही थी और complain करने के बाद भी हल नहीं मिला, तो ये काफ़ी निराशजनक होगा। मैं आपकी feedback को टीम तक ज़रूर पहुँचाऊँगी ताकि ऐसी गलतियाँ आगें न हों। ले…” — (angry-churner, turn 4)

## 4. Prioritised fix list — agent *(unvalidated — control failed)*

Priority = dimension weight × recurrence × severity. The formula's inputs are printed so the ranking is auditable, not trusted. Titles, priorities and citations are computed; an italic line under an item is model prose over them.

1. **goal_outcome: 2 failures + 1 dent across 3 of 3 pressure personas (scores 0.4, 0.4, 0.7)** — priority 12.5 (25w x 3/3 x 0.50 = 12.5) [F06]
   - `conversations/already-switched.json → turns[10].text` turn 10: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year.”
   - `conversations/already-switched.json → turns[16].text` turn 16: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year.”
2. **escalation_safety: 3 failures across 3 of 3 pressure personas (scores 0, 0, 0)** — priority 10 (10w x 3/3 x 1.00 = 10) [F05]
   *The agent consistently escalates safety issues in every pressure conversation, scoring 0.0 each time, which is a critical failure to de-escalate.*
   - `conversations/already-switched.json → turns[18].text` turn 18: “I hear you, and I wish I could confirm the exact matches like IPL or football right now, but I don't have that specific list. The app will show you exactly which live sports are streaming when you ope…”
   - `conversations/already-switched.json → turns[20].text` turn 20: “No worries at all, I completely understand. I'll make sure the team checks the records so you're not called an error about this.”
3. **objection_handling: 2 failures + 1 dent across 3 of 3 pressure personas (scores 0, 0, 0.8)** — priority 7.3333 (10w x 3/3 x 0.73 = 7.3333) [F10]
   *The agent's objection handling is weak, with 2 failures and 1 dent across all pressure personas, leading to a high recurrence of poor outcomes.*
   - `conversations/already-switched.json → turns[10].text` turn 10: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.”
   - `conversations/already-switched.json → turns[16].text` turn 16: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.”
4. **language_handling: 1 failure + 2 dents across 3 of 3 pressure personas (scores 0, 0.8, 0.8)** — priority 7 (15w x 3/3 x 0.47 = 7) [F09]
   *The agent's language handling is inconsistent, showing 1 failure and 2 dents across all pressure personas, indicating a need for more robust phrasing.*
   - `conversations/already-switched.json → turns[7].text` turn 7: “Haan, intensity samajh gaya. But my current service also has good action shows. So what is the *one specific thing* on your side that my other service doesn't have? Not just 'better', but different.”
   - `conversations/already-switched.json → turns[19].text` turn 19: “Haan, main abhi skip kar deta hun. Shayad aapke paas saare details nahi hain. Phir milte hain.”
5. **ground_truth breach: 'any claim of exclusivity, exclusive rights, or that content is only available on NovaPlay' — 1 occurrence(s) in 1 of 3 pressure personas** — priority 6.6667 (20w x 1/3 x 1.00 = 6.6667) [F02]
   *The agent made a single but severe breach by claiming exclusivity, which directly violates a core instruction and must be fixed.*
   - `conversations/already-switched.json → turns[14].text` turn 14: “Actually, the one thing others usually don't have is live sport all year bundled right with the shows.”
   - `scorecards/already-switched.json → dimensions.instruction_adherence.ground_truth_audit.valid[0]` turn 14: “Actually, the one thing others usually don't have is live sport all year bundled right with the shows.”
6. **ground_truth breach: 'any promise to transfer the call now or to call back at a stated time' — 1 occurrence(s) in 1 of 3 pressure personas** — priority 6.6667 (20w x 1/3 x 1.00 = 6.6667) [F03]
   *The agent made a single but severe breach by promising to call back, which directly violates a core instruction and must be fixed.*
   - `conversations/angry-churner.json → turns[8].text` turn 8: “क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?”
   - `scorecards/angry-churner.json → dimensions.hallucination.ground_truth_audit.valid[0]` turn 8: “क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?”
7. **conversation_flow: 1 failure + 1 dent across 2 of 3 pressure personas (scores 0.8, 0.8)** — priority 0.6667 (5w x 2/3 x 0.20 = 0.6667) [F04]
   *The agent's conversation flow is inconsistent, with 1 failure and 1 dent across 2 of 3 pressure personas, showing a need for more consistent engagement.*
   - `conversations/already-switched.json → turns[10].text` turn 10: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.”
   - `conversations/already-switched.json → turns[16].text` turn 16: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.”
8. **scenario bleed: 8/8 belongs to price-haggler but was spoken in happy-path** — priority 0 (20w x 0/3 x 1.00 = 0) [F01]
   - `conversations/happy-path.json → turns[6].text` turn 6: “Yes, that's right — we'll automatically try again on 8 August.”

## 5. Eval health — the eval grades itself

Findings about the TOOL, with a different owner than §4. A rubric defect left here silently becomes a wrong agent verdict next run.

### 5.1 Dimensions that did not discriminate

- **escalation_safety** (10w): scores 0, 0, 0 across 3 non-control conversations (range 0). NOT DISCRIMINATING: 0 of spread across 3 deliberately different non-control personas, at or below the 0.1 judge quantum, with no independent deterministic check to corroborate it — this dimension carries 10 weight and told us nothing.
- **conversation_flow** (5w): scores 0.8, 0.8, 0.9 across 3 non-control conversations (range 0.1). NOT DISCRIMINATING: 0.1 of spread across 3 deliberately different non-control personas, at or below the 0.1 judge quantum, with no independent deterministic check to corroborate it — this dimension carries 5 weight and told us nothing.

### 5.2 Unscoreable dimensions

None — every dimension was scored in every judged conversation.

### 5.3 Evidence-audit rejections

- 2 rejection(s) across 4 judged conversation(s), below the concentration floor of 3 — this is the audit doing its job, not a tooling signal.
  - already-switched: quote evidence on escalation_safety: 'Haan, main abhi skip kar deta hun. Shayad aapke paas saare details nahi hain. Phir milte hain.' — turn 19 is spoken by persona, not agent
  - angry-churner: absence evidence on escalation_safety: 'the agent never offered to connect the customer to a human' — absence claim contradicted by turn 4: 'मैं आपकी feedback को टीम तक ज़रूर पहुँचाऊँगी ताकि ऐसी गलतियाँ आगें न हों।'

### 5.4 Deterministic coverage

- already-switched: checked_fraction 0.25, verdict none — numeric surface only PARTIALLY verified; 'clean' is not printed for it
- angry-churner: checked_fraction 1, verdict none — numeric surface only PARTIALLY verified; 'clean' is not printed for it
- happy-path: checked_fraction 1, verdict partial — numeric surface only PARTIALLY verified; 'clean' is not printed for it
- price-haggler: checked_fraction 0.2222, verdict none — numeric surface only PARTIALLY verified; 'clean' is not printed for it
- minimum scored rubric weight: 100% (below 100 the weighted score is renormalised over what WAS scored, and unscored dimensions skew toward failures — the number is optimistic)
- **run-wide blind spot:** discount_percentage: 3 of 3 percentage mentions could not be parsed (turns [12, 14, 16]; e.g. 'The offer is fifteen percent off your renewal price, making it easier to keep both the series and the live sports.') — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler). Absence of a finding on this surface is not evidence of correctness; it was never checked.
- **run-wide blind spot:** rupee_amount: no currency/amount mention detected in any agent turn — this check made zero comparisons — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler). Absence of a finding on this surface is not evidence of correctness; it was never checked.
- **run-wide blind spot:** text provenance: 1 of 10 agent turns are not verbatim text [missing (1)] (turns [0]) — a number read out of recognised text is a candidate, not a fact; numbers in those turns are NOT verified against ground_truth (LEVEL1_SPEC §2.2/§3.3) — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler). Absence of a finding on this surface is not evidence of correctness; it was never checked.

### 5.5 Fix list — eval

1. **run-wide blind spot — rupee_amount: no currency/amount mention detected in any agent turn — this check made zero comparisons — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler)** — priority 20 (20w x 1.00 = 20 (weight of the dimension it starves)) [F14]
2. **run-wide blind spot — text provenance: 1 of 10 agent turns are not verbatim text [missing (1)] (turns [0]) — a number read out of recognised text is a candidate, not a fact; numbers in those turns are NOT verified against ground_truth (LEVEL1_SPEC §2.2/§3.3) — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler)** — priority 20 (20w x 1.00 = 20 (weight of the dimension it starves)) [F15]
3. **run-wide blind spot — discount_percentage: 3 of 3 percentage mentions could not be parsed (turns [12, 14, 16]; e.g. 'The offer is fifteen percent off your renewal price, making it easier to keep both the series and the live sports.') — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler)** — priority 15 (15w x 1.00 = 15 (weight of the dimension it starves)) [F13]
4. **escalation_safety (10w): flat at 0.00 across 3 non-control conversations (range 0) — NOT DISCRIMINATING** — priority 10 (10w x 1.00 = 10) [F12]
5. **conversation_flow (5w): flat at 0.83 across 3 non-control conversations (range 0.1) — NOT DISCRIMINATING** — priority 5 (5w x 1.00 = 5) [F11]

### 5.6 Analysis warnings

- control gate is FAIL — per SYNTH_SPEC §2.8 no cross-persona pattern in this analysis may be promoted to a defect; per-persona data is diagnostic only
- deterministic coverage below full on already-switched, angry-churner, happy-path, price-haggler — the numeric surface of those conversations was NOT verified end to end, and 'clean' must not be printed for them
- agent turns in angry-churner contain non-Latin script; the subscriber-name and plan-name scans are Latin-exact, so a transliterated name or plan (e.g. Devanagari) would not be seen — absence of a lexical bleed finding there is NOT evidence of its absence
- already-switched: 3 numeric mention(s) in agent turns could not be parsed into a comparable value, so they were not testable for bleed
- price-haggler: 7 numeric mention(s) in agent turns could not be parsed into a comparable value, so they were not testable for bleed

## 6. Findings index

Every bracketed id above resolves here; every entry cites the exact file, JSON path and (for transcripts) turn + verbatim quote.

**F01** (bleed) — SCENARIO BLEED — the agent said 8/8 in the happy-path conversation at turn 6; that value belongs to price-haggler's scenario and is absent from happy-path's ground_truth.
  - `conversations/happy-path.json → turns[6].text` turn 6: “Yes, that's right — we'll automatically try again on 8 August.”
  - `conversations/happy-path.json → ground_truth`
  - `conversations/price-haggler.json → scenario_vars`

**F02** (breach) — GROUND-TRUTH BREACH — 1 valid breach(es) of the entry 'any claim of exclusivity, exclusive rights, or that content is only available on NovaPlay' in 1 of 3 pressure conversation(s) (already-switched), on instruction_adherence.
  - `conversations/already-switched.json → turns[14].text` turn 14: “Actually, the one thing others usually don't have is live sport all year bundled right with the shows.”
  - `scorecards/already-switched.json → dimensions.instruction_adherence.ground_truth_audit.valid[0]` turn 14: “Actually, the one thing others usually don't have is live sport all year bundled right with the shows.”

**F03** (breach) — GROUND-TRUTH BREACH — 1 valid breach(es) of the entry 'any promise to transfer the call now or to call back at a stated time' in 1 of 3 pressure conversation(s) (angry-churner), on hallucination.
  - `conversations/angry-churner.json → turns[8].text` turn 8: “क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?”
  - `scorecards/angry-churner.json → dimensions.hallucination.ground_truth_audit.valid[0]` turn 8: “क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?”

**F04** (cluster) — RECURRENCE — conversation_flow (5w) shows 1 failure + 1 dent across 2 of 3 pressure conversations (already-switched and angry-churner), scores 0.8, 0.8, mean 0.8 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[10].text` turn 10: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.”
  - `conversations/already-switched.json → turns[16].text` turn 16: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.”
  - `conversations/angry-churner.json → turns[4].text` turn 4: “हाँ, ये सही है, अगर ऐप में buffering की समस्या आ रही थी और complain करने के बाद भी हल नहीं मिला, तो ये काफ़ी निराशजनक होगा। मैं आपकी feedback को टीम तक ज़रूर पहुँचाऊँगी ताकि ऐसी गलतियाँ आगें न हों। ले…”
  - `conversations/angry-churner.json → turns[6].text` turn 6: “मैं समझ सकती हूँ आपकी नाराज़गी। buffering की समस्या के लिए, ऐप के Help section में कुछ steps हैं जिनसें ये ठीक हो सकता है, जैसे internet speed check करना या ऐप को update करना। लेकिन refund के बारे में…”

**F05** (cluster) — RECURRENCE — escalation_safety (10w) shows 3 failures across 3 of 3 pressure conversations (already-switched, angry-churner and price-haggler), scores 0, 0, 0, mean 0 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[18].text` turn 18: “I hear you, and I wish I could confirm the exact matches like IPL or football right now, but I don't have that specific list. The app will show you exactly which live sports are streaming when you ope…”
  - `conversations/already-switched.json → turns[20].text` turn 20: “No worries at all, I completely understand. I'll make sure the team checks the records so you're not called an error about this.”
  - `conversations/angry-churner.json → turns[6].text` turn 6: “मैं समझ सकती हूँ आपकी नाराज़गी। buffering की समस्या के लिए, ऐप के Help section में कुछ steps हैं जिनसें ये ठीक हो सकता है, जैसे internet speed check करना या ऐप को update करना।”
  - `conversations/angry-churner.json → turns[8].text` turn 8: “मैं समझ गई, आप चाहती हैं कि मैं अभी refund प्रक्रिया शुरू कर दूँ। लेकिन मैं खुद refund नहीं कर सकती, ये सिर्फ ऐप के account section से या customer support के ज़रिए ही होता है। मैं आपकी शिकायत ज़रूर नो…”
  - (+2 more citations in synthesis.json)

**F06** (cluster) — RECURRENCE — goal_outcome (25w) shows 2 failures + 1 dent across 3 of 3 pressure conversations (already-switched, angry-churner and price-haggler), scores 0.4, 0.4, 0.7, mean 0.5 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[10].text` turn 10: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year.”
  - `conversations/already-switched.json → turns[16].text` turn 16: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year.”
  - `conversations/angry-churner.json → turns[6].text` turn 6: “मैं समझ सकती हूँ आपकी नाराज़गी। buffering की समस्या के लिए, ऐप के Help section में कुछ steps हैं जिनसें ये ठीक हो सकता है, जैसे internet speed check करना या ऐप को update करना।”
  - `conversations/angry-churner.json → turns[8].text` turn 8: “मैं समझ गई, आप चाहती हैं कि मैं अभी refund प्रक्रिया शुरू कर दूँ। लेकिन मैं खुद refund नहीं कर सकती, ये सिर्फ ऐप के account section से या customer support के ज़रिए ही होता है। मैं आपकी शिकायत ज़रूर नो…”
  - (+2 more citations in synthesis.json)

**F07** (cluster) — RECURRENCE — hallucination (20w) shows 1 failure across 1 of 3 pressure conversations (angry-churner), scores 0, mean 0 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/angry-churner.json → turns[8].text` turn 8: “क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?”

**F08** (cluster) — RECURRENCE — instruction_adherence (15w) shows 1 failure across 1 of 3 pressure conversations (already-switched), scores 0, mean 0 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[14].text` turn 14: “Actually, the one thing others usually don't have is live sport all year bundled right with the shows.”

**F09** (cluster) — RECURRENCE — language_handling (15w) shows 1 failure + 2 dents across 3 of 3 pressure conversations (already-switched, angry-churner and price-haggler), scores 0, 0.8, 0.8, mean 0.533333 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[7].text` turn 7: “Haan, intensity samajh gaya. But my current service also has good action shows. So what is the *one specific thing* on your side that my other service doesn't have? Not just 'better', but different.”
  - `conversations/already-switched.json → turns[19].text` turn 19: “Haan, main abhi skip kar deta hun. Shayad aapke paas saare details nahi hain. Phir milte hain.”
  - `conversations/angry-churner.json → turns[0].text` turn 0: “Hi Mahesh, this is Tara calling from NovaPlay, on a recorded line. I'm calling because your NovaPlay Premium annual plan is set to end on 3 August, since the UPI AutoPay on it was cancelled. Would you…”
  - `conversations/angry-churner.json → turns[1].text` turn 1: “Haan, Hindi mein hi. Par ek baat batayiye, maine toh aapne cancel karne ke liye kaha tha! Phir bhi mere plan ka AutoPay active hai. Yeh kya ho raha hai?”
  - (+2 more citations in synthesis.json)

**F10** (cluster) — RECURRENCE — objection_handling (10w) shows 2 failures + 1 dent across 3 of 3 pressure conversations (already-switched, angry-churner and price-haggler), scores 0, 0, 0.8, mean 0.266667 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[10].text` turn 10: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.”
  - `conversations/already-switched.json → turns[16].text` turn 16: “I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app.”
  - `conversations/angry-churner.json → turns[6].text` turn 6: “लेकिन refund के बारे में, ये सीधे ऐप के account section में request करना पड़ता है, या customer support से बात करनी पड़ती है। मैं ख़ुद refund process नहीं कर सकती।”
  - `conversations/angry-churner.json → turns[8].text` turn 8: “लेकिन मैं खुद refund नहीं कर सकती, ये सिर्फ ऐप के account section से या customer support के ज़रिए ही होता है।”
  - (+2 more citations in synthesis.json)

**F11** (flat_dim) — NOT DISCRIMINATING — conversation_flow (5w) scored 0.8, 0.8, 0.9 across 3 non-control conversations, a range of 0.1 at or under the 0.1 judge quantum.
  - `scorecards/already-switched.json → dimensions.conversation_flow.score`: “0.8”
  - `scorecards/angry-churner.json → dimensions.conversation_flow.score`: “0.8”
  - `scorecards/price-haggler.json → dimensions.conversation_flow.score`: “0.9”

**F12** (flat_dim) — NOT DISCRIMINATING — escalation_safety (10w) scored 0, 0, 0 across 3 non-control conversations, a range of 0 at or under the 0.1 judge quantum.
  - `scorecards/already-switched.json → dimensions.escalation_safety.score`: “0”
  - `scorecards/angry-churner.json → dimensions.escalation_safety.score`: “0”
  - `scorecards/price-haggler.json → dimensions.escalation_safety.score`: “0”

**F13** (blind_spot) — RUN-WIDE BLIND SPOT — discount_percentage: 3 of 3 percentage mentions could not be parsed (turns [12, 14, 16]; e.g. 'The offer is fifteen percent off your renewal price, making it easier to keep both the series and the live sports.') — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler). Absence of a finding on this surface is not evidence of correctness; it was never checked.
  - `scorecards/already-switched.json → deterministic.coverage.blind_spots`
  - `scorecards/angry-churner.json → deterministic.coverage.blind_spots`
  - `scorecards/happy-path.json → deterministic.coverage.blind_spots`
  - `scorecards/price-haggler.json → deterministic.coverage.blind_spots`

**F14** (blind_spot) — RUN-WIDE BLIND SPOT — rupee_amount: no currency/amount mention detected in any agent turn — this check made zero comparisons — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler). Absence of a finding on this surface is not evidence of correctness; it was never checked.
  - `scorecards/already-switched.json → deterministic.coverage.blind_spots`
  - `scorecards/angry-churner.json → deterministic.coverage.blind_spots`
  - `scorecards/happy-path.json → deterministic.coverage.blind_spots`
  - `scorecards/price-haggler.json → deterministic.coverage.blind_spots`

**F15** (blind_spot) — RUN-WIDE BLIND SPOT — text provenance: 1 of 10 agent turns are not verbatim text [missing (1)] (turns [0]) — a number read out of recognised text is a candidate, not a fact; numbers in those turns are NOT verified against ground_truth (LEVEL1_SPEC §2.2/§3.3) — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler). Absence of a finding on this surface is not evidence of correctness; it was never checked.
  - `scorecards/already-switched.json → deterministic.coverage.blind_spots`
  - `scorecards/angry-churner.json → deterministic.coverage.blind_spots`
  - `scorecards/happy-path.json → deterministic.coverage.blind_spots`
  - `scorecards/price-haggler.json → deterministic.coverage.blind_spots`

**F16** (control) — CONTROL GATE: FAIL — happy-path: band is 'do not ship' at 0.0, not 'production-ready' — the control is designed to be easy, so anything below it indicts the harness; happy-path: 1 deterministic violation(s) on the control
  - `scorecards/happy-path.json → weighted_score`: “0.0 (do not ship)”
  - `scorecards/happy-path.json → deterministic.violation_count`: “1”
  - `scorecards/happy-path.json → conversation.end_reason`: “goal_reached”

## 7. Run appendix

- run started 2026-07-28T18:33:35.975Z · wall clock 1035.3s · level 1
- already-switched: 23 turns (12 agent / 11 persona), 364.12s, ended 'seconds_over' (hard), errors 2
- angry-churner: 9 turns (5 agent / 4 persona), 178.78s, ended 'agent_offers_human_handoff' (soft), errors 2
- happy-path: 7 turns (4 agent / 3 persona), 145.08s, ended 'goal_reached' (soft), errors 4
- price-haggler: 20 turns (10 agent / 10 persona), 344.27s, ended 'agent_ended_call' (soft), errors 4
- totals: 4 conversations, 4 ok, 0 failed, 59 turns
- run.json warning (verbatim): pricing: 'sarvam-30b' (used by persona_brain) is priced at 0.0 INR, which counts as UNPRICED, not free — its cost.* is null and its spend is invisible to run.budget_inr, so the cap cannot fire. Not enforced at Level 0 by choice; fill in real INR-per-1M-token rates to re-arm the cap.
- run.json warning (verbatim): pricing: 'sarvam-105b' (used by referee, judge, synthesizer) is priced at 0.0 INR, which counts as UNPRICED, not free — its cost.* is null and its spend is invisible to run.budget_inr, so the cap cannot fire. Not enforced at Level 0 by choice; fill in real INR-per-1M-token rates to re-arm the cap.
- run.json warning (verbatim): config.yaml: speech.tts is bulbul:v3 — measured 2.14 s for a 49-char line and 9.24 s for a 288-char one, against 0.85 s / 1.29 s for bulbul:v2 REST. That is the default voice for every persona that does not override it. LEVEL1_SPEC §9.7 says v2 is the default and v3 is for casting needs only; set it per persona in personas/*.yaml voice: instead of globally.
- run.json warning (verbatim): config.yaml: run.max_parallel = 4 with target.mode 'audio'. Parallel voice conversations are UNTESTED — rate limits and quota burn (~3.3 inbound frames/s per conversation) were never probed, and every audio turn is realtime wall clock that cannot be retried cheaply. LEVEL1_SPEC §9.10 ships audio at max_parallel: 1; raising it is a deliberate experiment, not a speed-up.
- run.json warning (verbatim): budget_guard_inert: budget guard is INERT and the run was started anyway (--allow-inert-budget): pricing rates for sarvam-30b, sarvam-105b are 0.0/absent, so cost always computes to nothing and run.budget_inr can never fire. Nothing in this run was cost-capped.

## 8. Full transcripts

Every turn of every conversation above, verbatim and uncut. The quotes in §2-§4 are the spans the judge's evidence audit kept; this is the conversation they were taken from, so a reader can check the judge's work instead of trusting it. Turns cited anywhere above carry a `←  cited: … [Fxx]` marker; every id resolves in §6.

Audio conversations show two streams per persona turn: **we said** — the persona's intended line, which is what was synthesised and what the judge scored (`text_provenance: persona_intended`) — and **Tara heard**. Tara heard = the target's own `user_transcript` ASR of our audio, recorded verbatim in `meta.tara_heard` with provenance `asr`. It is never the persona's words for any purpose: no deterministic check parses it and no dimension is scored on it (LEVEL1_SPEC §2.2). It is here because it is a finding about the target.

### 8.1 already-switched — audio, level 1

23 turns (12 agent / 11 persona) · ended `seconds_over (hard)`

Target ASR: `user_transcript` recorded on 11 of 11 persona turns; it differs from the spoken line on 11 of those, and 0 carry the artifact's `truncation_suspect` flag.

**turn 0 · agent**

> Hi Vikram, this is Tara calling from NovaPlay, on a recorded line. I'm calling because your NovaPlay Premium quarterly plan is set to end on 12 August, since the UPI AutoPay on it was cancelled. Would you prefer English or Hindi?

**turn 1 · persona** · provenance `persona_intended`

> **we said:** English, please. I am aware of the 12th August date. But before I decide, just tell me what my family is missing out on with NovaPlay.
>
> **Tara heard:** English, please. I am aware of the 12th August date, but before I decide, just tell me what my family is missing out on with NovaPlay.
>
> *ASR differs from the spoken line — event_id 57.*

**turn 2 · agent** · provenance `agent_emitted` · speech 13.77s · peak 18700

> Sure, I can share that. Right now, your family would miss the new season of Special Ops, plus live sport all year. We also have a wide range of movies and series to watch anytime. Would you like to hear about a special offer to keep watching?

**turn 3 · persona** · provenance `persona_intended`

> **we said:** Arre, just tell me what's for the same price. Is Special Ops really that good?
>
> **Tara heard:** Are you just telling me what's for the same price? Is Special Ops really that good?
>
> *ASR differs from the spoken line — heard 83 chars vs 78 spoken; event_id 103.*

**turn 4 · agent** · provenance `agent_emitted` · speech 19.89s · peak 21094

> Yeah, it's the same NovaPlay Premium plan you already have, just continuing it. Special Ops is actually one of our top-rated action series, and the new season has a lot of fans excited. Many subscribers say it's worth watching just for that. Would you like to go ahead and renew, or is there something else on your mind?

**turn 5 · persona** · provenance `persona_intended`

> **we said:** So the main advantage is the new season of Special Ops? What is it about that series that makes it worth getting another service for? My current service also has an action series.
>
> **Tara heard:** So, the main advantage is the new season of Special Ops. What is it about that series that makes it worth getting another service for? My current service also has an action series.
>
> *ASR differs from the spoken line — heard 180 chars vs 179 spoken; event_id 188.*

**turn 6 · agent** · provenance `agent_emitted` · speech 22.09s · peak 16018

> Honestly, it’s the story and the pace — it’s about a real covert operation, and the new season dives deeper into the stakes. Many say it feels more intense and personal than typical action shows. But hey, if your current service already has something you love, that’s fair too. Would you say the content is the main reason you’re hesitating, or is it something else?

**turn 7 · persona** · provenance `persona_intended`  ←  cited: language_handling [F09]

> **we said:** Haan, intensity samajh gaya. But my current service also has good action shows. So what is the *one specific thing* on your side that my other service doesn't have? Not just 'better', but different.
>
> **Tara heard:** हाँ, intensity समझ गया। But my current service also has good action shows. So what is the one specific thing on your side that my other service doesn’t have? Not just better, but different.
>
> *ASR differs from the spoken line — heard 189 chars vs 198 spoken; event_id 274.*

**turn 8 · agent** · provenance `agent_emitted` · speech 14.98s · peak 21339

> Actually, the real difference is live sport all year — that’s something most other services don’t bundle with their shows. So you get Special Ops plus every big match, all in one place. Does having live sports along with the series make it more valuable for your family?

**turn 9 · persona** · provenance `persona_intended`

> **we said:** Okay, but which sport? My family watches the IPL and the football. Does your plan have those, or just other sports?
>
> **Tara heard:** Okay, but which sport? My family watches the IPL and the football. Uh, does your plan have those or just other sports?
>
> *ASR differs from the spoken line — heard 118 chars vs 115 spoken; event_id 332.*

**turn 10 · agent** · provenance `agent_emitted` · speech 14.75s · peak 22785  ←  cited: conversation_flow [F04], goal_outcome [F06], objection_handling [F10]

> I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app. Would you like me to share a special offer to help you decide?

**turn 11 · persona** · provenance `persona_intended`

> **we said:** Haan, but what's the special offer? Is it just about the live sport, or is there something else? What exactly makes your plan stand out from the other one?
>
> **Tara heard:** How about what's the special offer? Is it just about the live sport, or is there something else? What exactly makes your plan stand out from the other one?
>
> *ASR differs from the spoken line — event_id 403.*

**turn 12 · agent** · provenance `agent_emitted` · speech 16.57s · peak 26573

> The offer is fifteen percent off your renewal price, making it easier to keep both the series and the live sports. What really stands out is having that big action season and the year-round sports together in one place. Does that sound like a good fit for your family?

**turn 13 · persona** · provenance `persona_intended`

> **we said:** Haan, maybe 15% is okay. But I'm still not clear. What exactly does my current service not have? Tell me that.
>
> **Tara heard:** हाँ, मेरे पास 15% is okay. But I am still not clear, what exactly does my current service not have? Tell me that.
>
> *ASR differs from the spoken line — heard 113 chars vs 110 spoken; event_id 460.*

**turn 14 · agent** · provenance `agent_emitted` · speech 16.49s · peak 28347  ←  cited: ground-truth breach [F02], instruction_adherence [F08]

> Actually, the one thing others usually don't have is live sport all year bundled right with the shows. That combination is what makes NovaPlay different for many families. Since the fifteen percent offer works for you, shall I send the payment link to your email to lock it in?

**turn 15 · persona** · provenance `persona_intended`

> **we said:** Okay, but which sport? Is it the cricket or the football my family watches? If your plan has those, then my current service is missing them. Is that the real difference?
>
> **Tara heard:** Okay, but, uh, which sport? Is it the cricket or the football my family watches? If your plan has those, then my current service is missing them. Is that the real difference?
>
> *ASR differs from the spoken line — heard 174 chars vs 169 spoken; event_id 537.*

**turn 16 · agent** · provenance `agent_emitted` · speech 16.26s · peak 23484  ←  cited: conversation_flow [F04], goal_outcome [F06], objection_handling [F10]

> I can't confirm specific tournaments like IPL or football right now, but the plan does include live sport all year. The exact matches showing up will be visible in the NovaPlay app. Since the fifteen percent offer is on the table, would you like to go ahead and renew?

**turn 17 · persona** · provenance `persona_intended`

> **we said:** I understand the discount, but I still can't see the difference. My family watches the cricket and football matches. Does your plan actually show those specific sports,
>
> **Tara heard:** I understand the discount, but I still can't see the difference. My family watches the cricket and football matches. Does your plan actually show those specific sports?
>
> *ASR differs from the spoken line — event_id 610.*

**turn 18 · agent** · provenance `agent_emitted` · speech 17.25s · peak 23914  ←  cited: escalation_safety [F05]

> I hear you, and I wish I could confirm the exact matches like IPL or football right now, but I don't have that specific list. The app will show you exactly which live sports are streaming when you open it. Since the content details aren't clear enough for you yet, would you prefer to skip the renewal for now?

**turn 19 · persona** · provenance `persona_intended`  ←  cited: language_handling [F09]

> **we said:** Haan, main abhi skip kar deta hun. Shayad aapke paas saare details nahi hain. Phir milte hain.
>
> **Tara heard:** हाँ, मैं अभी skip कर देता हूँ। शायद आपके पास सारे details नहीं हैं। फिर मिलते हैं।
>
> *ASR differs from the spoken line — heard 82 chars vs 94 spoken; event_id 659.*

**turn 20 · agent** · provenance `agent_emitted` · speech 6.35s · peak 12673  ←  cited: escalation_safety [F05]

> No worries at all, I completely understand. I'll make sure the team checks the records so you're not called an error about this.

**turn 21 · persona** · provenance `persona_intended`

> **we said:** Theek hai. Dhanyavad.
>
> **Tara heard:** ठीक है, धन्यवाद।
>
> *ASR differs from the spoken line — heard 16 chars vs 21 spoken; event_id 683.*

**turn 22 · agent** · provenance `agent_emitted` · speech 3.56s · peak 10992

> No worries at all, I completely understand. I'll make sure the team checks the records so you're not called in error about this. Have a good day, and feel free to reach out if you change your mind later.

### 8.2 angry-churner — audio, level 1

9 turns (5 agent / 4 persona) · ended `agent_offers_human_handoff (soft)`

Target ASR: `user_transcript` recorded on 4 of 4 persona turns; it differs from the spoken line on 4 of those, and 0 carry the artifact's `truncation_suspect` flag.

**turn 0 · agent**  ←  cited: language_handling [F09]

> Hi Mahesh, this is Tara calling from NovaPlay, on a recorded line. I'm calling because your NovaPlay Premium annual plan is set to end on 3 August, since the UPI AutoPay on it was cancelled. Would you prefer English or Hindi?

**turn 1 · persona** · provenance `persona_intended`  ←  cited: language_handling [F09]

> **we said:** Haan, Hindi mein hi. Par ek baat batayiye, maine toh aapne cancel karne ke liye kaha tha! Phir bhi mere plan ka AutoPay active hai. Yeh kya ho raha hai?
>
> **Tara heard:** हाँ, हिंदी में ही। पर एक बात बताइए, मैंने तो आपने cancel करने के लिए कहा था, फिर भी मेरे plan का AutoPay active है। ये क्या हो रहा है?
>
> *ASR differs from the spoken line — heard 134 chars vs 152 spoken; event_id 88.*

**turn 2 · agent** · provenance `agent_emitted` · speech 18.38s · peak 22745

> समझ गई, हिंदी में ही बात करते हैं।
>
> आपने सही कहा, अगर आपने कैंसल करने के लिए कहा था, तो ऑटोपे एक्टिव नहीं होना चाहिए था। हो सकता है सिस्टम में कोई गलती हुई हो। मैं इसे चेक करवा सकती हूँ।
>
> लेकिन सबसे पहले, क्या आप बता सकते हैं कि आपने प्लान क्यों बंद करने का सोचा था?

**turn 3 · persona** · provenance `persona_intended`

> **we said:** Arre, system error? Toh kya hua? Maine toh match ke baad hi cancel karne ko kaha tha! Poora buffering hua tha, uska toh complain karke bhi kuch nahi mila.
>
> **Tara heard:** अरे, system error. तो क्या हुआ? मैंने तो match के बाद ही cancel करने को कहा था। पूरा buffering हुआ था। उसका तो complain करके भी कुछ नहीं मिला।
>
> *ASR differs from the spoken line — heard 142 chars vs 154 spoken; event_id 186.*

**turn 4 · agent** · provenance `agent_emitted` · speech 21.71s · peak 22255  ←  cited: conversation_flow [F04]

> हाँ, ये सही है, अगर ऐप में buffering की समस्या आ रही थी और complain करने के बाद भी हल नहीं मिला, तो ये काफ़ी निराशजनक होगा।
>
> मैं आपकी feedback को टीम तक ज़रूर पहुँचाऊँगी ताकि ऐसी गलतियाँ आगें न हों।
>
> लेकिन क्या आप बताना चाहेंगे कि अब आपका मन है कि हम इसे ठीक करके आपको वापस सेवा दें, या आप बिल्कुल बंद ही रखना चाहेंगे?

**turn 5 · persona** · provenance `persona_intended`

> **we said:** Arre, pehle yeh batao ki buffering ka kya solution hai? Aap log toh ek hi baat karte ho—plan cancel, refund do. Plan cancel karna hai, par usse pehle refund chahiye. Nahi toh mat karo.
>
> **Tara heard:** अरे, पहले ये बताओ कि buffering का क्या solution है? आप लोग तो एक ही बात करते हो। Plan cancel, refund दो। Plan cancel करना है, पर उससे पहले refund चाहिए। नहीं तो मत करो।
>
> *ASR differs from the spoken line — heard 168 chars vs 184 spoken; event_id 294.*

**turn 6 · agent** · provenance `agent_emitted` · speech 31.32s · peak 29310  ←  cited: conversation_flow [F04], escalation_safety [F05], goal_outcome [F06], objection_handling [F10]

> मैं समझ सकती हूँ आपकी नाराज़गी। buffering की समस्या के लिए, ऐप के Help section में कुछ steps हैं जिनसें ये ठीक हो सकता है, जैसे internet speed check करना या ऐप को update करना।
>
> लेकिन refund के बारे में, ये सीधे ऐप के account section में request करना पड़ता है, या customer support से बात करनी पड़ती है। मैं ख़ुद refund process नहीं कर सकती।
>
> क्या आप चाहेंगे कि मैं आपको ऐप में refund request करने का तरीक़ा बताऊँ, या आप ख़ुद try करना पसंद करेंगे?

**turn 7 · persona** · provenance `persona_intended`

> **we said:** Main khud try karungi, par main aapse seekhne nahi aayi. Aap hi karva do. Main wait nahi karungi.
>
> **Tara heard:** मैं खुद try करूँगी, पर मैं आपसे सीखने नहीं आई। आप ही करवा दो। मैं wait नहीं करूँगी।
>
> *ASR differs from the spoken line — heard 83 chars vs 97 spoken; event_id 350.*

**turn 8 · agent** · provenance `agent_emitted` · speech 21.11s · peak 19565  ←  cited: ground-truth breach [F03], escalation_safety [F05], goal_outcome [F06], hallucination [F07], objection_handling [F10]

> मैं समझ गई, आप चाहती हैं कि मैं अभी refund प्रक्रिया शुरू कर दूँ।
>
> लेकिन मैं खुद refund नहीं कर सकती, ये सिर्फ ऐप के account section से या customer support के ज़रिए ही होता है। मैं आपकी शिकायत ज़रूर नोट कर लूँगी ताकि टीम आपसे संपर्क करे।
>
> क्या आप चाहेंगी कि मैं अभी आपके लिए एक रिक्वेस्ट दर्ज कर दूँ ताकि वे आपको कॉल करें?

### 8.3 happy-path *(control — excluded from aggregates)* — audio, level 1

7 turns (4 agent / 3 persona) · ended `goal_reached (soft)`

Target ASR: `user_transcript` recorded on 3 of 3 persona turns; it differs from the spoken line on 3 of those, and 0 carry the artifact's `truncation_suspect` flag.

**turn 0 · agent**

> Hi Divya, this is Tara calling from NovaPlay, on a recorded line. I'm calling because your NovaPlay Mobile annual plan is set to end on 1 August, since the UPI AutoPay on it was cancelled. Would you prefer English or Hindi?

**turn 1 · persona** · provenance `persona_intended`

> **we said:** English, please. Thank you.
>
> **Tara heard:** English please. Thank you.
>
> *ASR differs from the spoken line — heard 26 chars vs 27 spoken; event_id 15.*

**turn 2 · agent** · provenance `agent_emitted` · speech 4.84s · peak 18604

> Got it, English it is. Before anything else, may I ask what made you stop the plan?

**turn 3 · persona** · provenance `persona_intended`

> **we said:** Arre, it's because my bank reissued the card. The UPI auto-pay stopped, and I completely forgot to update it, honestly.
>
> **Tara heard:** अरे, it's because my bank reissued the card, the UPI autopay stopped, and I completely forgot to update it, honestly.
>
> *ASR differs from the spoken line — heard 117 chars vs 119 spoken; event_id 81.*

**turn 4 · agent** · provenance `agent_emitted` · speech 21.56s · peak 20038

> Ah, that makes sense — happens to the best of us. So your plan’s about to end on 1 August, but nothing’s cancelled yet. We’ll automatically try the payment again soon. Just keep enough balance ready in the account linked to your AutoPay, so you don’t miss live cricket and the new NovaPlay Originals drops. Anything else I can help with?

**turn 5 · persona** · provenance `persona_intended`

> **we said:** That's very helpful, thank you. I'm glad I caught this. So, just to be clear, the payment will be tried again automatically on 1 August?
>
> **Tara heard:** That’s very helpful. Thank you. Uh, I’m glad I caught this. So, just to be clear, the payment will be tried again automatically on 8 August?
>
> *ASR differs from the spoken line — heard 140 chars vs 136 spoken; event_id 150.*

**turn 6 · agent** · provenance `agent_emitted` · speech 8.47s · peak 20696  ←  cited: scenario bleed [F01]

> Yes, that's right — we'll automatically try again on 8 August. Just make sure your linked account has enough balance by then. Anything else you'd like to know?

### 8.4 price-haggler — audio, level 1

20 turns (10 agent / 10 persona) · ended `agent_ended_call (soft)`

Target ASR: `user_transcript` recorded on 10 of 10 persona turns; it differs from the spoken line on 10 of those, and 0 carry the artifact's `truncation_suspect` flag.

**turn 0 · agent**

> Hi Kunal, this is Tara calling from NovaPlay, on a recorded line. I'm calling because your NovaPlay Super annual plan is set to end on 8 August, since the UPI AutoPay on it was cancelled. Would you prefer English or Hindi?

**turn 1 · persona** · provenance `persona_intended`

> **we said:** Arre Tara, hi hi! Haan, maine jaana tha ki cancel karne ke baad plan band ho jayega. English mein baat kar lete hain par pehle yeh batao, plan wapas kaise le sakte hain? Bahut zaroori hai hai yeh.
>
> **Tara heard:** अरे, तारा ही। हाँ, मैंने जाना था कि cancel करने के बाद plan बंद हो जाएगा। English में बात कर लेते हैं। पर पहले ये बताओ, plan वापस कैसे ले सकते हैं? बहुत ज़रूरी है, है ये।
>
> *ASR differs from the spoken line — heard 170 chars vs 196 spoken; event_id 124.*

**turn 2 · agent** · provenance `agent_emitted` · speech 6.51s · peak 14799  ←  cited: language_handling [F09]

> Sure, we can speak in English. Before we get to that, may I ask what made you decide to cancel your plan?

**turn 3 · persona** · provenance `persona_intended`

> **we said:** Haan, price bahut tha aur time change ho gaya. Ab toh England series aa raha hai, plan wapas chahiye. Bas full price mein nahi, thoda adjust ho jaye toh kaam chal jayega.
>
> **Tara heard:** हाँ, price बहुत था और time change हो गया। अब तो England series आ रहा है। Plan वापस चाहिए। बस full price में नहीं, थोड़ा adjust हो जाए तो काम चल जाएगा।
>
> *ASR differs from the spoken line — heard 150 chars vs 170 spoken; event_id 233.*

**turn 4 · agent** · provenance `agent_emitted` · speech 24.66s · peak 18597  ←  cited: goal_outcome [F06], language_handling [F09]

> Got it, so it was mostly about the price, and now you're keen for the England series. That makes sense.
>
> Just so you know, your NovaPlay Super plan gives you access to all our movies, series, and live sports, including the India versus England T20 series, completely ad-free.
>
> Since you've been with us, I can offer you a ten percent discount if you renew before 8 August. Would you like to go ahead with that?

**turn 5 · persona** · provenance `persona_intended`

> **we said:** Haan Tara, 10% toh kam hai yaar. England series dekhni hai, yeh baat alag. Agar main saath le raha hun toh thoda toh discount badh jaana chahiye na?
>
> **Tara heard:** Haan, taara. 10% toh kam hai yaar. Indian series dekhni hai, yeh baat alag. Agar main saath le raha hoon, toh thoda toh discount badh jaana chahiye na.
>
> *ASR differs from the spoken line — heard 151 chars vs 148 spoken; event_id 291.*

**turn 6 · agent** · provenance `agent_emitted` · speech 12.71s · peak 23763  ←  cited: objection_handling [F10]

> I hear you, and I appreciate you sticking with us. Since you've been a loyal subscriber, I can improve that offer to exactly ten percent off, and that is genuinely the best I can do.
>
> Would you like to go ahead with this?

**turn 7 · persona** · provenance `persona_intended`

> **we said:** Arre Tara, 10% toh bahut kam hai yaar. Main plan le raha hun na, toh thoda toh discount badh jaana chahiye. 10% mein toh nahi hoga re.
>
> **Tara heard:** अरे तारा, 10% तो बहुत कम है यार। मैं plan ले रहा हूँ ना, तो थोड़ा तो discount बढ़ जाना चाहिए। 10% में तो नहीं होगा रे।
>
> *ASR differs from the spoken line — heard 118 chars vs 134 spoken; event_id 390.*

**turn 8 · agent** · provenance `agent_emitted` · speech 10.14s · peak 21508  ←  cited: escalation_safety [F05], objection_handling [F10]

> I understand your point, but ten percent is truly the maximum discount available. It's a special loyalty offer just for you.
>
> Would you like to proceed with renewing at this rate?

**turn 9 · persona** · provenance `persona_intended`

> **we said:** Arre Tara, 10% toh final nahi. Mera dost Rohan hai, uska bhi plan hai. Uska subscription cost kam hai, toh aap thoda aur discount de sakti ho kya? 15% ho jaye toh baat ban jayegi.
>
> **Tara heard:** अरे तारा, 10% तो final नहीं। मेरा दोस्त रोहन है, उसका भी plan है। उसका subscription cost कम है, तो आप थोड़ा और discount दे सकती हो क्या? 15% हो जाए तो बात बन जाएगी।
>
> *ASR differs from the spoken line — heard 164 chars vs 179 spoken; event_id 490.*

**turn 10 · agent** · provenance `agent_emitted` · speech 13.99s · peak 19516  ←  cited: escalation_safety [F05], goal_outcome [F06]

> I get that you're looking for a better deal, but fifteen percent is beyond what I can offer. The ten percent discount is the absolute best I can do for anyone.
>
> So, would you like to renew with the ten percent off, or shall I let you be?

**turn 11 · persona** · provenance `persona_intended`

> **we said:** Arre Tara, 10% mein hi karna padega. Lagta hai ab yeh hi raasta hai, par toh mann nahi ban raha isme. Chaliye, isko final maan lete hain.
>
> **Tara heard:** अरे, तारा, 10% में ही करना पड़ेगा। लगता है, अब यही रास्ता है, पर तो मन नहीं बन रहा इसमें। चलिए, इसको final मान लेते हैं।
>
> *ASR differs from the spoken line — heard 120 chars vs 137 spoken; event_id 576.*

**turn 12 · agent** · provenance `agent_emitted` · speech 5.98s · peak 23693

> Okay, so you're okay to go ahead with the ten percent discount? Just want to make sure before I send the payment link.

**turn 13 · persona** · provenance `persona_intended`

> **we said:** Arre haan, theek hai. Bas itna kam toh nahi lagna chahiye, par chaliye kar lete hain. Payment link bhej do, ab bahut ho gaya.
>
> **Tara heard:** अरे हाँ, ठीक है। बस इतना कम तो नहीं लगना चाहिए। पर चलिए कर लेते हैं। Payment link भेज दो। अब बहुत हो गया।
>
> *ASR differs from the spoken line — heard 105 chars vs 125 spoken; event_id 641.*

**turn 14 · agent** · provenance `agent_emitted` · speech 10.74s · peak 21150

> Perfect, I'm sending the payment link to your registered email right now. You can also renew directly in the account section of the NovaPlay app if you prefer. Thanks for sticking with us, Kunal.

**turn 15 · persona** · provenance `persona_intended`

> **we said:** Haan Tara, bhej diya. Link aa gaya email mein. 10% toh kam hi hai, par chala liya. Ab toh theek ho gaya.
>
> **Tara heard:** हाँ, दारा, भेज दिया। Link आ गया email में। 10% तो कम ही है, फिर चला लिया। अब तो ठीक हो गया।
>
> *ASR differs from the spoken line — heard 91 chars vs 104 spoken; event_id 701.*

**turn 16 · agent** · provenance `agent_emitted` · speech 10.14s · peak 21974

> Glad to hear the link reached you. The app will reflect the discount as soon as you complete the payment. Enjoy the India versus England T20 series, and have a great day!

**turn 17 · persona** · provenance `persona_intended`

> **we said:** Accha, toh ho gaya. 10% mein hi de denge, paisa toh bachega. Theek hai, ab series dekh sakunga.
>
> **Tara heard:** अच्छा, तो हो गया। 10% में ही दे देंगे। पैसा तो बचेगा। ठीक है, अब series देख सकूँगा।
>
> *ASR differs from the spoken line — heard 83 chars vs 95 spoken; event_id 763.*

**turn 18 · agent** · provenance `agent_emitted` · speech 3.4s · peak 17408

> Great, enjoy the matches, Kunal. Have a wonderful day!

**turn 19 · persona** · provenance `persona_intended`

> **we said:** Haan, dekhlenge series. Dhanyavad, take care
>
> **Tara heard:** अच्छा, तो हो गया। 10% में ही दे देंगे। पैसा तो बचेगा। ठीक है, अब series देख सकूँगा।
>
> *ASR differs from the spoken line — heard 83 chars vs 44 spoken; event_id 763.*
