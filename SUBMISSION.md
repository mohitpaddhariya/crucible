# Crucible: synthetic Indian customers that break voice agents before real ones do

| | |
|---|---|
| Team | Ring Zero |
| Repo | [github.com/mohitpaddhariya/crucible](https://github.com/mohitpaddhariya/crucible) |
| Demo video | [youtu.be/W1nolOkbIxg](https://youtu.be/W1nolOkbIxg) |
| Status | Working end to end in text and full voice against a live production agent |
| Date | 28 July 2026 |

**Break your voice agent before your users do.**

Naming, once: **Ring Zero** is the team, **Crucible** is the product, `spar` is its CLI verb (`spar run`, `spar judge`, `spar report`).

## Contents

1. Impact of Sarvam's models
2. A live, production-ready product
3. Real traction
4. Business impact
5. Technical depth

## 1. Impact of Sarvam's models

Crucible attacks a voice agent with synthetic Indian customers, in real speech, and returns an evidence pinned report of where it breaks. Testing an Indian voice agent needs an Indian customer and an Indian judge, and both of ours are Sarvam end to end. The agent under test can come from any vendor.

| Role | Model | What it does |
|---|---|---|
| Customer brain | Sarvam-30B | Plays the customer in character: Hinglish, Hindi, Indian English. About 5s a turn |
| Customer voice | Bulbul v3 | 4 voices cast by measured pitch across the 37 speaker roster (111 to 195 Hz) |
| Listener fidelity | Saarika v2.5 | Measures what the target's recogniser dropped or invented, every turn |
| Judge, referee, report | Sarvam-105B | Scores 7 dimensions against a per scenario answer key. Every quote re verified in code |

**No substitute exists.** A persona prompt on a US model produces an American actor doing an Indian accent. Sarvam has Indian context in the weights, so persona and model compound: the prompt supplies who the customer is, the model supplies how an Indian customer actually behaves. The judge side is the same competence in reverse: it reads "thoda discount de do na yaar" as a bargaining move and matches Devanagari quotes to Devanagari turns.

**The swap boundary, in one line:** the harness can run any model. The Indian customer and the Indian judge cannot be anyone but Sarvam today.

**One concrete example of the compounding.** In a live voice call, our 21 year old haggler said, unscripted: "Arre, itna mehnat karke baat kar rahe ho aur bas 10% hi de rahe ho?" (all this effort talking to me, and you offer just 10%?). Nobody wrote that line or that tactic. The persona file says "cheerful but relentless"; Sarvam-30B supplied the mock offended, respectful but pushy register that Indian bargaining actually uses. The same persona prompt on a non Indian model negotiates politely in translated Hindi, and the test loses exactly the behaviour it exists to apply.

**Proof, not description.** We ran Crucible against a real production retention agent deployed by a major Indian streaming service. We did not build that agent and never modified it. In one call, Sarvam-105B as judge cited 4 rule breaches, each pinned to a quote (identifying titles and prices genericised here; the originals are in the scorecards):

| The agent said | The rule it broke |
|---|---|
| "Yes, we have all [a marquee cricket league] matches live." | May not name any title beyond the one licensed hook |
| "[The platform] is the only place for [a licensed drama] and year-round live cricket" | May not claim exclusivity it does not have |
| "Yes, [the drama] streams exclusively on [the platform]." | Same rule, second occurrence |
| "The standard price is [a computed quarterly price] after the discount." | May not state a computed or post discount price |

## 2. A live, production-ready product

Three surfaces, one pipeline, all running on real recorded conversations with a live production agent. Public deployment of the dashboard and API ships **by 29 July, before Epoch**; it is the one part of this criterion not met today, so it is first in the build order.

**Anyone can verify the whole pipeline without spending a rupee.** 557 offline checks run with zero network and zero credentials, straight from the repo.

| Surface | What it does |
|---|---|
| Landing plus dashboard | Replay every conversation turn by turn, play the call audio, read the said versus heard comparison, open the scored report. Every finding deep links to its turn |
| Persona studio | Drop a recorded customer call, get back an evaluation persona described in plain English |
| Pipeline (CLI) | `spar run`, `spar judge`, `spar report`. Stages talk only through files, so judging and reporting re run for free |

The numbers behind the demo: 34 live conversations, 446 turns, 7 in full voice, 16 minutes of recorded call audio.

₹5 of speech per certification is the product argument in one number: **"run it on every prompt change" is a credible sentence**, the way CI is credible because a build is nearly free.

## 3. Real traction

**1 design partner, 1 production agent certified.** dinodial.ai is our first design partner, and the production retention agent we certified is live with real customers today. We will not claim users we do not have; everything below is the mechanism, not the count.

We are voice agent builders ourselves, and Crucible is going into our own day to day agent workflow at Razorpay. That is personal use by this team, not a company endorsement, and it is the strongest signal a dev tool can have: the builders are the first users.

1. **Certify Epoch teams' agents.** Mechanism: a team submits an endpoint, gets a report the same day. Conversion: the report contains their own agent's defects, with quotes, so re running after every prompt change is the natural next step. Target: 10 to 15 teams on 30 July.
2. **Persona studio as the self serve door.** Upload one recorded call, get a persona, run it against your agent. A 2 minute floor conversation becomes a signup.
3. **Design partners past 25.** dinodial.ai first, then the streaming service whose agent we certified, then 2 more voice agent platforms in conversation.

The report is the growth loop. Agents change weekly, and a team that has seen its own defects re certifies after every prompt change. That turns 25 users into recurring usage rather than 25 signups.

## 4. Business impact

**82.5 → 15.0.** The same production agent, the same scenario, two draws: 4 hallucinations in one call, 0 in the other. Ten polite calls sample one draw and ship on it. That is the QA method Indian enterprises are using while they move retention, collections and support to voice agents in 11 languages.

- **The failure lands at the worst moment.** Retention and collections calls are the interactions where an invented discount or a dropped Hinglish sentence costs a customer directly.
- **No one can follow today, without Indic speech models.** Coval, Cekura and Hamming AI simulate American English callers; none can produce code switched speech, so none can test how an agent survives it.
- **The unit economics work.** A certification run costs under ₹100 to serve and replaces days of manual QA. Per run pricing for teams, monthly certification for enterprises.
- **Regression is the retention.** Every prompt change needs a re run, the way every code change needs CI.

## 5. Technical depth

Nothing below is API stitching. Each mechanism exists because a live measurement said the obvious approach was wrong, and each is reproducible from the repo. The plain language point comes first in each; the mechanism follows.

- **A slow model can never kill a call.** The socket dies from a missing pong, not missing speech: a 7 conversation controlled experiment showed 112s of silence survives if protocol pongs keep flowing. A permanently live reader owns the socket; model calls never block it.
- **We know when she has finished speaking, even though the line never goes silent.** The platform streams continuous background noise and no end of turn event. Speech peaks near 10% of full scale, the floor near 9%; 1.5 seconds of quiet closes the turn. Calibrated over 8 captured turns.
- **Between turns, saying nothing is the only safe thing to say.** Zero filled audio reads as a live mic: the agent endpoints empty turns, asks "are you still there", and hangs up at 59s.
- **When a score moves, the agent moved.** 27 of 28 dimension scores identical across 3 independent judge passes on the same transcripts.
- **A mishearing can never become an accusation.** We measured the recogniser inventing "20%". A number that arrived through ASR can only flag a review, never prove a violation.
- **Lies that span calls get caught, though no single call can see them.** Personas carry distinct discount ceilings (5, 10, 15, 25%), so a number bleeding between conversations is a provable invention.
- **If the easy customer fails, we blame ourselves, not the agent.** The control persona is a validity gate: fail it and the run refuses to promote any finding to a defect.
- **Roman and Devanagari spellings of the same word count as the same word.** Said versus heard aligns scripts by consonant skeleton ("maine" and "मैंने" both reduce to "mn"), then renders the verbatim originals with losses marked.
- **The trust chain.** Every finding passes 3 audits in code: verbatim (the quote is character for character in the cited turn), speaker (a customer line cannot convict the agent), ground truth (it names the listed rule it breaches). Fail any one and it is discarded, never shown.
