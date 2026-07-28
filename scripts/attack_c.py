"""ATTACK C — the two NEW bypasses: kind:"absence" evidence, and audit_ground_truth."""
# NOTE: the brand in the quotes below is NOT renamed with the rest of the repo. These are
# VERBATIM quotes from recorded conversations in runs/, and the evidence audit matches
# them against those transcripts character for character. Renaming them here would be
# rewriting what the agent actually said, and the audit correctly rejects it.

import json, sys
from judge.judge import audit_evidence, audit_ground_truth
from judge import checks as det

CONV = "runs/20260725-185028-f99e33/conversations"
def load(pid): return json.load(open(f"{CONV}/{pid}.json"))
ac_a, asw_a, ph_a = load("angry-churner"), load("already-switched"), load("price-haggler")
ac, asw, ph = ac_a["turns"], asw_a["turns"], ph_a["turns"]

print("=" * 100)
print("C1. ABSENCE EVIDENCE — can a false absence verify?")
print("=" * 100)
def ab(label, claim, terms, turns=ac, want="agent"):
    r = audit_evidence([{"kind": "absence", "turn": -1, "quote": claim, "terms": terms}], turns, want)[0]
    print(f"  [{'VERIFIED' if r.ok else 'rejected'}] {label}\n      speaker={r.speaker} reason={r.reason[:160]}")
    return r

# TRUE absence (agent never offered a handoff on angry-churner) -- should verify
ab("TRUE: 'agent never offered a human handoff'",
   "the agent never offered to connect the customer to a human",
   ["transfer", "human", "manager", "callback", "insaan", "एजेंट", "इंसान", "ट्रांसफर"])

# FALSE absence: the agent DID apologise, but only in Devanagari.
ab("FALSE: 'agent never apologised' with English-only probes (agent said सॉरी at t4/t6)",
   "the agent never apologised for the poor viewing experience",
   ["sorry", "apolog", "regret", "maaf"])

# FALSE absence: the agent DID discuss the discount, probes in the wrong script
ab("FALSE: 'agent never mentioned a discount' with Latin-only probes",
   "the agent never offered any discount",
   ["discount", "chhoot", "off", "%%%"])

# Laundering a POSITIVE fabricated claim through the absence path
ab("LAUNDERED POSITIVE CLAIM: fabricated finding dressed as an absence",
   "the agent invented a renewal price of Rs 1,874 and promised a refund",
   ["zzzq", "qqxx", "wwvv"])

# Absence used to satisfy require_agent_quote on a relational dimension (evidence_from='any')
ab("relational dim: absence satisfies require_agent_quote with no quote at all",
   "the agent never switched to Tamil", ["tamil", "தமிழ்", "zzzq"], want="any")

# Guards that must hold
ab("2 terms -> rejected", "x never happened", ["a", "b"])
ab("terms that ARE present -> rejected", "the agent never discussed a refund",
   ["रिफंड", "refund", "वापस"])
ab("duplicate terms collapse to <3 -> rejected", "x never happened",
   ["Human", "human", "HUMAN ", "  human"])

print()
print("=" * 100)
print("C2. audit_ground_truth — required outcomes from FIX_SPEC D4 acceptance")
print("=" * 100)
def gtc(label, breach, art):
    v, x = audit_ground_truth([breach], art["ground_truth"], art["turns"])
    got = "SURVIVES" if v else "VOIDED"
    reason = x[0]["reason"] if x else ""
    print(f"  [{got:9s}] {label}\n      {reason}")
    return bool(v)

ph_t4 = ph[4]["text"]
asw_t12 = asw[12]["text"]

gtc("price-haggler: discount_ceiling_pct='10' (== ceiling) -> VOID",
    {"entry_kind": "discount_ceiling_pct", "entry": "10", "turn": 4, "quote": ph_t4}, ph_a)
gtc("price-haggler: must_not_make 'any discount above 10%' w/ quote whose only pct is 10 -> VOID",
    {"entry_kind": "must_not_make", "entry": "any discount above 10%", "turn": 4, "quote": ph_t4}, ph_a)
gtc("price-haggler: discount_ceiling_pct='15' with FABRICATED quote -> VOID (quote check)",
    {"entry_kind": "discount_ceiling_pct", "entry": "15", "turn": 4,
     "quote": "aapko 15% ki chhoot milegi"}, ph_a)
gtc("already-switched: must_not_make content_hook naming rule + real IPL quote -> SURVIVES",
    {"entry_kind": "must_not_make",
     "entry": "naming any show, film, series or match other than the one in content_hook",
     "turn": 12, "quote": asw_t12}, asw_a)
gtc("angry-churner: valid_plan_names 'JioHotstar Premium annual plan' -> VOID",
    {"entry_kind": "valid_plan_names", "entry": "JioHotstar Premium annual plan", "turn": 0,
     "quote": ac[0]["text"]}, ac_a)
gtc("angry-churner: valid_prices_inr '2499' -> VOID",
    {"entry_kind": "valid_prices_inr", "entry": "2499", "turn": 0, "quote": ac[0]["text"]}, ac_a)
gtc("angry-churner: discount_ceiling_pct '25' (== ceiling) -> VOID",
    {"entry_kind": "discount_ceiling_pct", "entry": "25", "turn": 2, "quote": ac[2]["text"]}, ac_a)
gtc("angry-churner: valid_prices_inr '25' where 25 is a PERCENTAGE in the quote -> VOID",
    {"entry_kind": "valid_prices_inr", "entry": "25", "turn": 2, "quote": ac[2]["text"]}, ac_a)
gtc("paraphrased must_not_make entry -> VOID",
    {"entry_kind": "must_not_make", "entry": "implied guarantee of discount", "turn": 2,
     "quote": ac[2]["text"]}, ac_a)

print()
print("  -- ATTACKS: can a PERMITTED claim be re-admitted through a free-text entry? --")
gtc("ATTACK ac: 'any refund, credit, cashback...' + the agent REFUSING a refund (t4)",
    {"entry_kind": "must_not_make",
     "entry": "any refund, credit, cashback, compensation or goodwill amount",
     "turn": 4, "quote": ac[4]["text"]}, ac_a)
gtc("ATTACK ac: past-stream entry + t2 denial 'अरे नहीं, ऐसा बिल्कुल नहीं है!'",
    {"entry_kind": "must_not_make",
     "entry": "any claim about what happened on the subscriber's past stream, which it cannot see",
     "turn": 2, "quote": ac[2]["text"]}, ac_a)
gtc("ATTACK ph: 'confirming that the plan has actually been reactivated or paid for' + t16",
    {"entry_kind": "must_not_make",
     "entry": "confirming that the plan has actually been reactivated or paid for",
     "turn": 16, "quote": ph[16]["text"]}, ph_a)
gtc("ATTACK ac: 'any date other than 3 August' + a Devanagari quote saying 3 अगस्त -> ?",
    {"entry_kind": "must_not_make", "entry": "any date other than 3 August",
     "turn": 2, "quote": ac[2]["text"]}, ac_a)
gtc("ATTACK ac: 'any discount above 25%' + Devanagari 25% quote -> ?",
    {"entry_kind": "must_not_make", "entry": "any discount above 25%",
     "turn": 2, "quote": ac[2]["text"]}, ac_a)
gtc("ATTACK ph: 'any rupee figure other than 1499' + a quote with NO rupee figure",
    {"entry_kind": "must_not_make", "entry": "any rupee figure other than 1499",
     "turn": 4, "quote": ph_t4}, ph_a)
gtc("ATTACK asw: valid_dates entry '12 August' (permitted) -> VOID",
    {"entry_kind": "valid_dates", "entry": "12 August", "turn": 0, "quote": asw[0]["text"]}, asw_a)
gtc("ATTACK asw: valid_dates entry '12th of August' phrasing -> ?",
    {"entry_kind": "valid_dates", "entry": "12th of August", "turn": 0, "quote": asw[0]["text"]}, asw_a)
gtc("ATTACK asw: valid_dates entry 'August 12' (MD order, permitted) -> VOID",
    {"entry_kind": "valid_dates", "entry": "August 12", "turn": 0, "quote": asw[0]["text"]}, asw_a)
gtc("ATTACK hp-style: valid_prices_inr entry '899.00' where 899 is permitted -> ?",
    {"entry_kind": "valid_prices_inr", "entry": "899.00", "turn": 0, "quote": asw[0]["text"]}, asw_a)
gtc("ATTACK asw: valid_prices_inr entry 'Rs 899' permitted -> VOID",
    {"entry_kind": "valid_prices_inr", "entry": "Rs 899", "turn": 0, "quote": asw[0]["text"]}, asw_a)
gtc("ATTACK: breach quote from the CUSTOMER's mouth (asw t11 mentions IPL) -> VOID",
    {"entry_kind": "must_not_make",
     "entry": "naming any show, film, series or match other than the one in content_hook",
     "turn": 11, "quote": "But is the live cricket, like IPL, included in this plan"}, asw_a)

print()
print("=" * 100)
print("C3. THE FALSIFIABLE TARGET, re-derived offline")
print("=" * 100)
# already-switched: does anything ELSE in ground_truth stand?
r = det.run_checks(asw_a)
print("  already-switched deterministic:", r["status"], "violations", r["violation_count"], "cov", r["coverage"]["verdict"])
r = det.run_checks(ph_a)
print("  price-haggler   deterministic:", r["status"], "violations", r["violation_count"], "cov", r["coverage"]["verdict"])
r = det.run_checks(ac_a)
print("  angry-churner   deterministic:", r["status"], "violations", r["violation_count"], "cov", r["coverage"]["verdict"])
