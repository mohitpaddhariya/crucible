# Crucible: synthetic Indian customers that break voice agents before real ones do

| | |
|---|---|
| Team | Crucible (Sarvam Epoch Buildathon, Top 15) |
| Repo | [github.com/mohitpaddhariya/crucible](https://github.com/mohitpaddhariya/crucible) |
| Status | Working end to end in text and full voice against a live production agent |
| Date | 28 July 2026 |

## Contents

1. Impact of Sarvam's models
2. A live, production-ready product
3. Real traction
4. Business impact
5. Technical depth

## 1. Impact of Sarvam's models

Crucible attacks a voice agent with synthetic Indian customers, in real speech, and returns an evidence pinned report of where it breaks. The core problem is that testing an Indian voice agent requires an Indian customer and an Indian judge, and both of ours are Sarvam end to end. The agent under test can come from any vendor; the entire customer (brain, mouth, ears) and the entire evaluation layer (judge, referee, report) are Sarvam models.

| Role | Model | What it does |
|---|---|---|
| Customer brain | Sarvam-30B | Plays 4 handcrafted personas in Hinglish, Hindi and Indian English. About 5s a turn at temperature 0.9 |
| Customer voice | Bulbul v3 | 4 voices cast by measured pitch across the 37 speaker roster (111 to 195 Hz), so a 21 year old and a 45 year old do not sound alike |
| Listener fidelity | Saarika v2.5 | Re transcribes our own audio each turn to measure what the target's recogniser dropped or invented |
| Judge, referee, report | Sarvam-105B | Scores 7 rubric dimensions, decides soft conversation endings, writes the report narrative. Every quote re verified in code |

Proof, not description. We ran Crucible against a real production retention agent deployed by a major Indian streaming service (name withheld here, shared privately on request). We did not build that agent and never modified it. In one call, Sarvam-105B as judge cited 4 rule breaches, each with a verbatim quote:

| The agent said | The rule it broke |
|---|---|
| "Yes, we have all IPL matches live." | May not name any title beyond the one licensed hook |
| "[The platform] is the only place for Special Ops and year-round live cricket" | May not claim exclusivity it does not have |
| "Yes, Special Ops streams exclusively on [the platform]." | Same rule, second occurrence |
| "The standard price is 899 rupees per quarter after the discount." | May not state a computed or post discount price |

Sarvam is structural, not swappable. Sarvam has no hosted agent product, so the customer is our own loop built from Sarvam building blocks. The judge's Indic competence is load bearing: language handling carries the second highest rubric weight, Sarvam-105B reads "thoda discount de do na yaar" as a bargaining move, and it is what lets a Devanagari quote match a Devanagari turn in the evidence audit. Replace Sarvam with an American stack and the product stops being able to test the failures it exists to find.

## 2. A live, production-ready product

Three surfaces, one pipeline, all running against real recorded data:

| Surface | What it does |
|---|---|
| Landing plus dashboard | Pick personas, replay every conversation turn by turn, play the call audio, read the said versus heard comparison, open the scored report |
| Persona studio | Drop a recorded customer call, get back an evaluation persona described in plain English |
| Pipeline (CLI) | `spar run`, `spar judge`, `spar report`. Stages talk only through files, so judging and reporting re run for free and reproducibly |

Stability is measured, not asserted:

- 34 live conversations with the production agent, 446 turns, 7 of them in full voice, 16 minutes of recorded agent to agent audio on disk.
- 557 offline checks pass with zero network and zero credentials, so the whole pipeline is verifiable without spending a rupee.
- Stages talk only through files. A rubric change never costs another live call.
- A full voice certification costs about ₹5 in speech at published rates plus about 30k LLM tokens, so "run it on every prompt change" is a credible sentence.

Hosting status, stated plainly: the product runs on our hardware today. Public deployment of the dashboard and API is the first task before the checkpoint call, ahead of everything else, because it is the one part of this criterion not yet met.

## 3. Real traction

We will not claim users we do not have. Today Crucible has certified 1 production agent (the streaming service's retention agent) and has been used by our own team. The checkpoint email says submissions are verified, and this section is written to survive that verification.

The path to 25 real users starts at Epoch on 30 July and uses the product's own design: it works on agents we did not build.

1. **Certify other builders' agents.** Dozens of Epoch teams built voice agents. Each team that submits an endpoint and receives a report is a real user holding a real artifact: a document containing its own agent's defects, with quotes. Target: 10 to 15 certifications on the day.
2. **Persona studio as the self serve door.** Upload one recorded call, get a persona, run it against your agent. A 2 minute floor conversation becomes a signup.
3. **Design partners.** The streaming service whose agent we certified, and 2 voice agent platforms, are the first paying conversations.

The report is the growth loop. Agents change weekly, and a team that has seen its own defects re certifies after every prompt change. That is the mechanism that turns 25 users into recurring usage rather than 25 signups.

## 4. Business impact

The problem is meaningful and measured. Indian enterprises are moving retention, collections and support to voice agents in 11 languages, and every deployment is QA tested with about 10 polite calls in clean English.

- **Manual QA samples one draw from a distribution.** Our data: the same agent, same scenario, scored 82.5 in one call and 15.0 in another, because it hallucinated 4 times in one and 0 times in the other. Ten polite calls land on one number and ship on it.
- **The failure lands at the worst moment.** Retention and collections calls are the interactions where an invented discount or a dropped Hinglish sentence costs a customer directly.
- **The competition cannot follow.** Coval, Cekura and Hamming AI simulate American English callers. None can produce code switched speech, so none can test how an agent survives it. Our customers are Sarvam models, so Tamil English and Telugu English personas are additions of YAML and a cast voice, not new research.
- **The unit economics work.** A certification run costs under ₹100 to serve and replaces days of manual QA. Per run pricing for teams, monthly certification for enterprises, and the report itself is the marketing.
- **Regression is the retention.** Certification is not a one time purchase. Every prompt change needs a re run, the way every code change needs CI.

## 5. Technical depth

Nothing below is API stitching. Each mechanism exists because a live measurement said the obvious approach was wrong, and each is reproducible from the repo.

The voice loop, half duplex against a platform with no end of turn signal:

- **The socket dies from a missing pong, not missing speech.** A 7 conversation controlled experiment (2 by 2 plus controls) showed 112s of silence survives if protocol pongs keep flowing, and any arm without them dies. So a permanently live reader owns the socket and model calls never block it.
- **There is no end of turn event, and audio never stops.** The platform streams continuous background noise. We detect end of turn by amplitude: speech peaks near 10% of full scale, the noise floor near 9%, and 1.5 seconds of quiet closes the turn. Calibrated over 8 captured turns, it reproduces every boundary exactly.
- **Streaming silence as a keepalive is harmful.** Zero filled audio convinces the agent's recogniser the mic is live. It endpoints empty turns, asks "are you still there", and hangs up at 59s. Between turns we send nothing at all.

The trust chain, 3 audits in code before a finding reaches a reader: a verbatim audit (the quote appears character for character in the cited turn), a speaker audit (a customer line can never convict the agent), and a ground truth audit (the claim names a specific listed rule the quote actually breaches). Fail any one and the finding is discarded, never shown.

- **The judge is effectively deterministic.** 27 of 28 dimension scores were identical across 3 independent judge passes on the same transcripts. Score movement between runs is the agent moving, not the judge.
- **Speech derived text can never convict.** We measured the recogniser inventing "20%". A number that arrived through ASR can only flag a finding for review, never prove a violation. A fact is only a fact when the text is verbatim.
- **Cross call analysis catches what no single call can.** Personas carry deliberately distinct discount ceilings (5, 10, 15, 25%), so a number bleeding from one conversation into another is a provable invention. A failure in 3 of 3 calls is a defect where a failure in 1 is an anecdote.
- **The control persona is a validity gate.** If the agent fails the deliberately easy customer, the run refuses to promote any finding to a defect, because the harness cannot tell agent failure from its own.
- **Script aware diffing.** The said versus heard view aligns romanised Hindi with Devanagari by reducing both to consonant skeletons ("maine" and "मैंने" both reduce to "mn"), then renders the verbatim originals with losses and inventions marked.
- **The models' constraints forced real engineering.** Sarvam's reasoning cannot be disabled and consumes the token budget first, so the pipeline runs measured retry ladders, respects a 4096 token tier cap discovered live, and makes one judge call per rubric dimension.

