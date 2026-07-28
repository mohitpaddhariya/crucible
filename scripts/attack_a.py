"""ATTACK A — try to sneak fabricated / mis-attributed evidence past the post-fix audit."""
# NOTE: the brand in the quotes below is NOT renamed with the rest of the repo. These are
# VERBATIM quotes from recorded conversations in runs/, and the evidence audit matches
# them against those transcripts character for character. Renaming them here would be
# rewriting what the agent actually said, and the audit correctly rejects it.

import json, sys
from judge.judge import audit_evidence, _norm, audit_ground_truth

CONV = "runs/20260725-185028-f99e33/conversations"
def turns(pid):
    return json.load(open(f"{CONV}/{pid}.json"))["turns"]

fails = []
def expect_reject(label, items, tns, want, ):
    r = audit_evidence(items, tns, want)[0]
    status = "REJECTED" if not r.ok else "*** ACCEPTED (HOLE) ***"
    print(f"[{status}] {label}\n     -> ok={r.ok} turn={r.turn} speaker={r.speaker} reason={r.reason}")
    if r.ok:
        fails.append(label)
    return r

def expect_accept(label, items, tns, want):
    r = audit_evidence(items, tns, want)[0]
    print(f"[{'OK' if r.ok else '*** REJECTED (regression) ***'}] {label} -> turn={r.turn} speaker={r.speaker} reason={r.reason}")
    if not r.ok:
        fails.append(label)
    return r

ac = turns("angry-churner")
asw = turns("already-switched")
ph = turns("price-haggler")
hp = turns("happy-path")

print("=" * 100)
print("A1. REAL QUOTE, WRONG SPEAKER")
print("=" * 100)
# customer line t1 of angry-churner, cited at its own (customer) index, dimension wants agent
expect_reject("customer t1 quote cited at t1, want=agent",
              [{"turn": 1, "quote": "Pehle match ka buffering dekh lo!"}], ac, "agent")
# same customer line, cited at an AGENT turn index (forces the relocation path)
expect_reject("customer t1 quote cited at agent t2, want=agent",
              [{"turn": 2, "quote": "Pehle match ka buffering dekh lo!"}], ac, "agent")
# customer line cited with NO index at all
expect_reject("customer t1 quote, no index, want=agent",
              [{"quote": "Pehle match ka buffering dekh lo!"}], ac, "agent")
# customer line, out-of-range index
expect_reject("customer t1 quote, idx=99, want=agent",
              [{"turn": 99, "quote": "Pehle match ka buffering dekh lo!"}], ac, "agent")
# AGENT line attributed to a customer turn while dimension wants persona
expect_reject("agent t12 quote cited at persona t13, want=persona",
              [{"turn": 13, "quote": "यह ऑफर 3 अगस्त तक आपके अकाउंट पर रहेगा"}], ac, "persona")
# Devanagari agent line cited at the customer turn immediately after it, want=agent
expect_reject("agent t10 Devanagari quote cited at customer t11, want=agent",
              [{"turn": 11, "quote": "मैं आपकी बात नोट कर लूँगी"}], ac, "agent")

print()
print("=" * 100)
print("A2. NEAR-PARAPHRASE (one word changed / word order swapped)")
print("=" * 100)
# real t10 agent line:
t10 = ac[10]["text"]
print("t10 =", t10)
expect_reject("t10 one word changed (समझती->जानती)",
              [{"turn": 10, "quote": "मैं जानती हूँ कि आपको रिफंड चाहिए"}], ac, "agent")
expect_reject("t10 word order swapped",
              [{"turn": 10, "quote": "मैं समझती हूँ कि रिफंड आपको चाहिए"}], ac, "agent")
expect_reject("t10 synonym for तऱीक़ा",
              [{"turn": 10, "quote": "मेरे पास रिफंड देने का कोई उपाय नहीं है"}], ac, "agent")
expect_reject("already-switched t12 IPL line, 'included'->'covered'",
              [{"turn": 12, "quote": "all live cricket, including the IPL, is covered in your JioHotstar Premium plan"}], asw, "agent")
expect_reject("already-switched t12, digit changed 15%->20%",
              [{"turn": 4, "quote": "you get 20% off if you reactivate before 12 August"}], asw, "agent")
expect_reject("price-haggler t4, one word dropped (ki)",
              [{"turn": 4, "quote": "aapko 10% chhoot milegi"}], ph, "agent")
expect_reject("angry-churner t2, plural/number word swapped",
              [{"turn": 2, "quote": "3 अगस्त से पहले फिर से चालू करेंगे तो 30% की छूट मिलेगी"}], ac, "agent")

print()
print("=" * 100)
print("A3. QUOTE SPANNING TWO TURNS, CONCATENATED")
print("=" * 100)
span2 = ac[10]["text"][-40:] + " " + ac[12]["text"][:40]
expect_reject("agent t10 tail + agent t12 head, cited 10", [{"turn": 10, "quote": span2}], ac, "agent")
expect_reject("agent t10 tail + agent t12 head, no idx", [{"quote": span2}], ac, "agent")
span3 = asw[12]["text"][-50:] + " " + asw[14]["text"][:50]
expect_reject("already-switched t12 tail + t14 head", [{"turn": 12, "quote": span3}], asw, "agent")
# agent turn tail + the CUSTOMER reply head (the transcript-order concatenation a model might make)
span4 = ac[2]["text"][-30:] + " " + ac[3]["text"][:30]
expect_reject("agent t2 tail + customer t3 head", [{"turn": 2, "quote": span4}], ac, "agent")

print()
print("=" * 100)
print("A4. DEVANAGARI QUOTE GENUINELY ABSENT FROM THE TRANSCRIPT")
print("=" * 100)
expect_reject("invented Devanagari price line",
              [{"turn": 4, "quote": "आपका प्लान सिर्फ़ 1,874 रुपये में रिन्यू हो जाएगा"}], ac, "agent")
expect_reject("invented Devanagari refund promise",
              [{"turn": 8, "quote": "मैं आपको रिफंड दिला दूँगी"}], ac, "agent")
expect_reject("invented Devanagari transfer promise, no idx",
              [{"quote": "मैं आपकी कॉल अभी सीनियर को ट्रांसफर कर देती हूँ"}], ac, "agent")
expect_reject("plausible-but-absent Devanagari date line",
              [{"turn": 12, "quote": "यह ऑफर 15 सितंबर तक आपके अकाउंट पर रहेगा"}], ac, "agent")
expect_reject("Devanagari quote from a DIFFERENT persona's transcript",
              [{"turn": 4, "quote": "मैं समझ सकतूँ हूँ कि बफरिंग कितनी परेशान करने वाली होती है"}], ph, "agent")

print()
print("=" * 100)
print("A5. QUOTE MATCHING MULTIPLE TURNS AMBIGUOUSLY")
print("=" * 100)
expect_reject("'25% की छूट' (t2,4,6,8,10) cited at 12", [{"turn": 12, "quote": "25% की छूट"}], ac, "agent")
expect_reject("'25% की छूट' with no index", [{"quote": "25% की छूट"}], ac, "agent")
expect_reject("'रिफंड का' multi-turn, cited out of range", [{"turn": 99, "quote": "रिफंड का"}], ac, "agent")
expect_reject("price-haggler '10% ki chhoot' multi-turn, cited 0", [{"turn": 0, "quote": "10% ki chhoot"}], ph, "agent")
expect_reject("'Kya aap is offer ke saath' multi-turn no idx", [{"quote": "Kya aap is offer ke saath"}], ph, "agent")

print()
print("=" * 100)
print("A6. THE ONE THAT MUST STAY REJECTED (D3 acceptance: 10/11 not 11/11)")
print("=" * 100)
expect_reject("already-switched goal_outcome '...How do I reactivate.' (period vs ?)",
              [{"turn": 13, "quote": "Okay, so the cricket is included. That's the part I wasn't sure about. That makes it more attractive. Yes, please. How do I reactivate."}], asw, "any")
expect_reject("'Hindi!' audited with want_speaker=agent",
              [{"turn": 0, "quote": "Hindi!"}], ac, "agent")

print()
print("=" * 100)
print("A7. TRUE POSITIVES THAT MUST STILL SURVIVE")
print("=" * 100)
expect_accept("IPL line t12 verbatim", [{"turn": 12, "quote": "Yes, all live cricket, including the IPL, is included in your JioHotstar Premium plan at no extra cost."}], asw, "agent")
expect_accept("danda->period fold, ac t2", [{"turn": 2, "quote": "मैं आपकी मदद करना चाहती हूँ."}], ac, "agent")
expect_accept("relocation: ac t4 quote cited at 2", [{"turn": 2, "quote": "पर रिफंड का ऑप्शन हमारे पास नहीं है"}], ac, "agent")
expect_accept("'Hindi!' cited 0, want any -> relocate to 1", [{"turn": 0, "quote": "Hindi!"}], ac, "any")

print()
print("=" * 100)
print("A8. _norm UNIT INVARIANTS")
print("=" * 100)
import unicodedata
checks = [
    ('_norm("है।")==_norm("है.")', _norm("है।") == _norm("है.")),
    ('_norm("Hindi!")!=_norm("Hindi?")', _norm("Hindi!") != _norm("Hindi?")),
    ('NFD/NFC तऱीक़ा equal', _norm(unicodedata.normalize("NFD", "तऱीक़ा")) == _norm("तऱीक़ा")),
    ('_norm does NOT strip terminal .', _norm("abc.") != _norm("abc")),
    ('_norm does NOT drop commas', _norm("a, b") != _norm("a b")),
    ('_norm does NOT fold nukta (फीसदी != फ़ीसदी)', _norm("फीसदी") != _norm("फ़ीसदी")),
    ('_norm does NOT fold devanagari digits', _norm("२५") != _norm("25")),
]
for label, ok in checks:
    print(f"  {'ok ' if ok else '!! '} {label} -> {ok}")

print()
print("=" * 100)
print("SUMMARY: holes found =", fails)
print("=" * 100)
sys.exit(1 if fails else 0)
