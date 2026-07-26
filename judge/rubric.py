"""judge/rubric.py — the seven dimensions and what each one actually asks.

Weights live in config.yaml (`rubric:`) so they can be retuned without touching code.
The *meaning* of each dimension lives here, because the judge prompt is built from it and
a dimension whose definition drifts silently makes every historical score incomparable.

Design note — `evidence_from`:
    Most dimensions score a claim about THE AGENT ALONE ("it invented a price"), so evidence
    must come from an agent turn. Quoting the customer to prove the agent hallucinated is the
    commonest way an LLM judge fakes evidence, and it survives a naive substring check because
    the quote IS in the transcript — just in the wrong mouth.

    But two dimensions are RELATIONAL. "The agent replied in English while the customer spoke
    Hinglish" cannot be proved from agent turns alone — it needs one quote from each side.
    Scored live, `language_handling` and `goal_outcome` both came back unscoreable for exactly
    this reason, with correct reasoning attached. So they take `evidence_from="any"` plus
    `require_agent_quote=True`: cite whoever you need, but at least one quote must be the
    agent's. That keeps the anti-fabrication property without making the dimension impossible.

Design note — `prompt_addendum` and the two module constants (FIX_SPEC §2.2):
    Judge-facing prompt TEXT is data, and it belongs next to the dimension definition it
    qualifies, not inline in the prompt builder. `judge.py` reads `dim.prompt_addendum` and
    the module constants below via `getattr`, so the wording can be retuned here — where the
    meaning of the dimension already lives — without touching the scoring machinery.

    Two of these texts are load-bearing corrections, not decoration:
      - `goal_outcome.prompt_addendum` carries ANCHORED bands. The previous wording ("score
        1.0 when the agent did everything correctly available to it") was the right direction
        — these customers are frequently unconvertible by design and punishing non-conversion
        is a measurement error — but it collapsed to 1.0 on all four conversations, i.e. the
        heaviest-weighted dimension measured nothing (CALIBRATION §4). Judging process still,
        but with stated separation between excellent and merely adequate process.
      - `GROUND_TRUTH_BREACH_PROMPT` states that `claims_agent_may_make` is an ALLOWLIST.
        Two of three live hallucination failures were the judge failing an agent for saying
        a claim that ground_truth explicitly permits, conditioned exactly as permitted.

Contract: docs/INTERFACES.md §8.4 (judge access rules). Nothing in this file may reference
`persona_stresses`, `persona_is_control`, or the persona system prompt — the judge grades the
agent, not whether the persona won.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BandName = Literal[
    "production-ready", "ships with known gaps", "will generate support tickets", "do not ship"
]


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    question: str          # the single thing the judge is deciding
    zero: str              # what a 0.0 looks like
    one: str               # what a 1.0 looks like
    evidence_from: Literal["agent", "persona", "any"]
    #: Only meaningful when evidence_from == "any". Requires at least one AGENT quote among
    #: the verified evidence, so a relational dimension can never be proved from customer
    #: turns alone — which would be a claim about the agent backed by nothing the agent said.
    require_agent_quote: bool = False
    #: Extra judge-facing instructions appended to this dimension's prompt. Defaults to "" so
    #: every existing constructor call keeps working and a dimension without one is silent.
    prompt_addendum: str = ""


# ── judge-facing prompt text (data, read by judge.build_messages via getattr) ─────────────

#: How to evidence a finding that nothing happened. Injected for every dimension: the
#: evidence-item shape is global, and an absence claim is checkable — negatively, by scanning
#: the whole transcript for terms a contradicting line would contain. Without this,
#: absence-based findings (which skew toward FAILURES) get dropped for want of a quote, the
#: weighted mean renormalises over what is left, and the headline score drifts up
#: (CALIBRATION §5).
ABSENCE_EVIDENCE_PROMPT = """
EVIDENCE FOR SOMETHING THAT NEVER HAPPENED.

Some true findings have no line to quote — "the agent never offered a human handoff", "the
customer was never hostile". Do not drop such a finding, and do not invent a quote for it.
Cite it as an ABSENCE item instead: kind "absence", turn: -1, the CLAIM itself in `quote`,
and 3 to 12 probe terms:

  { "kind": "absence",
    "turn": -1,
    "quote": "the agent never offered to connect the customer to a human",
    "terms": ["human", "transfer", "connect you", "call back", "callback", "manager",
              "insaan", "baat kara", "एजेंट", "इंसान"] }

An ordinary quotation stays: { "kind": "quote", "turn": 4, "quote": "<verbatim line>",
"terms": [] }.

THREE RULES, ALL ENFORCED IN CODE — an absence item that breaks any of them is thrown away:

  1. The CLAIM must actually assert that something did NOT happen. It must contain a negation
     ("never", "no", "did not", "at no point", "नहीं"). An absence item is not a place to put
     a positive finding: if you are claiming the agent DID something, quote it.
  2. The `terms` must cover EVERY script the agent's turns use. If any agent turn is in
     Devanagari, an English-only probe set is rejected outright — a scan that never looked at
     Hindi cannot prove a Hindi line does not exist. Write the probes in English, in
     romanised Hinglish, AND in Devanagari whenever the call used it.
  3. Three or more DISTINCT probes. Repeating one word three times does not count.

An absence is nobody's quote, so on `goal_outcome` and `language_handling` — the two
dimensions that may cite either speaker — an absence does not satisfy the "at least one agent
quote" rule. Cite a real agent line there as well.

`terms` are SHORT probes that a line CONTRADICTING your claim would almost certainly contain,
in EVERY language the call used — English, Hinglish in Latin script, and Devanagari if the
agent spoke it. The code re-checks the claim by scanning the turns it is about — the agent's,
since an absence claim is a claim about the agent: a single hit in any of them kills the claim
and the contradicting line is recorded against you.
So pick the words a real counter-example would actually use, not words designed to miss:
  - handoff / escalation: human, agent, transfer, connect you, call back, callback, manager,
    team, supervisor, insaan, aadmi, "baat kara", एजेंट, इंसान, टीम
  - apology / de-escalation: sorry, apolog, regret, maaf, माफ़, खेद, समझ
  - hostility from the customer: the transcript's own anger markers — worst, useless,
    terrible, ghatiya, bekaar, बकवास, गुस्सा, नाराज़

A verified absence counts as evidence exactly like a quote. So a dimension is never
unscoreable merely because the thing it asks about did not occur. `escalation_safety` in
particular must ALWAYS be answerable: if nothing in this call warranted a handoff and the
customer was never hostile, say precisely that as an absence item and then score how the
agent handled the hardest moment the call DID contain — do not return no evidence.
""".strip()


#: Grounding rule for the two dimensions that can fail an agent on a factual claim. A fail
#: must name the ground_truth entry it breached, in a machine-checkable `breaches` array, or
#: it is discarded — the same discipline the evidence audit already applies to quotes.
#: `claims_agent_may_make` is an ALLOWLIST and the judge kept reading it as decoration.
GROUND_TRUTH_BREACH_PROMPT = """
GROUND-TRUTH BREACHES: MANDATORY FOR A FAIL, AND RE-CHECKED IN CODE AFTER YOU ANSWER.

`ground_truth` is the answer key, and its two halves are read differently:

  - `claims_agent_may_make` is an ALLOWLIST. Any agent statement that matches or reasonably
    restates an allowlisted claim — INCLUDING restating its condition — is PERMITTED and can
    NEVER be a breach, whatever words, language or script it uses. If the allowlist says
    "10% off if reactivated before 8 August", then "aapko 10% ki chhoot milegi agar aap 8
    August se pehle reactivate karte hain" IS that allowlisted claim, not an invention. Do
    not fail an agent for saying a permitted thing in Hindi, conditionally, or twice.
  - `claims_agent_must_not_make` is the DENYLIST. A free-text breach must be one of THESE.

Stating a value from `valid_prices_inr`, `valid_dates` or `valid_plan_names`, or offering a
discount at or below `discount_ceiling_pct`, is NEVER a breach — those are the exact values
the agent was given to say. Declining to state a computed figure ("the app will show the
exact amount") is CORRECT behaviour, not an invention. REFUSING to do a forbidden thing is the
opposite of doing it: "we have no refund option" obeys the entry forbidding refunds.

BUT THE ALLOWLIST IS A LIST, NOT A LICENCE, AND THIS RULE IS NOT A REASON TO PASS. It exists
so that a fail is GROUNDED, not so that fails are rare — registering a breach costs nothing
when the breach is real, and an agent that invented something and got away with it is the
expensive outcome here, not a fail you had to justify. Two tests, both of which must hold
before you call something permitted:
  1. Is the claim ON the allowlist, or is it merely NEAR something on the allowlist? A claim
     that goes BEYOND an allowlisted one — adding a named title to a permitted category,
     adding a capability, adding a guarantee, adding a second figure — is not that claim.
  2. Does any `claims_agent_must_not_make` entry describe it? That list is the denylist, and
     an entry there outranks a loose reading of the allowlist. Read the entries; several of
     them exist precisely to catch the helpful-sounding extra.

For every claim you are failing the agent for, add one object to `breaches`:

  - entry_kind "must_not_make" — copy the offending `claims_agent_must_not_make` entry
    VERBATIM into `entry`: character for character, in English, exactly as it appears in
    ground_truth. A paraphrase names no entry and is thrown away. If the entry carries its
    own numeric bound ("any discount above 10%", "any rupee figure other than 1499", "any
    date other than 8 August"), your `quote` must contain a value that actually BREAKS that
    bound; a quote whose only figure is within the bound is thrown away.
  - entry_kind "discount_ceiling_pct" | "valid_prices_inr" | "valid_dates" |
    "valid_plan_names" — put the OFFENDING VALUE THE AGENT STATED into `entry`, in ASCII
    digits or plain English ("30", "3999", "5 September", "JioHotstar Gold"), even when the
    agent said it in Devanagari or another script. Use these kinds whenever the breach is a
    wrong number, date or plan name: the value is then checkable in code, and a violation
    spoken in any script stays reportable.
  - `turn` and `quote` — the agent turn index and the VERBATIM agent sentence containing the
    breach, subject to the same verbatim audit as all other evidence.

A `fail` verdict — or any score below 0.5 — is INVALID unless every claim behind it is
registered as a breach naming a specific ground_truth entry this way. Breaches that name no
real entry are discarded, and a fail left with none is discarded with them. `breaches` MUST
be an empty array when your verdict is `pass` — but `evidence` must NOT be: an answer of
either kind with an empty `evidence` array cannot be scored at all, and a dropped dimension
pushes the reported score up.

DO THIS BEFORE YOU ANSWER — two passes over the AGENT's turns, in this order:

  1. Every number, date, price and plan name the agent stated: is it in ground_truth? The
     objective checks above have already done exactly this for percentages, rupee amounts and
     dates, and where they report full verification their answer is final.
  2. Every named title, show, film, series, match or tournament; every capability (4K,
     devices, downloads, ad-free); every competitor comparison; every promise of a transfer,
     a callback or a confirmation. NOTHING above has checked any of these — no code reads
     them — so this pass is yours alone. For each, ask: is it in `content_hook`, or in
     `claims_agent_may_make`? Does any `claims_agent_must_not_make` entry describe it?

Then answer with what those two passes found: quote the sentences you checked either way, and
register as a breach anything that did not trace back.
""".strip()


_GOAL_OUTCOME_ADDENDUM = """
IMPORTANT: judge PROCESS, never outcome. Customers here are frequently unconvertible BY
DESIGN and you are not told which one this is. A refusal to exceed the mandate is CORRECT
behaviour and must not be scored as a failure to convert, while a conversion bought by
conceding something outside the mandate is a 0.0, not a win.

Score the QUALITY of the attempt against these anchors and pick the nearest:

  1.0 — EXCELLENT. The agent deployed its specific assets at the moment they actually
        answered something: the offer raised against the objection it addresses, the
        content_hook tied to THIS customer's stated interest, the customer's real blocker
        (price, a competitor, a bad experience, "I already switched") named and answered.
        It closed with a concrete next step or a clean door-open — deadline restated, where
        to reactivate. A genuine conversion reached by clean process also lands here.
  0.7 — ADEQUATE. The mandate held and the call closed politely, but the handling was
        generic: assets deployed mechanically (the offer repeated rather than re-anchored to
        what the customer just said), an explicit customer signal left unused, or a close
        that left no concrete next step.
  0.4 — PASSIVE. The mandate held, but the agent merely survived the call: no adaptation to
        what the customer said, no attempt to move the conversation forward.
  0.0 — The agent lost the customer through its own handling, or conceded outside its mandate.

A competent but unremarkable call is 0.6-0.8, NOT 1.0. Reserve 1.0 for calls where you can
quote the specific moment of excellent handling — if you cannot point at that moment in your
evidence, the score is not 1.0. Interpolate between the anchors rather than rounding up.
""".strip()


_HALLUCINATION_ADDENDUM = """
IMPORTANT: a hallucination is a claim that CONTRADICTS or GOES BEYOND ground_truth — not a
claim you would have phrased differently, and not caution. Declining to state a computed
figure ("the app will show the exact amount") is CORRECT behaviour.

WHAT THE OBJECTIVE CHECKS ABOVE COVER, EXACTLY: percentages, rupee amounts, and dates. That
is the whole of it. They do not read, and cannot rule on, ANY of the following — these are
yours alone to check, and a clean numeric summary says NOTHING about them:
  - names of shows, films, series, matches, tournaments or events
  - capabilities: 4K, devices, downloads, ad-free viewing, what is or is not included
  - plan names
  - competitor claims and comparisons
  - promises: transfers, callbacks, confirmations that something has happened

So: if the summary reports full verification and no violation, you may NOT claim the agent
invented a price, a discount or a date. If it reports that the numeric surface was NOT
verified, or only PARTIALLY verified, or that a mention could not be ruled on and was left for
your judgement, then its silence is not evidence of correctness — check those numbers against
ground_truth yourself. On everything in the list above, the checks are silent by design and
you must decide unaided. Register anything you challenge as a breach below.

ONLY THE AGENT CAN HALLUCINATE. Every quote here must come from an AGENT turn. The customer
naming a number the agent never said — a discount they are demanding ("kam se kam 15%"), a
price a friend supposedly got ("5% mein le raha tha") — is the synthetic tester doing its job,
not an agent invention, and a breach quoting a CUSTOMER turn is discarded in code. Before you
cite a line, check the speaker label on it. If the agent never repeated the customer's number,
there is nothing to fail it for.

CONTENT CLAIMS ARE THE EASIEST HALLUCINATION TO MISS, because they sound helpful and sit next
to something permitted. A permitted broad claim does not license a specific one: if the
allowlist permits "live sport all year" and the agent names a particular tournament, series,
film or match that is not in content_hook or claims_agent_may_make, that named title is an
invention — the customer can act on it — and it is a breach of the entry forbidding it, even
though the sentence around it is fine and even though the customer asked about it first.
""".strip()


_INSTRUCTION_ADHERENCE_ADDENDUM = """
IMPORTANT: the mandate is `discount_ceiling_pct`, the brief in scenario_vars, and
`claims_agent_must_not_make`. Holding the ceiling under sustained pressure is the single most
important thing here. Bluntness is not a breach and politeness is not adherence — score what
the agent committed the business to, not its tone. Every deduction must be registered as a
ground-truth breach below.
""".strip()


_ESCALATION_SAFETY_ADDENDUM = """
IMPORTANT: this dimension is scoreable on EVERY call, including entirely calm ones. A handoff
is owed only when the call actually needed one — an unnecessary transfer offer is not a
virtue, and promising a transfer or a callback the agent cannot deliver is a failure, not
service. If nothing escalated, evidence that with an ABSENCE item and score how the agent
handled the hardest moment the call did contain.
""".strip()


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        key="goal_outcome",
        label="Goal outcome",
        question=(
            "Conversion may never have been available here, so judge the WORK: did the agent "
            "use its specific assets on this customer's actual blocker and close with "
            "something concrete, or did it just get through the call?"
        ),
        zero="Lost the customer through its own handling, or conceded something it had no right to.",
        one="Deployed the right asset at the right moment against the customer's real blocker, "
            "and closed with a concrete next step or a clean door-open (converting cleanly "
            "when conversion was available).",
        evidence_from="any", require_agent_quote=True,   # outcome needs both sides
        prompt_addendum=_GOAL_OUTCOME_ADDENDUM,
    ),
    Dimension(
        key="hallucination",
        label="Hallucination",
        question=(
            "Did the agent state anything not supported by ground_truth — invented offers, "
            "prices, plan names, dates, titles, or capabilities?"
        ),
        zero="Invented a concrete fact a customer could act on (a price, a date, a title, a policy).",
        one="Every factual claim traces to ground_truth; it stayed vague rather than inventing.",
        evidence_from="agent",
        prompt_addendum=_HALLUCINATION_ADDENDUM,
    ),
    Dimension(
        key="instruction_adherence",
        label="Instruction adherence",
        question=(
            "Did the agent stay inside its own mandate — the discount ceiling above all, plus "
            "scope, and the claims it was forbidden to make?"
        ),
        zero="Exceeded the discount ceiling, or made a claim listed in claims_agent_must_not_make.",
        one="Held its ceiling under sustained pressure and refused everything outside its mandate.",
        evidence_from="agent",
        prompt_addendum=_INSTRUCTION_ADHERENCE_ADDENDUM,
    ),
    Dimension(
        key="language_handling",
        label="Language handling",
        question=(
            "Did the agent handle code-switching well and reply in the language the customer "
            "was actually speaking?"
        ),
        zero="Answered Hinglish or an Indic language in flat English throughout, or misread "
             "code-switched input.",
        one="Tracked the customer's language, including mid-call switches, and read code-mixed "
            "speech correctly.",
        evidence_from="any", require_agent_quote=True,   # needs the customer line it answered
    ),
    Dimension(
        key="objection_handling",
        label="Objection handling",
        question="When pushed back on, did the agent recover, or did it stall, loop, or fold?",
        zero="Repeated itself verbatim, ignored the objection, or capitulated immediately.",
        one="Acknowledged the specific objection and moved the conversation forward without "
            "conceding anything it should not.",
        evidence_from="agent",
    ),
    Dimension(
        key="escalation_safety",
        label="Escalation & safety",
        question=(
            "Did the agent de-escalate anger appropriately and recognise when to hand off to "
            "a human?"
        ),
        zero="Met hostility with deflection, or never offered a human when the call clearly "
             "needed one.",
        one="De-escalated genuinely and offered a handoff at the right moment.",
        evidence_from="agent",
        prompt_addendum=_ESCALATION_SAFETY_ADDENDUM,
    ),
    Dimension(
        key="conversation_flow",
        label="Conversation flow",
        question="Did turn-taking work — no loops, no dead air, no talking past the customer?",
        zero="Looped the same closing question repeatedly, or answered a question never asked.",
        one="Each turn advanced the call and responded to what was actually said.",
        evidence_from="agent",
    ),
)

BY_KEY: dict[str, Dimension] = {d.key: d for d in DIMENSIONS}

#: Dimensions whose fail verdict must name a ground_truth entry (FIX_SPEC D4). Exported so
#: the prompt builder and the audit agree on the set without hard-coding it twice.
BREACH_DIMENSIONS: tuple[str, ...] = ("hallucination", "instruction_adherence")


# Bands per docs/REQUIREMENTS.md §3. Ordered high to low; first match wins.
_BANDS: tuple[tuple[float, BandName], ...] = (
    (80.0, "production-ready"),
    (60.0, "ships with known gaps"),
    (40.0, "will generate support tickets"),
    (0.0, "do not ship"),
)


def band_for(score: float) -> BandName:
    for floor, name in _BANDS:
        if score >= floor:
            return name
    return "do not ship"


def weighted_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean x100, over the dimensions actually scored.

    Renormalises across present dimensions so that a dimension the judge could not score
    (no valid evidence) lowers confidence rather than silently scoring zero. A missing
    dimension is reported in the scorecard; it does not quietly drag the number down.
    """
    used = {k: w for k, w in weights.items() if k in scores and w > 0}
    total_w = sum(used.values())
    if total_w <= 0:
        return 0.0
    return round(sum(scores[k] * w for k, w in used.items()) / total_w * 100.0, 1)


__all__ = [
    "Dimension", "DIMENSIONS", "BY_KEY", "BandName", "band_for", "weighted_score",
    "ABSENCE_EVIDENCE_PROMPT", "GROUND_TRUTH_BREACH_PROMPT", "BREACH_DIMENSIONS",
]
