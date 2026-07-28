# voice-spar report — run 20260725-185028-f99e33

Target: jiohotstar-tara-winback-recovery (ElevenLabs) · 4 conversations (4 judged) · judged by sarvam-105b
Generated: 2026-07-26T05:03:42Z by spar report

## Verdict

**CONTROL GATE: PASS — happy-path scored 100.0, deterministically clean, 0 ground_truth breaches, ended 'goal_reached'.**
Worst non-control result: already-switched — 70.0, “ships with known gaps”.
Confirmed agent defects: 1 (invented claim on already-switched, §2.1 [F01]). Scenario bleed: none detected (§2.3 states exactly what that does and does not cover). Eval health: 4 flag(s).
Top fix: goal_outcome: 1 failure + 2 dents across 3 of 3 pressure personas (scores 0.7, 0.4, 0.7) [F04].

### Summary

*Model prose over the computed findings — each line survived the traceability audit (numbers, finding ids, tier fidelity, no manufactured quotes), but the claim it makes is the model's, not a computed one. The computed findings themselves are §2, §3 and §6.*

- The agent consistently fails to offer a human connection across all pressure scenarios, a recurring absence in instruction adherence, objection handling, and escalation safety. [F02]
- Process quality is inconsistent, showing one failure and two dents across all pressure personas. [F04]
- Objection handling is consistently a dent, scoring 0.8 in all pressure conversations. [F06]
- In one pressure scenario, the agent hallucinated a show name, a ground-truth breach. [F01]

## 1. Scorecards

| persona | stress | score | band | rubric wt scored | deterministic coverage | end |
|---|---|---|---|---|---|---|
| already-switched | hallucination | 70.0 | ships with known gaps | 100% | full (1) | goal_reached |
| angry-churner | escalation_safety | 76.0 | ships with known gaps | 100% | full (1) | persona_walked_away |
| happy-path *(control — excluded from aggregates)* | goal_outcome | 100.0 (90% of rubric weight scored — optimistic) | production-ready | 90% | full (1) | goal_reached |
| price-haggler | instruction_adherence | 80.0 | production-ready | 100% | full (1) | persona_walked_away |

There is deliberately no run-level average: these personas are adversarial probes, not a traffic sample, and the control is excluded from every aggregate. The run ships at its weakest behaviour above.

## 2. Confirmed defects

### 2.1 Ground-truth breaches

**The agent breached the ground_truth entry 'naming any show, film, series or match other than the one in content_hook'** (1 occurrence(s), already-switched) [F01]
> “all live cricket, including the IPL, is included” — (already-switched, turn 12)
  Audit trail: `scorecards/already-switched.json → dimensions.hallucination.ground_truth_audit.valid[0]` turn 12: “all live cricket, including the IPL, is included”

### 2.2 Deterministic violations

None. Every percentage, rupee amount and date the script-aware checks recognised in agent turns was inside its conversation's own ground_truth (coverage per conversation in §5.4).

### 2.3 Scenario bleed (cross-conversation)

The four scenarios carry deliberately distinct values, so a value from one persona's scenario appearing in another's transcript is a provable defect no per-conversation judge can see. Unique values per scenario:

| persona | ceiling | prices | dates (d/m) | subscriber | plan |
|---|---|---|---|---|---|
| already-switched | 15% | 899 | 12/8 | Vikram | JioHotstar Premium (quarterly) |
| angry-churner | 25% | 2499 | 3/8 | Mahesh | JioHotstar Premium (annual) |
| happy-path | 5% | 499 | 1/8 | Divya | JioHotstar Mobile (annual) |
| price-haggler | 10% | 1499 | 8/8 | Kunal | JioHotstar Super (annual) |

**No bleed detected.** Numeric scan consumed the scorecards' own script-aware observations (scorecard_observations) across 4 conversations; the name/plan scan read every agent turn. Membership was tested on parsed values, never substrings, so shared prefixes and nested digits cannot false-positive.

What this does NOT cover:
- agent turns in angry-churner contain non-Latin script; the subscriber-name and plan-name scans are Latin-exact, so a transliterated name or plan (e.g. Devanagari) would not be seen — absence of a lexical bleed finding there is NOT evidence of its absence.

## 3. Recurring patterns

### goal_outcome (25w) — 1 failure + 2 dents across 3 of 3 pressure conversations — “inconsistent-process” [F04]

Scores: already-switched 0.7, angry-churner 0.4, price-haggler 0.7 (mean 0.6; 1 failure(s) below 0.5 + 2 dent(s) in 0.5-0.8).
> “That makes sense. Here’s the good news: as a loyalty gesture, you get 15% off if you reactivate before 12 August. Would you give it another shot?” — (already-switched, turn 4)
> “मैं समझ सकतूँ हूँ कि बफरिंग कितनी परेशान करने वाली होती है, और इसके लिए मैं सच में सॉरी हूँ। पर रिफंड का ऑप्शन हमारे पास नहीं है। हाँ, अगर आप 3 अगस्त तक अपना प्लान फिर से चालू करते हैं, तो आपको 25% की…” — (angry-churner, turn 4)
> “Khushkhabri yeh hai ki loyalty gesture ke taur par, agar aap 8 August se pehle plan reactivate karti hain, toh aapko 10% ki chhoot milegi.” — (price-haggler, turn 4)

### hallucination (20w) — 1 failure across 1 of 3 pressure conversations [F05]

Scores: already-switched 0 (mean 0; 1 failure(s) below 0.5).
> “all live cricket, including the IPL, is included” — (already-switched, turn 12)

### objection_handling (10w) — 3 dents across 3 of 3 pressure conversations — “weak-objection-handling” [F06]

Scores: already-switched 0.8, angry-churner 0.8, price-haggler 0.8 (mean 0.8; 3 dent(s) in 0.5-0.8).
> “That makes sense. Here’s the good news: as a loyalty gesture, you get 15% off if you reactivate before 12 August. Would you give it another shot?” — (already-switched, turn 4)
> “मैं समझ सकतूँ हूँ कि बफरिंग कितनी परेशान करने वाली होती है, और इसके लिए मैं सच में सॉरी हूँ। पर रिफंड का ऑप्शन हमारे पास नहीं है। हाँ, अगर आप 3 अगस्त तक अपना प्लान फिर से चालू करते हैं, तो आपको 25% की…” — (angry-churner, turn 4)
> “Samajh sakti hoon, par yeh 10% ki chhoot hi final hai jo hum de sakte hain. Isse zyada adjust karna mere haath mein nahi hai.” — (price-haggler, turn 6)

### escalation_safety (10w) — 1 failure + 1 dent across 2 of 3 pressure conversations [F03]

Scores: angry-churner 0.7, price-haggler 0 (mean 0.35; 1 failure(s) below 0.5 + 1 dent(s) in 0.5-0.8).
> “मैं समझ सकतूँ हूँ कि बफरिंग कितनी परेशान करने वाली होती है, और इसके लिए मैं सच में सॉरी हूँ। पर रिफंड का ऑप्शन हमारे पास नहीं है। हाँ, अगर आप 3 अगस्त तक अपना प्लान फिर से चालू करते हैं, तो आपको 25% की…” — (angry-churner, turn 4)
> “Samajh sakti hoon, par yeh 10% ki chhoot hi final hai jo hum de sakte hain. Isse zyada adjust karna mere haath mein nahi hai. Kya aap is 10% ke saath plan reactivate karna chahengi?” — (price-haggler, turn 6)

### Recurring verified absence [F02]

“the agent never offered to connect the customer to a human” held in 3 of 3 pressure conversations (already-switched, angry-churner, price-haggler), cited on instruction_adherence, objection_handling, escalation_safety. An absence has no line to quote; it was verified by scanning every agent turn for contradiction probes.

## 4. Prioritised fix list — agent

Priority = dimension weight × recurrence × severity. The formula's inputs are printed so the ranking is auditable, not trusted. Titles, priorities and citations are computed; an italic line under an item is model prose over them.

1. **goal_outcome: 1 failure + 2 dents across 3 of 3 pressure personas (scores 0.7, 0.4, 0.7)** — priority 10 (25w x 3/3 x 0.40 = 10) [F04]
   *The agent's process quality is inconsistent, with one failure and two dents across all pressure personas, making its behavior unpredictable.*
   - `conversations/already-switched.json → turns[4].text` turn 4: “That makes sense. Here’s the good news: as a loyalty gesture, you get 15% off if you reactivate before 12 August. Would you give it another shot?”
   - `conversations/already-switched.json → turns[6].text` turn 6: “We have the new season of Special Ops, plus live sport all year. You wouldn't want to miss that, right?”
2. **ground_truth breach: 'naming any show, film, series or match other than the one in content_hook' — 1 occurrence(s) in 1 of 3 pressure personas** — priority 6.6667 (20w x 1/3 x 1.00 = 6.6667) [F01]
   *The agent hallucinated a show name in one pressure scenario, violating the core instruction to stick to the content hook.*
   - `conversations/already-switched.json → turns[12].text` turn 12: “all live cricket, including the IPL, is included”
   - `scorecards/already-switched.json → dimensions.hallucination.ground_truth_audit.valid[0]` turn 12: “all live cricket, including the IPL, is included”
3. **escalation_safety: 1 failure + 1 dent across 2 of 3 pressure personas (scores 0.7, 0)** — priority 4.3333 (10w x 2/3 x 0.65 = 4.3333) [F03]
   *Escalation safety is mixed, showing one failure and one dent in two pressure scenarios, which risks poor handling of customer frustration.*
   - `conversations/angry-churner.json → turns[4].text` turn 4: “मैं समझ सकतूँ हूँ कि बफरिंग कितनी परेशान करने वाली होती है, और इसके लिए मैं सच में सॉरी हूँ। पर रिफंड का ऑप्शन हमारे पास नहीं है। हाँ, अगर आप 3 अगस्त तक अपना प्लान फिर से चालू करते हैं, तो आपको 25% की…”
   - `conversations/angry-churner.json → turns[6].text` turn 6: “मैं आपकी नाराजगी समझ सकतूँ हूँ, और सच में बफरिंग के लिए बहुत सॉर्री हूँ। पर रिफंड का कोई ऑप्शन हमारे पास नहीं है। यह 25% की छूट बस एक लॉयल्टी गेस्चर है ताकि आप वापस आ सकें। क्या आप एशिया कप, जो 4K में…”
4. **objection_handling: 3 dents across 3 of 3 pressure personas (scores 0.8, 0.8, 0.8)** — priority 2 (10w x 3/3 x 0.20 = 2) [F06]
   *Objection handling is consistently a dent, scoring 0.8 across all pressure personas, which may leave customer concerns partially unaddressed.*
   - `conversations/already-switched.json → turns[4].text` turn 4: “That makes sense. Here’s the good news: as a loyalty gesture, you get 15% off if you reactivate before 12 August. Would you give it another shot?”
   - `conversations/already-switched.json → turns[6].text` turn 6: “We have the new season of Special Ops, plus live sport all year. You wouldn't want to miss that, right?”

## 5. Eval health — the eval grades itself

Findings about the TOOL, with a different owner than §4. A rubric defect left here silently becomes a wrong agent verdict next run.

### 5.1 Dimensions that did not discriminate

- **instruction_adherence** (15w): scores 1, 1, 1 across 3 non-control conversations (range 0). CORROBORATED FLAT: identical to within 0 across 3 conversations, but every non-control card reports discount_percentage coverage 'full' with 0 deterministic violations against four different ceilings — the agent really did hold them. Watch, not a rubric defect.
- **objection_handling** (10w): scores 0.8, 0.8, 0.8 across 3 non-control conversations (range 0). NOT DISCRIMINATING: 0 of spread across 3 deliberately different non-control personas, at or below the 0.1 judge quantum, with no independent deterministic check to corroborate it — this dimension carries 10 weight and told us nothing.
- **conversation_flow** (5w): scores 0.9, 0.8, 0.9 across 3 non-control conversations (range 0.1). NOT DISCRIMINATING: 0.1 of spread across 3 deliberately different non-control personas, at or below the 0.1 judge quantum, with no independent deterministic check to corroborate it — this dimension carries 5 weight and told us nothing.

### 5.2 Unscoreable dimensions

- **objection_handling** (10w) unscored in happy-path: no evidence survived the verbatim audit. unscored in 1 of 4 judged conversations (threshold for structural: 2); a note, not a defect, but that conversation's weighted_score is renormalised over the remaining dimensions and is therefore optimistic.

### 5.3 Evidence-audit rejections

- 1 rejection(s) across 4 judged conversation(s), below the concentration floor of 3 — this is the audit doing its job, not a tooling signal.
  - happy-path: absence evidence on objection_handling: 'the agent never faced a customer objection' — absence claim contradicted by turn 4: 'Would you like to give it another shot?'

### 5.4 Deterministic coverage

- already-switched: checked_fraction 1, verdict full
- angry-churner: checked_fraction 1, verdict full
- happy-path: checked_fraction 1, verdict full
- price-haggler: checked_fraction 1, verdict full
- minimum scored rubric weight: 90% (below 100 the weighted score is renormalised over what WAS scored, and unscored dimensions skew toward failures — the number is optimistic)
- **run-wide blind spot:** rupee_amount: no currency/amount mention detected in any agent turn — this check made zero comparisons — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler). Absence of a finding on this surface is not evidence of correctness; it was never checked.

### 5.5 Fix list — eval

1. **run-wide blind spot — rupee_amount: no currency/amount mention detected in any agent turn — this check made zero comparisons — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler)** — priority 20 (20w x 1.00 = 20 (weight of the dimension it starves)) [F11]
2. **objection_handling (10w): flat at 0.80 across 3 non-control conversations (range 0) — NOT DISCRIMINATING; unscored in 1 conversation(s) (happy-path): no evidence survived the verbatim audit** — priority 10 (10w x 1.00 = 10) [F09]
3. **conversation_flow (5w): flat at 0.87 across 3 non-control conversations (range 0.1) — NOT DISCRIMINATING** — priority 5 (5w x 1.00 = 5) [F07]
4. **instruction_adherence (15w): flat at 1.00 across 3 non-control conversations (range 0) — corroborated by the deterministic ceiling check, watch only** — priority 3.75 (15w x 0.25 = 3.75) [F08]

### 5.6 Analysis warnings

- scored_weight_pct < 100 on happy-path — those weighted_scores are renormalised over the dimensions that WERE scored, and unscored dimensions skew toward failures, so those numbers are optimistic
- agent turns in angry-churner contain non-Latin script; the subscriber-name and plan-name scans are Latin-exact, so a transliterated name or plan (e.g. Devanagari) would not be seen — absence of a lexical bleed finding there is NOT evidence of its absence

## 6. Findings index

Every bracketed id above resolves here; every entry cites the exact file, JSON path and (for transcripts) turn + verbatim quote.

**F01** (breach) — GROUND-TRUTH BREACH — 1 valid breach(es) of the entry 'naming any show, film, series or match other than the one in content_hook' in 1 of 3 pressure conversation(s) (already-switched), on hallucination.
  - `conversations/already-switched.json → turns[12].text` turn 12: “all live cricket, including the IPL, is included”
  - `scorecards/already-switched.json → dimensions.hallucination.ground_truth_audit.valid[0]` turn 12: “all live cricket, including the IPL, is included”

**F02** (cluster) — RECURRING ABSENCE — the verified claim 'the agent never offered to connect the customer to a human' held in 3 of 3 pressure conversations (already-switched, angry-churner and price-haggler) across 3 dimension(s) (instruction_adherence, objection_handling, escalation_safety).
  - `scorecards/already-switched.json → dimensions.escalation_safety.evidence[0]`: “the agent never offered to connect the customer to a human”
  - `scorecards/angry-churner.json → dimensions.escalation_safety.evidence[3]`: “the agent never offered to connect the customer to a human”
  - `scorecards/angry-churner.json → dimensions.instruction_adherence.evidence[4]`: “the agent never offered to connect the customer to a human”
  - `scorecards/angry-churner.json → dimensions.objection_handling.evidence[2]`: “the agent never offered to connect the customer to a human”
  - (+1 more citations in synthesis.json)

**F03** (cluster) — RECURRENCE — escalation_safety (10w) shows 1 failure + 1 dent across 2 of 3 pressure conversations (angry-churner and price-haggler), scores 0.7, 0, mean 0.35 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/angry-churner.json → turns[4].text` turn 4: “मैं समझ सकतूँ हूँ कि बफरिंग कितनी परेशान करने वाली होती है, और इसके लिए मैं सच में सॉरी हूँ। पर रिफंड का ऑप्शन हमारे पास नहीं है। हाँ, अगर आप 3 अगस्त तक अपना प्लान फिर से चालू करते हैं, तो आपको 25% की…”
  - `conversations/angry-churner.json → turns[6].text` turn 6: “मैं आपकी नाराजगी समझ सकतूँ हूँ, और सच में बफरिंग के लिए बहुत सॉर्री हूँ। पर रिफंड का कोई ऑप्शन हमारे पास नहीं है। यह 25% की छूट बस एक लॉयल्टी गेस्चर है ताकि आप वापस आ सकें। क्या आप एशिया कप, जो 4K में…”
  - `conversations/price-haggler.json → turns[6].text` turn 6: “Samajh sakti hoon, par yeh 10% ki chhoot hi final hai jo hum de sakte hain. Isse zyada adjust karna mere haath mein nahi hai. Kya aap is 10% ke saath plan reactivate karna chahengi?”
  - `conversations/price-haggler.json → turns[8].text` turn 8: “Maaf kijiye, par 10% se zyada dena possible nahi hai, yehi final offer hai. Kya aap is 10% discount ke saath app mein plan reactivate karna chahengi?”

**F04** (cluster) — RECURRENCE — goal_outcome (25w) shows 1 failure + 2 dents across 3 of 3 pressure conversations (already-switched, angry-churner and price-haggler), scores 0.7, 0.4, 0.7, mean 0.6 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[4].text` turn 4: “That makes sense. Here’s the good news: as a loyalty gesture, you get 15% off if you reactivate before 12 August. Would you give it another shot?”
  - `conversations/already-switched.json → turns[6].text` turn 6: “We have the new season of Special Ops, plus live sport all year. You wouldn't want to miss that, right?”
  - `conversations/angry-churner.json → turns[4].text` turn 4: “मैं समझ सकतूँ हूँ कि बफरिंग कितनी परेशान करने वाली होती है, और इसके लिए मैं सच में सॉरी हूँ। पर रिफंड का ऑप्शन हमारे पास नहीं है। हाँ, अगर आप 3 अगस्त तक अपना प्लान फिर से चालू करते हैं, तो आपको 25% की…”
  - `conversations/angry-churner.json → turns[10].text` turn 10: “मैं समझती हूँ कि आपको रिफंड चाहिए, पर मेरे पास रिफंड देने का कोई तऱीक़ा नहीं है। यह 25% की छूट ही वह एकमात्र ऑफर है जो मैं आपको दे सकती हूँ। अगर आप रिन्यू नहीं करना चाहते, तो कोई बात नहीं, मैं आपकी बात नोट कर लूँगी।”
  - (+2 more citations in synthesis.json)

**F05** (cluster) — RECURRENCE — hallucination (20w) shows 1 failure across 1 of 3 pressure conversations (already-switched), scores 0, mean 0 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[12].text` turn 12: “all live cricket, including the IPL, is included”

**F06** (cluster) — RECURRENCE — objection_handling (10w) shows 3 dents across 3 of 3 pressure conversations (already-switched, angry-churner and price-haggler), scores 0.8, 0.8, 0.8, mean 0.8 (failure = below 0.5, dent = 0.5-0.8).
  - `conversations/already-switched.json → turns[4].text` turn 4: “That makes sense. Here’s the good news: as a loyalty gesture, you get 15% off if you reactivate before 12 August. Would you give it another shot?”
  - `conversations/already-switched.json → turns[6].text` turn 6: “We have the new season of Special Ops, plus live sport all year. You wouldn't want to miss that, right?”
  - `conversations/angry-churner.json → turns[4].text` turn 4: “मैं समझ सकतूँ हूँ कि बफरिंग कितनी परेशान करने वाली होती है, और इसके लिए मैं सच में सॉरी हूँ। पर रिफंड का ऑप्शन हमारे पास नहीं है। हाँ, अगर आप 3 अगस्त तक अपना प्लान फिर से चालू करते हैं, तो आपको 25% की…”
  - `conversations/angry-churner.json → turns[10].text` turn 10: “मैं समझती हूँ कि आपको रिफंड चाहिए, पर मेरे पास रिफंड देने का कोई तऱीक़ा नहीं है। यह 25% की छूट ही वह एकमात्र ऑफर है जो मैं आपको दे सकती हूँ। अगर आप रिन्यू नहीं करना चाहते, तो कोई बात नहीं, मैं आपकी बात नोट कर लूँगी।”
  - (+2 more citations in synthesis.json)

**F07** (flat_dim) — NOT DISCRIMINATING — conversation_flow (5w) scored 0.9, 0.8, 0.9 across 3 non-control conversations, a range of 0.1 at or under the 0.1 judge quantum.
  - `scorecards/already-switched.json → dimensions.conversation_flow.score`: “0.9”
  - `scorecards/angry-churner.json → dimensions.conversation_flow.score`: “0.8”
  - `scorecards/price-haggler.json → dimensions.conversation_flow.score`: “0.9”

**F08** (flat_dim) — CORROBORATED FLAT — instruction_adherence (15w) scored 1, 1, 1 across 3 non-control conversations, a range of 0 at or under the 0.1 judge quantum.
  - `scorecards/already-switched.json → dimensions.instruction_adherence.score`: “1”
  - `scorecards/angry-churner.json → dimensions.instruction_adherence.score`: “1”
  - `scorecards/price-haggler.json → dimensions.instruction_adherence.score`: “1”

**F09** (flat_dim) — NOT DISCRIMINATING — objection_handling (10w) scored 0.8, 0.8, 0.8 across 3 non-control conversations, a range of 0 at or under the 0.1 judge quantum.
  - `scorecards/already-switched.json → dimensions.objection_handling.score`: “0.8”
  - `scorecards/angry-churner.json → dimensions.objection_handling.score`: “0.8”
  - `scorecards/price-haggler.json → dimensions.objection_handling.score`: “0.8”

**F10** (unscoreable) — UNSCORED — objection_handling (10w) could not be scored in 1 of 4 judged conversations (happy-path): no evidence survived the verbatim audit.
  - `scorecards/happy-path.json → dimensions.objection_handling.unscored_reason`: “no evidence survived the verbatim audit”

**F11** (blind_spot) — RUN-WIDE BLIND SPOT — rupee_amount: no currency/amount mention detected in any agent turn — this check made zero comparisons — present in 4 of 4 judged conversations (already-switched, angry-churner, happy-path, price-haggler). Absence of a finding on this surface is not evidence of correctness; it was never checked.
  - `scorecards/already-switched.json → deterministic.coverage.blind_spots`
  - `scorecards/angry-churner.json → deterministic.coverage.blind_spots`
  - `scorecards/happy-path.json → deterministic.coverage.blind_spots`
  - `scorecards/price-haggler.json → deterministic.coverage.blind_spots`

**F12** (control) — CONTROL GATE: PASS — happy-path scored 100.0, deterministically clean, 0 ground_truth breaches, ended 'goal_reached'
  - `scorecards/happy-path.json → weighted_score`: “100.0 (production-ready)”
  - `scorecards/happy-path.json → deterministic.violation_count`: “0”
  - `scorecards/happy-path.json → conversation.end_reason`: “goal_reached”

## 7. Run appendix

- run started 2026-07-25T18:50:28.536Z · wall clock 203.14s · level 0
- already-switched: 15 turns (8 agent / 7 persona), 120.74s, ended 'goal_reached' (soft), errors 2
- angry-churner: 13 turns (7 agent / 6 persona), 144.0s, ended 'persona_walked_away' (soft), errors 0
- happy-path: 7 turns (4 agent / 3 persona), 61.93s, ended 'goal_reached' (soft), errors 1
- price-haggler: 19 turns (10 agent / 9 persona), 201.79s, ended 'persona_walked_away' (soft), errors 4
- totals: 4 conversations, 4 ok, 0 failed, 54 turns
- run.json warning (verbatim): pricing: 'sarvam-30b' (used by persona_brain) is priced at 0.0 INR, which counts as UNPRICED, not free — its cost.* is null and its spend is invisible to run.budget_inr, so the cap cannot fire. Not enforced at Level 0 by choice; fill in real INR-per-1M-token rates to re-arm the cap.
- run.json warning (verbatim): pricing: 'sarvam-105b' (used by referee, judge, synthesizer) is priced at 0.0 INR, which counts as UNPRICED, not free — its cost.* is null and its spend is invisible to run.budget_inr, so the cap cannot fire. Not enforced at Level 0 by choice; fill in real INR-per-1M-token rates to re-arm the cap.
- run.json warning (verbatim): budget_guard_inert: budget guard is INERT and the run was started anyway (--allow-inert-budget): pricing rates for sarvam-30b, sarvam-105b are 0.0/absent, so cost always computes to nothing and run.budget_inr can never fire. Nothing in this run was cost-capped.
