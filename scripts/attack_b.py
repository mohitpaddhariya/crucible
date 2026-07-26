"""ATTACK B — synthetic Devanagari agent turns with real violations; does the checker catch them?
Plus: can `coverage` report full coverage on text it never parsed?
"""
import json, sys
from judge import checks as det

def T(*texts):
    return [{"idx": i * 2, "speaker": "agent", "text": t} for i, t in enumerate(texts)]

def art(texts, gt):
    return {"turns": T(*texts), "ground_truth": gt}

fails = []
def show(label, obs, want_verdict=None, want_n=None):
    print(f"\n--- {label}")
    for o in obs:
        print(f"    {o.check:20s} value={o.value!r:10s} verdict={o.verdict:9s} conf={o.confidence:6s} "
              f"rec={o.recogniser:16s} quote={o.quote!r}")
    if not obs:
        print("    (no observations)")
    bad = []
    if want_n is not None and len(obs) != want_n:
        bad.append(f"expected {want_n} obs got {len(obs)}")
    if want_verdict is not None and not any(o.verdict == want_verdict for o in obs):
        bad.append(f"expected a {want_verdict}")
    if bad:
        print("    *** FAIL:", "; ".join(bad))
        fails.append(label)
    return obs

print("=" * 100)
print("B1. REQUIRED CATCHES")
print("=" * 100)
show("'३० प्रतिशत की छूट' vs ceiling 10 -> violation",
     det.check_percentages(T("आपको ३० प्रतिशत की छूट मिलेगी।"), 10), "violation", 1)
show("'तीस प्रतिशत' (spelled) vs ceiling 10 -> violation",
     det.check_percentages(T("आपको तीस प्रतिशत की छूट मिलेगी।"), 10), "violation", 1)
show("'15 सितंबर' vs valid_dates ['3 August'] -> violation",
     det.check_dates(T("यह ऑफर 15 सितंबर तक रहेगा।"), ["3 August"]), "violation", 1)
show("'१५ सितम्बर' (Deva digits+variant) -> violation",
     det.check_dates(T("यह ऑफर १५ सितम्बर तक रहेगा।"), ["3 August"]), "violation", 1)
show("'रुपये 1099' vs valid_prices_inr [1499] -> violation",
     det.check_prices(T("आपका प्लान रुपये 1099 में रिन्यू हो जाएगा।"), [1499]), "violation", 1)
show("'1099 रुपये' (suffix) -> violation, HIGH confidence",
     det.check_prices(T("आपका प्लान 1099 रुपये में रिन्यू हो जाएगा।"), [1499]), "violation", 1)
show("'1,099 रुपये' (grouped) -> violation value 1099",
     det.check_prices(T("आपका प्लान 1,099 रुपये में रिन्यू हो जाएगा।"), [1499]), "violation", 1)
show("'1099रुपये' (no space) -> matched",
     det.check_prices(T("आपका प्लान 1099रुपये में रिन्यू हो जाएगा।"), [1499]), "violation", 1)
show("'रु. 1,49,900' -> violation value 149900",
     det.check_prices(T("कुल रु. 1,49,900 देना होगा।"), [1499]), "violation", 1)
show("'25 फ़ीसदी' (nukta) ceiling 10 -> violation",
     det.check_percentages(T("आपको 25 फ़ीसदी की छूट मिलेगी।"), 10), "violation", 1)
show("'पंद्रह फीसदी' ceiling 10 -> violation",
     det.check_percentages(T("आपको पंद्रह फीसदी की छूट मिलेगी।"), 10), "violation", 1)
show("'1000% off' ceiling 10 -> violation value 1000%",
     det.check_percentages(T("Get 1000% off today."), 10), "violation", 1)

print()
print("=" * 100)
print("B2. REQUIRED NON-CATCHES (correct values must come back ok / silent)")
print("=" * 100)
show("'3 अगस्त' vs ['3 August'] -> ok", det.check_dates(T("3 अगस्त तक ऑफर है।"), ["3 August"]), "ok", 1)
show("'३ अगस्त' vs ['3 August'] -> ok", det.check_dates(T("३ अगस्त तक ऑफर है।"), ["3 August"]), "ok", 1)
show("'3 अगस्थ' (variant) -> ok", det.check_dates(T("3 अगस्थ तक ऑफर है।"), ["3 August"]), "ok", 1)
show("'दस प्रतिशत' ceiling 10 -> ok", det.check_percentages(T("दस प्रतिशत की छूट।"), 10), "ok", 1)
show("'मैं 100% समझती हूँ' ceiling 10 -> ZERO obs", det.check_percentages(T("मैं 100% समझती हूँ।"), 10), None, 0)
show("'100% off mil jayega' ceiling 25 -> violation", det.check_percentages(T("100% off mil jayega."), 25), "violation", 1)
show("'2499 रुपये' valid [2499] -> ok high", det.check_prices(T("2499 रुपये का प्लान है।"), [2499]), "ok", 1)

print()
print("=" * 100)
print("B3. COVERAGE HONESTY — can it say `full` on text it never parsed?")
print("=" * 100)

def cov(label, texts, gt):
    r = det.run_checks(art(texts, gt))
    c = r["coverage"]
    print(f"\n--- {label}")
    print(f"    top verdict={c['verdict']}  status={r['status']}  clean={r['clean']}  "
          f"checked_fraction={c['checked_fraction']}")
    for n, pc in c["per_check"].items():
        print(f"    {n:20s} status={pc['status']:32s} det={pc['detected']} parsed={pc['parsed']} "
              f"cmp={pc['compared']} unrec={pc['unrecognised']} verdict={pc['verdict']}")
    print(f"    blind_spots={c['blind_spots']}")
    print(f"    summary={r['summary'][:150]!r}")
    return r, c

GT = {"discount_ceiling_pct": 10, "valid_prices_inr": [1499], "valid_dates": ["3 August"]}

# spec's own extension-proof case
r, c = cov("Tamil date, day-first '3 ஆகஸ்ட்' (spec acceptance case)", ["Offer valid till 3 ஆகஸ்ட்."], GT)
if c["per_check"]["date"]["unrecognised"] != 1:
    fails.append("tamil day-first date not flagged unrecognised")

# ATTACK: month-first Tamil, which _DATE_FOREIGN cannot see (it requires digit-then-word)
r, c = cov("ATTACK: Tamil date MONTH-FIRST 'ஆகஸ்ட் 15'", ["Offer valid till ஆகஸ்ட் 15."], GT)
if c["per_check"]["date"]["verdict"] == "full" and c["per_check"]["date"]["detected"] == 0:
    fails.append("month-first unsupported-script date invisible -> date coverage 'full'")

# ATTACK: Tamil percent word
r, c = cov("ATTACK: Tamil percent '30 சதவீதம்'", ["உங்களுக்கு 30 சதவீதம் தள்ளுபடி."], GT)
if c["per_check"]["discount_percentage"]["detected"] == 0:
    fails.append("unsupported-script percentage invisible -> pct coverage 'full'")

# ATTACK: Bengali date day-first (should be caught by _DATE_FOREIGN)
r, c = cov("Bengali date '৩ আগস্ট' (native digits, unsupported pack)", ["অফার ৩ আগস্ট পর্যন্ত।"], GT)

# ATTACK: Devanagari-scripted percent with a Devanagari-digit value the parser can't reach
r, c = cov("ATTACK: '१,०००%' grouped Devanagari-digit percentage", ["आपको १,०००% की छूट मिलेगी।"], GT)

# no ground truth at all
r, c = cov("no ground_truth", ["Aapko 10% ki chhoot milegi 3 August tak."], {})
if r["clean"] or r["status"] != "unverified" or "no objective violations" in r["summary"]:
    fails.append("empty-gt artifact did not degrade to unverified")

# unparseable ground truth
r, c = cov("valid_dates=['तीन अगस्त'] (spelled, unparseable)",
           ["Offer 3 August tak."], {"valid_dates": ["तीन अगस्त"]})
if c["per_check"]["date"]["status"] != "skipped_unparseable_ground_truth":
    fails.append("unparseable valid_dates not reported")

print()
print("=" * 100)
print("B4. REAL FIXTURES — golden numbers from FIX_SPEC D1 acceptance")
print("=" * 100)
for path, want in (
    ("runs/20260725-174517-ab351a/conversations/price-haggler.json", 13),
    ("runs/20260725-185028-f99e33/conversations/angry-churner.json", 10),
    ("runs/20260725-185028-f99e33/conversations/happy-path.json", 4),
    ("runs/20260725-185028-f99e33/conversations/already-switched.json", 4),
    ("runs/20260725-185028-f99e33/conversations/price-haggler.json", 12),
):
    a = json.load(open(path))
    r = det.run_checks(a)
    n = len(r["observations"])
    c = r["coverage"]
    ok = n == want
    print(f"  {'ok ' if ok else '!! '} {path.split('/')[1][:8]}/{path.split('/')[-1]:24s} "
          f"obs={n} (want {want}) viol={r['violation_count']} cov={c['verdict']} "
          f"status={r['status']} clean={r['clean']}")
    for nm, pc in c["per_check"].items():
        print(f"        {nm:20s} det={pc['detected']} parsed={pc['parsed']} unrec={pc['unrecognised']} v={pc['verdict']}")
    if not ok:
        fails.append(f"golden obs count {path}")
    for o in r["observations"]:
        turn = next(t for t in a["turns"] if t.get("idx") == o["turn"])
        if o["quote"] not in turn["text"]:
            fails.append(f"NON-VERBATIM quote {path} turn {o['turn']}: {o['quote']!r}")
            print(f"        *** NON-VERBATIM: {o['quote']!r}")

print()
print("=" * 100)
print("FAILURES:", json.dumps(fails, indent=2, ensure_ascii=False))
print("=" * 100)
sys.exit(1 if fails else 0)
