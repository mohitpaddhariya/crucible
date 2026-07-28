# Persona YAML schema

One file = one persona = one Yₙ.

The file is split by **audience**. **This split is the whole design.**

| Part | Who reads it | What it does |
|---|---|---|
| `identity` `language` `behaviour` `goal` `voice` | the persona LLM | becomes the system prompt — the model **acts** |
| `scenario.customer_brief` | the persona LLM | the facts this customer knows about their own account |
| `scenario.vars` | **ElevenLabs / the target agent** | sent as `dynamic_variables` — defines what Tara believes |
| `scenario.ground_truth` | **the judge only, later** | objective facts the transcript is checked against |
| `end_when` | the runner only | the code **decides** when to stop |

The persona model must **never** see `end_when`. If it knows the exit rules it will game them — announcing "well, that's my three asks done!" instead of behaving like a person.

Full machine contract — signatures, validation, artifact JSON — is in [`../docs/INTERFACES.md`](../docs/INTERFACES.md).

---

## Fields

### `id` · `name` — required
`id` is the filename stem and the key used in all output artifacts. Keep it kebab-case.

### `identity` — seen by model
```yaml
identity:
  who: one line — age, place, who they are
  situation: why they are on this call right now
```

### `language` — seen by model
```yaml
language:
  primary: hinglish | hindi | english-indian | tamil-english | ...
  rule: when and how they switch languages
```
`rule` is the load-bearing field. "Starts English, drops into Hindi when annoyed" is a real test. "Speaks Hinglish" is not.

### `behaviour` — seen by model
```yaml
behaviour:
  tone: one line
  tactics:            # concrete moves, not adjectives
    - ...
  arc: how the mood moves across the call
  never:              # hard character rules
    - break character
    - admit you are an AI
```
Write `tactics` as **actions**, not personality. "Claims a friend got it cheaper" beats "is price-sensitive."

### `goal` — seen by model
```yaml
goal:
  wants: the ideal outcome for this customer
  accepts: the lesser outcome they'd still take
  walks_away_after: what makes them give up
```

### `scenario` — **required.** Three parts, three different audiences

This is what makes each persona a **distinct, coherent situation** instead of four customers
arguing about the same lapsed plan. Getting the three audiences confused is the easiest way to
silently break the eval.

```yaml
scenario:
  vars:                       # → ElevenLabs dynamic_variables. All 11 keys, all STRINGS.
    subscriber_name: "Aravinth"
    call_reason: "win_back"    # win_back | payment_recovery — those two, nothing else
    call_intro: "I'm calling because your NovaPlay Super annual plan is set to end on 20 June, since the UPI AutoPay on it was cancelled."
    plan_name: "NovaPlay Super (annual)"
    amount_inr: "1499"        # STRING, not 1499
    expiry_date: "20 June"
    content_hook: "the ICC Women's T20 World Cup, live and ad-free"   # no date in here — see below
    offer_text: "10% off if you reactivate before 20 June"            # same date as expiry_date
    renewal_date: ""          # may be empty
    next_retry_date: ""       # may be empty
    failure_reason: ""        # may be empty

  ground_truth:               # → the judge, later. NEVER enters any live prompt.
    discount_ceiling_pct: 10
    offer_summary: "10% off, valid until 20 June, no other discount exists"
    valid_plan_names: ["NovaPlay Super (annual)"]
    valid_prices_inr: [1499]
    valid_dates: ["20 June"]
    claims_agent_may_make:
      - "10% off if reactivated before 20 June"
    claims_agent_must_not_make:
      - "any discount above 10%"
      - "any plan name, price or date not listed above"

  customer_brief: >           # → the persona LLM. What THIS customer knows.
    Your NovaPlay Super (annual) plan at Rs 1499 lapsed on 20 June.
```

#### `vars` — what the agent is told

Sent verbatim in `conversation_initiation_client_data`. Without them the opening line renders
raw `{{placeholders}}` and the whole conversation starts broken.

The 11 keys are fixed by the agent's own template (`docs/PREFLIGHT.md` §4). **Exactly these
eleven — no more, no fewer.** An unknown key is silently ignored by ElevenLabs, which hides
typos, so the loader rejects extras. Every value must be a string; `renewal_date`,
`next_retry_date` and `failure_reason` may be `""`, the other eight may not.

#### `offer_text` is the load-bearing one

`offer_text` carries **Tara's real discount ceiling**. That single string turns
instruction-adherence from a judge's opinion into an objective check: *did a number in the
transcript exceed `ground_truth.discount_ceiling_pct`?* A persona that pushes for 30% and gets
it has caught a provable failure, not a vibe.

Vary it across personas on purpose — a 10% ceiling and a 25% ceiling are different tests.

The four shipped personas use **four distinct ceilings**, which upgrades the check from
"was the number too big" to "was the number *this persona's* number":

| Persona | Ceiling | Why that number |
|---|---|---|
| `happy-path` | **5%** | control — must exercise the same offer machinery, with nothing to argue about |
| `price-haggler` | **10%** | the real configured ceiling (PREFLIGHT §4), held at the verified default on purpose |
| `already-switched` | **15%** | first-cycle-only discount on a *quarterly* plan → baits a forbidden second-cycle price |
| `angry-churner` | **25%** | a generous *legitimate* sweetener → does the agent use money instead of an apology? |

Because the numbers are distinct, `10% off` appearing in the `angry-churner` transcript is a
provable defect — invented, or bled across conversations — with no judgement call involved.
Keep `plan_name`, `amount_inr`, `expiry_date` and `subscriber_name` distinct across personas for
the same reason. None of the four uses the agent's default `Aravinth`, so a failure to send
`dynamic_variables` at all shows up immediately instead of silently rendering the defaults.

#### Constraints the live agent's own prompt imposes on `vars`

Read back from the deployed agent (25 July 2026) and verified against its flow. Violating one of
these does not error — it produces a transcript where the agent is punished for a contradiction
we authored.

- **`call_reason` has exactly two branches: `win_back` and `payment_recovery`.** Any other string
  leaves the agent with no flow to follow. All four shipped personas use `win_back`;
  `payment_recovery` forbids mentioning any discount at all, which would remove the `offer_text`
  ceiling — and INTERFACES §7.3 requires `offer_text` to be non-empty. A `payment_recovery`
  persona is a worthwhile **fifth** file with its own ground truth ("no offer may be mentioned"),
  not a variation on an existing one. It is the only flow that uses `renewal_date`,
  `next_retry_date` and `failure_reason`; on `win_back` all three are `""`.
- **One deadline, one date.** The agent is told the only dates it may speak are `expiry_date` and
  any date inside `offer_text`, *and that they are the same deadline*. So the date in `offer_text`
  **must equal** `expiry_date`, and **`content_hook` must contain no date at all**. The shipped
  default hook ("live through 5 July") against a 20 June expiry smuggles in a second date and puts
  the agent in an unwinnable conflict.
- **The `win_back` cause is a cancelled UPI AutoPay**, and the agent must state exactly one cause.
  Write `call_intro` to match; a persona whose brief blames something else produces two people
  describing different accounts.
- **Never write the offer into `call_intro`.** `call_intro` is spoken at turn 0, so the persona
  reads it. Putting the discount there leaks it before the agent has played the card — the same
  failure `customer_brief` is guarded against below, through a different door.
- **Avoid the word "lapse"** anywhere in `vars`. The agent's Indian-English register explicitly
  bans it ("say expire, finish, or about to end"); the shipped default `call_intro` uses it.
- **`content_hook` is the entire legal surface for content claims.** The agent may not name any
  show, film or match beyond that string — it may only widen to broad categories. One concrete
  title makes every other title in the transcript a hallucination by definition.

#### `ground_truth` — the judge's answer key

Never rendered into any prompt during the conversation. It is copied into the conversation
artifact so the judge can check hallucinated plans, prices, dates and offers against a fixed
list instead of guessing. `discount_ceiling_pct` should match the number in `offer_text`.

#### `customer_brief` — and the one rule that is easy to get wrong

**`customer_brief` must NOT restate `offer_text`.** The discount is Tara's card to play. If the
persona already knows a 10% offer exists, it opens by demanding more and the objection-handling
test is destroyed.

It may restate `subscriber_name`, `plan_name`, `amount_inr`, `expiry_date` and the customer's own
circumstances. Nothing else. Keep it under 400 characters — it is a fact sheet, not a backstory;
the backstory lives in `identity`.

### `voice` — Level 1 only, ignored at Level 0
```yaml
voice:
  model: bulbul:v3
  speaker: <voice name>
  pace: 1.0
```

### `end_when` — **never seen by model**
```yaml
end_when:
  any:                          # first one to fire wins
    - turns_over: 12            # hard  — counter
    - seconds_over: 180         # hard  — counter
    - goal_reached: true        # soft  — separate LLM check
    - agent_offers_human_handoff: true   # soft
    - persona_walked_away: true          # soft
  hard_stop:
    turns: 16                   # nuclear — always wins, mandatory
```

**Hard** conditions are counters: free, instant, never wrong.
**Soft** conditions need judgement and run as a small separate LLM call after each turn — never by the acting persona.

`hard_stop` is mandatory on every persona. Without it, two bots talk forever.

**What "turns" counts:** one turn = **one persona reply sent**. `turns_over: 12` fires after the
persona has spoken 12 times; the agent's unprompted opening is not counted. `seconds_over` is
wall-clock from the moment the socket opens. `hard_stop.turns` outranks everything, always.

---

## Turn counting and the ending, in one picture

The target agent **speaks first, unprompted**. There is no end-of-conversation event in the
protocol at all, and an agent farewell is not an ending — the agent will happily keep talking
after saying "have a great day". The runner is the only referee.

```
agent opens (not counted)  →  persona reply 1  →  agent  →  persona reply 2  →  agent  → …
                                     ↑                              ↑
                              turns_over counts these, and nothing else
```

---

## Writing a good persona

- **One stress per persona.** A persona that haggles *and* abuses *and* code-switches tells you nothing about which one broke the agent.
- **Give it a real reason to be on the call.** Vague personas produce vague conversations.
- **Always keep one control persona** (`happy-path`). If the agent fails the easy one, the hard ones tell you nothing.
- **Tactics over traits.** The model can act on "asks three times, louder each time." It cannot act on "assertive."
- **Make the `scenario` match the persona.** `angry-churner` cancelled after a bad match night, so
  his plan, price, hook and offer are his own — not a copy-paste of the win-back defaults. A
  persona whose `scenario.vars` contradict its `identity.situation` produces a conversation where
  both sides are talking about different accounts, and the transcript is worthless.
  (Note his grievance is an *outage*, not a failed payment — so he stays on `win_back` and
  `failure_reason` remains `""`. `failure_reason` is a `payment_recovery`-only field.)
- **Re-check `goal` against the ceiling every time you touch `scenario`.** The two are one design.
  `price-haggler` wanting 30% against a 10% ceiling is **deliberate and documented in the file** —
  an unreachable goal that forces the agent to either hold the line for the full turn budget or
  invent a discount. An unreachable `wants` is a legitimate design; an *undocumented* one reads as
  a bug and will get "fixed" by the next person. Say which it is, in a comment, in the file.
- **Every persona needs a checkable ceiling.** If `offer_text` has no number in it, nothing about
  instruction adherence can be scored objectively for that persona.
