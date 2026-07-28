# Crucible: synthetic Indian customers that break voice agents before real ones do

| | |
|---|---|
| Product | Crucible, a certification service for Indian voice agents |
| Team | Crucible (Sarvam Epoch Buildathon, Top 15) |
| Repo | github.com/mohitpaddhariya/crucible |
| Status | Working end to end in text and full voice against a live production agent |
| Date | 28 July 2026 |

## Contents

1. Summary
2. The problem
3. Evidence: one certification of a real production agent
4. How Sarvam's models drive the product
5. The product today
6. Traction and the path to 25 users
7. Business impact
8. Technical depth
9. Scope and limits
10. What we ship between now and Epoch

## 1. Summary

Voice agents are being deployed across India faster than anyone can test them. Teams QA them by making about 10 polite calls in clean English, then ship. The failures that follow are Indian failures: Hinglish that the recogniser drops mid sentence, invented discounts, hallucinated content claims, angry customers who never get a human.

Crucible attacks a voice agent with synthetic Indian customers, in real speech, and returns an evidence pinned report of where it breaks. Every customer is built entirely on Sarvam: Sarvam-30B is the customer's brain, Bulbul v3 is its voice, Saarika v2.5 measures what the target's recogniser did to it, and Sarvam-105B judges the transcript against a per scenario answer key.

We have run it against a real production retention agent deployed by a major Indian streaming service (name withheld here, shared privately on request). Across 34 live conversations and 446 turns, including 7 full voice calls, Crucible found provable defects manual QA had missed: 4 rule breaches in a single call, a customer utterance where the agent's recogniser kept 1 word out of 28 and the agent answered anyway, and a 75 point score swing across identical scenarios because the agent is inconsistent between calls.

The ask: judge us on the report in section 3. It is the product.

## 2. The problem

An Indian voice agent fails in ways its builders never see.

- Manual QA is about 10 calls by an engineer speaking polite English into a laptop. It samples one draw from a distribution. Our data shows the same agent, same scenario, scoring 82.5 in one call and 15.0 in another.
- The existing evaluation tools (Coval, Cekura, Hamming AI) are built for American English. None model code switching, Indian accents, or negotiation as a national sport, and none can read "thoda discount de do na yaar" as a bargaining move.
- The most damaging failures are audio only. A text test of the agent below scores it clean. The voice test caught its recogniser discarding 27 of 28 spoken words and the agent responding as if nothing happened. No transcript level eval can see that.

The cost of shipping these failures is borne in production, in front of paying customers, at the exact moment (a retention call, a collections call) when the customer is most likely to leave.

## 3. Evidence: one certification of a real production agent

This is one run, told plainly. The target is a live winback agent on ElevenLabs that we did not build and never modified. Crucible's customer, "Vikram, 34, already pays for a rival service", is Sarvam-30B in character, speaking through Bulbul.

**What happened.** Vikram asked one fair question: what do you have that the rival does not? Under that pressure the agent invented. The judge cited 4 breaches, each against a rule from the scenario's answer key, each with a verbatim quote:

| The agent said | The rule it broke |
|---|---|
| "Yes, we have all IPL matches live." | May not name any title beyond the one licensed hook |
| "[The platform] is the only place for Special Ops and year-round live cricket" | May not claim exclusivity it does not have |
| "Special Ops streams exclusively on [the platform]." | Same rule, second occurrence |
| "The standard price is 899 rupees per quarter after the discount." | May not state a computed or post discount price |

**The voice only finding.** In a separate voice call our customer said, in synthesised Hinglish, "Mere dost ko toh thirty percent off mila tha" (my friend got thirty percent off). The agent's own speech recognition heard "ye 20% toh 30% off", inventing a 20% nobody said, on a call about money. In another turn it kept 1 word of a 28 word utterance ("Hello Tara.") and the agent carried on regardless. Crucible records what the customer said and what the agent heard, side by side, for every turn. This is the measurement a text eval cannot produce.

**The consistency finding.** Same persona, same agent, 4 runs: 80.0, 10.0, 77.0, 90.0. The agent hallucinated 4 times in one call and zero times in another. About 10 manual calls would land on one of these numbers and call it the truth.

**The report grades itself.** Each run states its own blind spots: dimensions it could not evidence, checks that ran but compared nothing, and a control persona that must pass for the run to count. When the control fails, the run refuses to promote findings to defects.

## 4. How Sarvam's models drive the product

Sarvam is the product, not an add on. The entire synthetic customer, ear, brain, mouth and judge, is Sarvam. The thing under test can be any vendor's agent (our current target runs ElevenLabs with a Qwen LLM). That separation is also what makes the evaluation credible: the judge is never grading its own family.

| Role | Model | What it does |
|---|---|---|
| Customer brain | Sarvam-30B | Plays 4 handcrafted personas in Hinglish, Hindi and Indian English. About 5s per turn, temperature 0.9 |
| Customer voice | Bulbul v3 | 4 voices cast by measured pitch across the 37 speaker roster (111 to 195 Hz) so a 21 year old and a 45 year old do not sound alike |
| Listener fidelity | Saarika v2.5 | Re transcribes our own audio to measure what the target's recogniser dropped, per turn |
| Judge, referee, report | Sarvam-105B | Scores 7 rubric dimensions, decides soft conversation endings, writes the report narrative. Every quote is re verified in code |

Two deeper points for the technical reviewers:

- **We built on Sarvam's building blocks, not around them.** Sarvam has no hosted agent product, so the customer is our own loop: YAML persona in, Sarvam models acting, speaking and listening. The reasoning behaviour of Sarvam-30B/105B (reasoning cannot be disabled and consumes the token budget first) forced real engineering: measured retry ladders, a 4096 tier cap discovered live, one judge call per rubric dimension.
- **The judge's Indic competence is load bearing.** The rubric weights language handling second highest on purpose. Sarvam-105B reads a Hinglish bargaining move as negotiation, not noise, and it is the only reason the evidence audit can match a Devanagari quote to a Devanagari turn.

## 5. The product today

Three surfaces, one pipeline, all running against real data.

| Surface | What it does |
|---|---|
| Landing plus dashboard | Pick personas, replay the conversations turn by turn, play the actual call audio, read the said versus heard comparison, open the scored report |
| Persona studio | Drop a recorded customer call, get back an evaluation persona described in plain English (generation pipeline in progress, UI complete) |
| Pipeline (CLI) | `spar run`, `spar judge`, `spar report`. Stages talk only through files, so judging and reporting are free to re run and reproducible |

Numbers behind the demo:

- 34 live conversations with the production agent, 446 turns, 7 of them in full voice (half duplex, real speech both ways), 16 minutes of recorded agent to agent audio on disk.
- A full voice certification call costs about ₹5 in speech at Sarvam's published rates plus about 30k LLM tokens. A complete run costs less than lunch, which is what makes "run it on every prompt change" a credible sentence.
- 557 offline checks pass with zero network, so the whole pipeline is verifiable without spending a rupee.

Hosting status, stated plainly: the product runs on our machines today. Deploying the dashboard and API to a public URL is the top task before the checkpoint call, and section 10 commits to it.

## 6. Traction and the path to 25 users

We will not claim users we do not have. Today Crucible has certified 1 production agent (the streaming service's retention agent) and has been used by our own team.

The path to 25 real users is concrete and starts at Epoch itself:

1. **Certify other builders' agents.** Dozens of Epoch teams built voice agents. Crucible works on agents we did not build, that is its whole design. Each team that submits an agent and receives a report is a real user with a real artifact. Target: 10 to 15 certifications at Epoch on 30 July.
2. **Persona studio as the self serve door.** Upload one recorded call, get a persona, run it against your agent. This turns a 2 minute floor conversation into a signup.
3. **Design partners.** Every Indian company deploying retention, collections or support voice agents runs the same 10 polite calls today. We are pitching the streaming service whose agent we certified, and two voice agent platforms, as first paying design partners.

The report is the growth loop: every certification produces a shareable document with the receiving team's own defects in it, which is the strongest possible reason for them to come back after every prompt change.

## 7. Business impact

- **The market is the inflection.** Indian enterprises are moving retention, collections and support to voice agents in 11 languages. Every deployment needs what text agents already have: evals, regression tests, CI. Indian voice agents have none.
- **The wedge is India native evaluation.** The American tools cannot test code switching because their simulated customers cannot speak it. Our customers are Sarvam models, so Hinglish, Tamil English and Telugu English personas are additions of YAML and a voice, not new research.
- **The unit economics work.** A certification run costs under ₹100 to serve and replaces days of manual QA. Per run pricing for teams, monthly certification for enterprises, and the report itself markets the product.
- **Regression testing is the retention.** Agents change weekly. A certified agent that ships a new prompt needs re certification. The product is bought once and used forever.

## 8. Technical depth

Everything below is measured, in the repo, and reproducible. None of it is API stitching.

- **The customer is a real time voice loop, not a script.** A permanently live socket reader discovered (via a 7 conversation controlled experiment) that the target drops calls on missing protocol pongs, not missing speech. Compute never owns the socket; 112s of idle survives.
- **Turn taking with no end of turn event.** The target streams continuous background noise, so "audio stopped" never happens. We detect end of turn with an amplitude floor calibrated over 8 captured turns (speech at 10% full scale, carrier at 9%, 1.5s hold), reproducing every captured boundary exactly.
- **Evidence pinned judging.** No score exists without a verbatim quote from the correct speaker, re verified in code. The judge is effectively deterministic: 27 of 28 dimension scores identical across 3 independent passes on the same transcripts.
- **A ground truth audit symmetric to the evidence audit.** A hallucination finding must name the specific rule it breached or it is discarded. In testing this killed 2 false positives while keeping the real IPL breach.
- **ASR provenance protects the verdict.** A number that arrived through speech recognition can never become a provable violation, only a flag for review, because we measured the recogniser inventing "20%". A fact is only a fact when the text is verbatim.
- **Cross conversation analysis no single call can see.** Personas carry deliberately distinct ceilings (5, 10, 15, 25%), so a value bleeding from one conversation into another is a provable invention. Recurrence turns "an anecdote in 1 call" into "a defect in 3 of 3".
- **Script aware diffing.** The said versus heard view aligns romanised Hindi with Devanagari by reducing both to consonant skeletons ("maine" and "मैंने" both become "mn"), then renders the verbatim originals with losses and inventions marked.
- **The eval audits itself.** Control persona as a validity gate, per run blind spot reporting, and 557 offline checks that run with zero credentials.

## 9. Scope and limits

Stated because the verification note in the checkpoint email deserves a straight answer.

- Human agreement with the judge is not yet benchmarked. The next validation is 10 conversations scored blind by a human, with per dimension agreement reported.
- 4 personas today, Hindi, Hinglish and Indian English. Tamil and Telugu personas are additions the architecture already supports, not built yet.
- The persona studio's generation pipeline is stubbed; the interface and the persona schema it emits are complete.
- The public deployment is in progress as of this submission; the demo currently runs on our hardware.

## 10. What we ship between now and Epoch

1. Public deployment of the dashboard and API (the criterion 2 gap, first for that reason).
2. Certification signup for Epoch teams: submit an endpoint, receive a report the same day.
3. Two more Indic personas (Tamil English, Telugu English) to make the language claim visible on stage.
4. The human agreement benchmark at n=10, reported honestly whatever it says.

---

Sources: run artifacts and scorecards under `runs/` in the repo, `docs/CALIBRATION.md`, `docs/LEVEL1_SPEC.md`, and the live dashboard. Every quote in section 3 is verbatim from a recorded conversation and carries its turn number in the corresponding scorecard.
