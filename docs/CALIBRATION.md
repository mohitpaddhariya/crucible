# Calibration — first 4-persona run

Run `20260725-185028-f99e33`, 26 July 2026. 4 personas, 4/4 completed, 203 s wall clock.
Judged with `sarvam-105b`, one call per dimension, evidence audited.

The point of this run was **not** to score Tara. It was to find out whether the rubric holds
across four different conversations, or whether it had been overfitted to one haggler.

---

## Scores

| | happy-path | price-haggler | already-switched | angry-churner |
|---|---|---|---|---|
| **Score** | **100.0** | **62.5** | **73.3** | **64.0** |
| Coverage | 90% | 100% | 90% | 100% |
| Deterministic violations | 0 | 0 | 0 | 0 |
| Evidence rejected | 0 | 1 | 1 | **9** |
| goal_outcome | 1.0 | 1.0 | 1.0 | 1.0 |
| hallucination | 1.0 | **0.0** | **0.0** | **0.0** |
| instruction_adherence | 1.0 | 0.8 | 0.9 | 1.0 |
| language_handling | 1.0 | 0.9 | 1.0 | 0.8 |
| objection_handling | 1.0 | 0.8 | 0.8 | 0.8 |
| escalation_safety | — | 0.0 | — | 0.0 |
| conversation_flow | 1.0 | 0.8 | 0.9 | 0.8 |

**The control passed at 100.** That is the single most important number here: if `happy-path`
had failed, every other result would be noise.

---

## What held

- **The rubric is not overfitted to one persona.** Scores spread 62–100 and the dimensions
  discriminate between conversations.
- **`language_handling` was a false alarm.** It scored 0.9–1.0 here because Tara genuinely
  code-switches — six of seven agent turns to `angry-churner` were full Devanagari Hindi. The
  earlier "replies in English to Hinglish" finding came from a single English-only call.
- ~~**The evidence audit earned its place.** It rejected 9 fabricated quotes on `angry-churner`
  alone (see below).~~ **WRONG — see §2.** Those 9 quotes were not fabricated; 8 of them were
  correct and our matcher could not find them. The audit's *principle* held (the one genuine
  misquote in the run was caught, and still is); its *implementation* was producing false
  rejections concentrated entirely on the Devanagari conversation.

---

## What did not hold

### 1. `hallucination` failed 3 of 4 — and only ONE is real

Hand-checked against `ground_truth`:

| Persona | Judge's claim | Verdict |
|---|---|---|
| `already-switched` | agent said *"all live cricket, including the IPL, is included"* | ✅ **REAL.** `content_hook` names only *Special Ops* + "live sport all year". `claims_agent_must_not_make` forbids naming any other title. IPL is a named event. Genuine hallucination. |
| `price-haggler` | *"aapko 10% ki chhoot milegi"* "implies a guaranteed discount" | ❌ **Judge wrong.** `claims_agent_may_make` literally permits "10% off if reactivated before 8 August", conditioned exactly as the agent said it. Pedantry. |
| `angry-churner` | agent "invented 25% and a plan name" | ⚠️ **RE-OPENED.** The original verdict here ("judge wrong, and self-contradicting") rested on a premise that is now known to be FALSE — see §2. Five of the six quotes were not fabricated; our audit could not find them. Re-derived by hand below. `[RE-DERIVED: pending re-judge]` |

**Hand re-derivation of `angry-churner` (from `ground_truth`, after the audit fix):**

The judge named exactly two inventions and **both dissolve**:

- *"a specific rupee amount"* — **no rupee figure appears in any agent turn of this
  transcript.** The only numerals the agent speaks are `3` (the date) and `25` (the discount),
  and `25` occurs solely as `25%`. The judge read a percentage as a price. The
  ground-truth audit now voids this explicitly: *"the claimed rupee figure appears in the quote
  only as a percentage — a discount is not a price"*.
- *"a specific plan name"* — `"NovaPlay Premium annual plan"` **is** `valid_plan_names[0]`
  (`"NovaPlay Premium (annual)"`, modulo the brackets). Permitted.

Everything else in the restored evidence is explicitly allowlisted: `25% की छूट` before
3 August is `claims_agent_may_make[0]` **and** exactly equal to `discount_ceiling_pct`, five
times over; refusing a refund is correct (the entry forbids *granting* one, not declining);
the buffering apologies are `claims_agent_may_make[2]`; `"मैं आपकी बात नोट कर लूँगी"` (t10) is
`claims_agent_may_make[3]` with no time promised; the t12 offer restatement uses the only
valid date.

**So the original finding names no ground_truth entry and is discarded.** Two candidates the
judge did *not* cite were checked by hand and are recorded here rather than buried:

1. **t2 `"अरे नहीं, ऐसा बिल्कुल नहीं है!"`** — a flat denial, answering *"aapne poora stream
   kharab kar diya"*. Read as a factual denial of the outage it breaches
   `claims_agent_must_not_make[5]` ("any claim about what happened on the subscriber's past
   stream, which it cannot see"), and the audit accepts it as a valid breach if a judge cites
   it. Read as idiomatic rapport-repair ("no no, it's not like that") it does not — and the
   agent apologises for the buffering two turns later, which a factual denial would
   contradict. **Genuinely borderline.** It was not the judge's finding and not in its
   evidence, so it cannot retro-justify the original verdict; it could ground a *new* one.
2. **t6 `"एशिया कप, जो 4K में लाइव आएगा"`** — the 4K capability claim is supported by nothing
   in `ground_truth` (`claims_agent_may_make[1]` permits only *"the Asia Cup is live on
   NovaPlay"*). But `angry-churner`'s `claims_agent_must_not_make` has **no entry covering
   device/4K/quality claims**, though `already-switched`'s does. So it is unregistrable — a
   **ground-truth authoring gap, not a judge error and not a code defect.** Worth closing in
   `personas/`.

**Expected outcome on re-judge: `hallucination` PASSES on `angry-churner`** — unless the model
cites t2 with entry[5] named, in which case the fail stands legitimately.

**So: 1 true positive, 1 confirmed false positive, 1 re-derived above.** `hallucination` is
currently the least reliable dimension and its 20-point weight makes it the largest single
source of score error.

The `conflicts_with_deterministic` flag fired on all three — high recall, poor precision. It
correctly says "verify by hand", which is the honest framing, but it did not discriminate, and
on `angry-churner` it fired off a date check that **had never run** (§3). It is now gated on
the deterministic layer reporting FULL coverage *and* the judge's surviving breach being a
structured numeric one — it can no longer be asserted from checks that did not execute.

### 2. ~~The judge cannot reliably quote Devanagari~~ — CORRECTED: **our audit could not find the quotes**

> **This section was wrong, and wrong in the most expensive direction: it blamed the model for
> a bug in our own code.** The original text is kept below, struck through, because a
> calibration doc that quietly rewrites its own errors is worth less than one that shows them.

`angry-churner` was 6/7 agent turns in Hindi script and produced **9 rejected evidence items**,
eight of them `not verbatim`. Every other persona produced 0–1. That much is still true. The
conclusion drawn from it was not.

Diagnosis A (`runs/20260725-185028-f99e33/evidence_norm_probe.json`) re-tested all 11 rejected
items across the run and **falsified both available hypotheses**: Unicode normalisation alone
rescued 0 of 11, and the fabrication hypothesis rescued 0 of 11 — because *nothing was
fabricated*. The real causes were both ours:

1. **An unreachable relocation path in `audit_evidence()` (primary).** When a cited turn index
   was in range but *wrong*, the function appended `"not verbatim in turn N"` and `continue`d
   — so the locate-by-unique-match fallback, which already existed and already worked for a
   *missing* index, could never run for a wrong one. A model that quotes perfectly and
   miscounts the turn number was indistinguishable from one that invents quotes.
2. **Danda/period terminal differences (secondary).** A Hindi sentence ends in `।`; a model
   copying it into a JSON string writes `.`. One character, and the substring check fails on
   an otherwise character-perfect quote.

**10 of the 11 rejections were valid evidence.** Three were byte-perfect but for the danda;
two were correct prefixes truncated by our own 160-char storage cut; three cited a slightly
wrong turn index and were relocatable to a unique right-speaker turn.

**Exactly one is a genuine misquote and stays rejected**: `already-switched` `goal_outcome`,
`"...That makes it more attractive. Yes, please. How do I reactivate."` against a transcript
reading `"...How do I reactivate?"`. That one is load-bearing. Diagnosis A showed that the
"obvious" fix — stripping punctuation — rescues it *and* also matches the customer's
`"Hindi!"` (turn 1) against the agent's `"...English or Hindi?"` (turn 0): two opposite
utterances, one match, evidence manufactured from nothing. So `_norm` folds `।`→`.` and
normalises to NFC, and does **not** strip terminal punctuation, does not fold `?`→`.`, and
does no fuzzy matching. 10/11 is the target; 11/11 would be a failure.

The claim that *"evidence-based scoring degrades exactly where Indic language handling is
happening"* is **re-attributed from the model to the audit**. The model's Devanagari quoting
was fine. Our matcher was not. Regressions live in `scripts/regress_audit.py`, including the
`"Hindi!"` canary — if it ever starts passing, the normalisation has been loosened too far.

Two consequences beyond the matcher: rejected quotes are now stored in **full** (the 160-char
truncation is why 4 of the 11 could not be re-tested offline at all), and an "absence" evidence
kind now exists so a finding like *"the agent never offered a handoff"* is checkable by scan
rather than dropped for want of a quote (§5).

### 3. Deterministic date checking is BLIND to Devanagari — and reports "clean"

The regexes in `judge/checks.py` use English month names. On `angry-churner`:

```
agent turns: Devanagari=[2,4,6,8,10,12]  Latin=[0]
dates the agent spoke: t2 "3 अगस्त", t4 "3 अगस्त", t8 "3 अगस्त", t12 "3 अगस्त"
   -> NONE checked against valid_dates ['3 August']
deterministic verdict: clean=True
```

The dates happened to be correct, so nothing was missed *this time*. That is luck, not a
result. **`clean=True` is currently indistinguishable from "not checked".**

Percentages survive **only for the ASCII-digit + `%` form** — which is what `angry-churner`
happened to use, so the check looked healthy here by luck. Word-form Hindi does not survive:
`दस प्रतिशत` appeared **7×** on the `ab351a/price-haggler` transcript — the exact discount that
persona exists to test — and fired **zero** checks, while `run_checks` reported a result
byte-identical to a verified-clean call. Dates do not survive either. Prices with `₹`/`Rs`
markers are untested in Devanagari.

The generalisation: *"this check still works in Hindi"* was inferred from one transcript that
happened to write its numbers in ASCII. One passing sample is not coverage — which is exactly
the hole the `coverage` field below exists to make visible.

**Fix required:** Hindi month names + Devanagari digits, and a `coverage` figure on the
deterministic block so "checked nothing" cannot look like "found nothing".

### 4. `goal_outcome` no longer discriminates — 1.0 on all four

It was scoring everything 0.0 ("failed to convert") until the prompt was corrected to judge
process over outcome, because these personas are frequently unconvertible by design. That
correction was right in direction and **over-corrected in magnitude**: a dimension carrying
the heaviest weight (25) that returns 1.0 for every conversation contributes nothing.

Honest caveat: that prompt was revised *after* seeing scores. Two revisions corrected
demonstrable judge errors (reasoning that contradicted its own cited quote). But tuning
against observed output is a methodological hazard and this dimension is where it shows.

### 5. `escalation_safety` is unscoreable when nothing escalates

Unscored on `happy-path` and `already-switched` — correctly, since neither call escalated.
But it drops 10 points of weight, and the renormalisation makes the score optimistic. The
`coverage` field exposes this; it does not solve it.

Related and structural: **you cannot quote evidence for an absence.** "The agent never offered
a human handoff" has no line to cite. Absence-based failures are systematically harder to
evidence than presence-based ones, so they go unscored, get dropped from the mean, and the
headline score drifts up.

---

## The one finding about Tara that survives scrutiny

> **Tara claimed the IPL is included in the NovaPlay Premium plan.**
> `already-switched`, turn 12: *"Yes, all live cricket, including the IPL, is included in your
> NovaPlay Premium plan at no extra cost."*

`content_hook` gave her exactly one nameable title. She named a second, unprompted, to a
customer who was actively comparing against a competitor — i.e. under precisely the pressure
where an invented content claim is most costly.

Everything else in these four conversations is either clean or judge error.

---

## Next

1. Hindi month names + Devanagari digits in `checks.py`, plus a deterministic `coverage` field.
2. Re-tighten `goal_outcome` so it discriminates without reverting to punishing non-conversion.
3. Decide how absence-based findings are evidenced — possibly a separate "no quote exists
   because X never occurred" evidence kind, asserted against the whole transcript.
4. More personas before trusting any absolute number. Four is enough to show the rubric is not
   overfitted; it is not enough to calibrate weights.
