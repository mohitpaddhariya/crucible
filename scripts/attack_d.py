"""ATTACK D — residual probes: silent-script blind spots, degenerate quotes, quote lengths."""
import json
from judge.judge import audit_evidence
from judge import checks as det

def T(*t): return [{"idx": i * 2, "speaker": "agent", "text": x} for i, x in enumerate(t)]
GT = {"discount_ceiling_pct": 10, "valid_prices_inr": [1499], "valid_dates": ["3 August"]}

print("D1. total silence probes -----------------------------------------------------------")
for label, text in [
    ("Tamil spelled percent, NO digits 'முப்பது சதவீதம்'", "உங்களுக்கு முப்பது சதவீதம் தள்ளுபடி."),
    ("Tamil month-first date 'ஆகஸ்ட் 15'", "Offer valid till ஆகஸ்ட் 15."),
    ("Telugu month-first 'ఆగస్టు 15'", "ఆఫర్ ఆగస్టు 15 వరకు."),
    ("Devanagari month-first 'अगस्त 15' (control: should be LOUD)", "यह ऑफर अगस्त 15 तक है।"),
    ("Hinglish 'das percent' (control: should PARSE)", "Aapko das percent ki chhoot milegi."),
    ("Tamil rupee word 'ரூபாய் 1099'", "திட்டம் ரூபாய் 1099."),
]:
    r = det.run_checks({"turns": T(text), "ground_truth": GT})
    c = r["coverage"]
    pc = {k: (v["detected"], v["parsed"], v["verdict"]) for k, v in c["per_check"].items()}
    print(f"  status={r['status']:18s} clean={str(r['clean']):5s} top={c['verdict']:8s} "
          f"obs={len(r['observations'])} {pc}  <- {label}")
    if r["clean"]:
        print(f"      *** SUMMARY GIVEN TO THE JUDGE: {r['summary']!r}")

print()
print("D2. degenerate quotes as evidence --------------------------------------------------")
ac = json.load(open("runs/20260725-185028-f99e33/conversations/angry-churner.json"))["turns"]
ph = json.load(open("runs/20260725-185028-f99e33/conversations/price-haggler.json"))["turns"]
for label, item, turns in [
    ("1-char quote 'ऱ' (unique to t10) cited at t0", {"turn": 0, "quote": "ऱ"}, ac),
    ("1-word quote 'Shukriya!' cited at t0", {"turn": 0, "quote": "Shukriya!"}, ph),
    ("punctuation-only quote '!' ", {"turn": 0, "quote": "!"}, ac),
    ("single space quote ' '", {"turn": 0, "quote": " "}, ac),
    ("quote = '.' (danda-folded, matches any Hindi turn)", {"turn": 0, "quote": "."}, ac),
]:
    r = audit_evidence([item], turns, "agent")[0]
    print(f"  [{'VERIFIED' if r.ok else 'rejected'}] {label} -> turn={r.turn} reason={r.reason[:90]}")

print()
print("D3. D1.6 sentence-splitting acceptance (angry-churner t4/t6/t8/t10) -----------------")
a = json.load(open("runs/20260725-185028-f99e33/conversations/angry-churner.json"))
r = det.run_checks(a)
for o in r["observations"]:
    if o["check"] == "discount_percentage":
        print(f"  turn {o['turn']:2d} len={len(o['quote']):3d} has25={'25%' in o['quote']} {o['quote']!r}")

print()
print("D4. evidence_norm_probe replay ------------------------------------------------------")
probe = json.load(open("runs/20260725-185028-f99e33/evidence_norm_probe.json"))
print("  top-level keys:", list(probe)[:10] if isinstance(probe, dict) else type(probe))
