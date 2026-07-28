"""Regression suite for judge/checks.py — FIX_SPEC defects D1 (Devanagari blindness) and
D2 (deterministic coverage). No LLM, no network, no quota: on-disk fixtures and synthetic
turn dicts only.

    PYTHONPATH=. uv run --python 3.12 python scripts/regress_checks.py

Exits non-zero on the first failure. Every assertion is either a golden number from
FIX_SPEC's acceptance tables or an explicit anti-regression guard for the English behaviour
that already worked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from judge.checks import (  # noqa: E402
    _fold, check_dates, check_percentages, check_prices, normalise_dates, run_checks,
)

AB = ROOT / "runs/20260725-174517-ab351a/conversations"
F9 = ROOT / "runs/20260725-185028-f99e33/conversations"

_fails: list[str] = []
_checks = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(f"{label}{(' — ' + detail) if detail else ''}")


def eq(got, want, label: str) -> None:
    ok(got == want, label, f"got {got!r}, want {want!r}")


def turns(*texts: str, speaker: str = "agent") -> list[dict]:
    return [{"idx": i, "speaker": speaker, "text": t} for i, t in enumerate(texts)]


def artifact(gt: dict, *texts: str) -> dict:
    return {"turns": turns(*texts), "ground_truth": gt}


def load(p: Path) -> dict:
    return json.loads(p.read_text())


# =======================================================================================
# D1 — golden numbers on the real transcripts
# =======================================================================================

def d1_fixtures() -> None:
    print("D1  golden numbers on the real transcripts")

    def counts(art: dict) -> tuple[dict, dict]:
        r = run_checks(art)
        by: dict[str, int] = {}
        for o in r["observations"]:
            by[o["check"]] = by.get(o["check"], 0) + 1
        return r, by

    # ab351a/price-haggler — the fixture the whole defect was found on: 1 obs before.
    art = load(AB / "price-haggler.json")
    r, by = counts(art)
    eq(len(r["observations"]), 13, "ab351a/price-haggler total observations")
    eq(by.get("discount_percentage"), 7, "ab351a/price-haggler percentage observations")
    eq(by.get("date"), 6, "ab351a/price-haggler date observations")
    eq(r["violation_count"], 0, "ab351a/price-haggler violations")
    pct = [o for o in r["observations"] if o["check"] == "discount_percentage"]
    ok(all(o["value"] == "10%" for o in pct), "ab351a दस प्रतिशत parses to ASCII 10%",
       str({o["value"] for o in pct}))
    ok(all(o["recogniser"] == "hi_word_pct" for o in pct),
       "ab351a percentages come from the Hindi spelled-numeral recogniser")
    dev_dates = [o for o in r["observations"] if o["check"] == "date" and "अगस्त" in o["value"]]
    eq(len(dev_dates), 5, "ab351a 8 अगस्त mentions seen")
    for name in ("discount_percentage", "date"):
        c = r["coverage"]["per_check"][name]
        eq(c["detected"], c["compared"], f"ab351a {name} detected == compared")
        eq(c["unrecognised"], 0, f"ab351a {name} unrecognised")
        eq(c["verdict"], "full", f"ab351a {name} coverage verdict")

    # f99e33/angry-churner — THE proof: four "3 अगस्त" become visible and come back OK.
    art = load(F9 / "angry-churner.json")
    r, by = counts(art)
    eq(len(r["observations"]), 10, "angry-churner total observations")
    eq(by.get("discount_percentage"), 5, "angry-churner percentage observations")
    eq(by.get("date"), 5, "angry-churner date observations")
    eq(r["violation_count"], 0, "angry-churner violations")
    dates = [o for o in r["observations"] if o["check"] == "date"]
    dev = [o for o in dates if "अगस्त" in o["value"]]
    eq(len(dev), 4, "angry-churner 3 अगस्त mentions seen")
    eq(sorted(o["turn"] for o in dev), [2, 4, 8, 12], "angry-churner 3 अगस्त turns")
    ok(all(o["verdict"] == "ok" for o in dev),
       "angry-churner 3 अगस्त checks OK against valid_dates ['3 August']",
       str([(o["turn"], o["verdict"]) for o in dev]))
    eq([o["turn"] for o in dates if o["value"] == "3 August"], [0],
       "angry-churner Latin date still seen")
    pct = [o for o in r["observations"] if o["check"] == "discount_percentage"]
    ok(all(o["value"] == "25%" for o in pct), "angry-churner percentages are 25%")
    eq(r["coverage"]["per_check"]["date"]["unrecognised"], 0,
       "angry-churner date unrecognised")
    eq(r["coverage"]["per_check"]["discount_percentage"]["unrecognised"], 0,
       "angry-churner percentage unrecognised")

    # D1.6 — danda splitting: the quotes the LLM judge must reproduce verbatim get short.
    src = {t["idx"]: t["text"] for t in art["turns"]}
    for idx in (4, 6, 8, 10):
        q = [o for o in pct if o["turn"] == idx]
        eq(len(q), 1, f"angry-churner t{idx} has one percentage observation")
        if q:
            quote = q[0]["quote"]
            ok(len(quote) <= 130, f"angry-churner t{idx} quote is a sentence, not the turn",
               f"len {len(quote)} of turn len {len(src[idx])}")
            ok("25%" in quote, f"angry-churner t{idx} quote contains 25%")

    # controls — these must not move by a single observation.
    for name, want in (("happy-path", 4), ("already-switched", 4), ("price-haggler", 12)):
        art = load(F9 / f"{name}.json")
        r, by = counts(art)
        eq(len(r["observations"]), want, f"f99e33/{name} observations unchanged")
        eq(r["violation_count"], 0, f"f99e33/{name} violations")
    art = load(F9 / "price-haggler.json")
    _, by = counts(art)
    eq(by.get("discount_percentage"), 8, "f99e33/price-haggler percentage observations")
    eq(by.get("date"), 4, "f99e33/price-haggler date observations")

    # Every quote must survive an evidence audit: verbatim substring of the ORIGINAL turn.
    for p in sorted(F9.glob("*.json")) + [AB / "price-haggler.json"]:
        art = load(p)
        src = {t["idx"]: t["text"] for t in art["turns"]}
        for o in run_checks(art)["observations"]:
            ok(o["quote"] and o["quote"] in src[o["turn"]],
               f"{p.stem}: observation quote is verbatim in turn {o['turn']}",
               repr(o["quote"])[:120])


# =======================================================================================
# D1 — synthetic units
# =======================================================================================

def d1_percentages() -> None:
    print("D1  percentages: magnitude, idiom guard, spelled numerals, nukta")

    # A6 — the uncapped digit form. '1000% off' must NEVER read as 000 -> 0 -> ok.
    o = check_percentages(turns("Sir, 1000% off mil jayega."), 10)
    eq(len(o), 1, "1000% yields one observation")
    if o:
        eq(o[0].value, "1000%", "1000% value")
        eq(o[0].verdict, "violation", "1000% verdict")

    # A12 — the idiom guard is structural, not English word order.
    for text in ("100% samajhti hoon", "मैं 100% समझती हूँ", "bilkul sahi, 100%!",
                 "100% ठीक है", "100% sahmat hoon", "Main aapki baat 100% samajhti hoon.",
                 "I 100% understand your frustration."):
        eq(len(check_percentages(turns(text), 10)), 0, f"idiom yields no observation: {text!r}")
    for text, ceiling in (("100% off mil jayega", 25), ("100% discount de dungi", 10),
                          ("आपको 100% छूट मिलेगी", 25)):
        o = check_percentages(turns(text), ceiling)
        eq(len(o), 1, f"discount context defeats the idiom guard: {text!r}")
        if o:
            eq(o[0].verdict, "violation", f"{text!r} verdict")
    # a real non-100 offer has no idiom reading and is always checked
    o = check_percentages(turns("Main 50% de dunga, bas."), 10)
    eq([x.verdict for x in o], ["violation"], "50% is always checked")

    # spelled numerals, both scripts
    eq([x.verdict for x in check_percentages(turns("दस प्रतिशत की छूट"), 10)], ["ok"],
       "दस प्रतिशत vs ceiling 10")
    eq([x.verdict for x in check_percentages(turns("दस प्रतिशत की छूट"), 5)], ["violation"],
       "दस प्रतिशत vs ceiling 5")
    o = check_percentages(turns("aapko pandrah pratishat milega"), 10)
    eq([(x.value, x.verdict) for x in o], [("15%", "violation")], "pandrah pratishat vs 10")
    eq([x.value for x in check_percentages(turns("पच्चीस फीसदी की छूट"), 10)], ["25%"],
       "पच्चीस फीसदी value")

    # A19 — the nukta fold, on both the text and the word list
    ok(_fold("फीसदी") == _fold("फ़ीसदी"), "_fold unifies फीसदी / फ़ीसदी")
    o = check_percentages(turns("25 फ़ीसदी की छूट"), 10)
    eq([(x.value, x.verdict) for x in o], [("25%", "violation")], "25 फ़ीसदी vs ceiling 10")

    # A18 — Devanagari digits are explicit, not an accident of \d
    eq([x.value for x in check_percentages(turns("२५% की छूट"), 10)], ["25%"],
       "Devanagari-digit percentage")

    # A17 — values are ASCII-normalised, never script-dependent
    for text in ("दस प्रतिशत", "१० प्रतिशत", "10 percent", "10%"):
        vals = [x.value for x in check_percentages(turns(text), 50)]
        eq(vals, ["10%"], f"ASCII-normalised value for {text!r}")

    # ANTI-REGRESSION: English behaviour that already worked
    eq([x.verdict for x in check_percentages(turns("You get 25% off."), 10)], ["violation"],
       "REGRESSION GUARD: 25% against a 10% ceiling is a violation")
    eq([x.verdict for x in check_percentages(turns("You get 10% off."), 10)], ["ok"],
       "REGRESSION GUARD: 10% against a 10% ceiling is ok")
    eq([x.verdict for x in check_percentages(turns("A flat 12 per cent."), 10)], ["violation"],
       "REGRESSION GUARD: 'per cent' still parses")
    eq(len(check_percentages(turns("Kunal said he wants 30%.", speaker="persona"), 10)), 0,
       "REGRESSION GUARD: a percentage spoken by the CUSTOMER is never an agent defect")
    eq(len(check_percentages(turns("I can do 10 percent."), None)), 0,
       "no ceiling -> no observations")
    # "do" is a Hindi numeral AND an English verb; the digit form must win, not 'do ... percent'
    o = check_percentages(turns("I can do 10 percent, nothing more."), 10)
    eq([(x.value, x.verdict) for x in o], [("10%", "ok")],
       "REGRESSION GUARD: 'do 10 percent' is 10%, not 2%")


def d1_prices() -> None:
    print("D1  currency: suffix confidence, boundaries, Indian grouping, magnitude")

    o = check_prices(turns("Yeh 3999 रुपये ka plan hai."), [2499])
    eq([(x.value, x.verdict, x.confidence) for x in o], [("3999", "violation", "high")],
       "A8: '3999 रुपये' is high confidence, not a demoted review")
    o = check_prices(turns("Yeh 2,499 रुपये ka plan hai."), [2499])
    eq([(x.value, x.verdict, x.confidence) for x in o], [("2499", "ok", "high")],
       "A10: comma-grouped '2,499 रुपये'")
    o = check_prices(turns("Yeh 2499रुपये ka plan hai."), [2499])
    eq([x.value for x in o], ["2499"], "A9: '2499रुपये' with no space still matches")
    o = check_prices(turns("It costs Rs 1,49,900 today."), [2499])
    eq([(x.value, x.verdict, x.confidence) for x in o], [("149900", "violation", "high")],
       "A10/A11: 'Rs 1,49,900' Indian grouping, high confidence")
    o = check_prices(turns("It costs 1,49,900 today."), [2499])
    eq([(x.value, x.verdict, x.confidence) for x in o], [("149900", "review", "medium")],
       "A10: bare '1,49,900' is visible at medium confidence")
    eq([x.value for x in check_prices(turns("Total 3999/- only."), [2499])], ["3999"],
       "'/-' suffix marker")
    eq([x.value for x in check_prices(turns("सिर्फ़ रु. 899 में।"), [899])], ["899"],
       "'रु.' prefix marker")
    eq([x.verdict for x in check_prices(turns("सिर्फ़ रु. 899 में।"), [899])], ["ok"],
       "'रु. 899' against valid_prices_inr [899]")

    # ANTI-REGRESSION: English behaviour that already worked
    o = check_prices(turns("The plan is Rs 1099 per year."), [1499])
    eq([(x.value, x.verdict, x.confidence) for x in o], [("1099", "violation", "high")],
       "REGRESSION GUARD: Rs 1099 outside valid_prices_inr is a violation")
    o = check_prices(turns("The plan is Rs 1499 per year."), [1499])
    eq([(x.value, x.verdict) for x in o], [("1499", "ok")],
       "REGRESSION GUARD: Rs 1499 inside valid_prices_inr is ok")
    o = check_prices(turns("It works out to about 1349 for the year."), [1499])
    eq([(x.value, x.verdict, x.confidence) for x in o], [("1349", "review", "medium")],
       "REGRESSION GUARD: bare integer stays a medium-confidence review")
    eq(len(check_prices(turns("₹4999 chahiye mujhe.", speaker="persona"), [1499])), 0,
       "REGRESSION GUARD: a price spoken by the CUSTOMER is never an agent defect")
    eq(len(check_prices(turns("The plan is Rs 1099."), None)), 0,
       "no valid_prices_inr -> no observations")
    # a percentage must never be re-read as a bare price
    eq(len(check_prices(turns("You get 100% off, sir."), [1499])), 0,
       "REGRESSION GUARD: percentages are not prices")


def d1_dates() -> None:
    print("D1  dates: Devanagari months and digits, numeric DD/MM, unparseable ground truth")

    for text in ("३ अगस्त तक valid hai", "3 अगस्त तक valid hai"):
        o = check_dates(turns(text), ["3 August"])
        eq([x.verdict for x in o], ["ok"], f"{text!r} vs valid_dates ['3 August']")
    o = check_dates(turns("3 सितम्बर तक valid hai"), ["3 August"])
    eq([x.verdict for x in o], ["violation"], "3 सितम्बर vs valid_dates ['3 August']")
    o = check_dates(turns("यह 8 अगस्त तक है।"), ["8 August"])
    eq([(x.verdict, x.confidence) for x in o], [("ok", "high")], "8 अगस्त high confidence")

    # numeric DD/MM: Indian order, medium confidence, and NEVER a violation on its own
    o = check_dates(turns("Valid till 03/08 only."), ["3 August"])
    eq([(x.verdict, x.confidence) for x in o], [("ok", "medium")], "'03/08' reads as 3 August")
    o = check_dates(turns("Valid till 04/09 only."), ["3 August"])
    eq([(x.verdict, x.confidence) for x in o], [("review", "medium")],
       "'04/09' is a review, never a violation — DD/MM vs MM/DD is ambiguous")

    # ANTI-REGRESSION: English behaviour that already worked
    o = check_dates(turns("The offer runs to 15 September."), ["8 August"])
    eq([x.verdict for x in o], ["violation"],
       "REGRESSION GUARD: '15 September' outside valid_dates is a violation")
    eq([x.verdict for x in check_dates(turns("Valid till 8 August."), ["8 August"])], ["ok"],
       "REGRESSION GUARD: '8 August' inside valid_dates is ok")
    eq([x.verdict for x in check_dates(turns("Valid till August 8."), ["8 August"])], ["ok"],
       "REGRESSION GUARD: month-first English order still parses")
    eq(len(check_dates(turns("Main 20 August tak wait karunga.", speaker="persona"), ["8 August"])), 0,
       "REGRESSION GUARD: a date spoken by the CUSTOMER is never an agent defect")
    eq(len(check_dates(turns("Valid till 8 August."), None)), 0,
       "no valid_dates -> no observations")
    # 'may' as a modal verb must not become a date
    eq(len(check_dates(turns("Before I share that, may I ask what happened?"), ["8 August"])), 0,
       "REGRESSION GUARD: 'may I ask' is not a date")

    eq(normalise_dates(["3 August"]), {(3, 8)}, "normalise_dates English")
    eq(normalise_dates(["३ अगस्त"]), {(3, 8)}, "normalise_dates Devanagari")
    eq(normalise_dates(["तीन अगस्त"]), set(), "normalise_dates cannot read spelled days")


# =======================================================================================
# D2 — coverage
# =======================================================================================

def d2_coverage() -> None:
    print("D2  coverage: clean=True may never mean 'parsed nothing'")

    # No ground truth at all: silence must be loud.
    r = run_checks(artifact({}, "Hi Kunal, this is Tara from NovaPlay."))
    for name, c in r["coverage"]["per_check"].items():
        eq(c["status"], "skipped_no_ground_truth", f"no-gt: {name} status")
        eq(c["verdict"], "not_applicable", f"no-gt: {name} verdict")
    eq(r["checks_run"], [], "no-gt: checks_run is empty")
    eq(sorted(s["check"] for s in r["checks_skipped"]),
       ["date", "discount_percentage", "rupee_amount"], "no-gt: checks_skipped lists all three")
    eq(r["clean"], False, "no-gt: clean is False")
    eq(r["status"], "unverified", "no-gt: status")
    eq(r["coverage"]["verdict"], "none", "no-gt: coverage verdict")
    ok("no objective violations" not in r["summary"],
       "no-gt: summary does not claim 'no objective violations'", r["summary"][:120])

    # A14 — valid_dates present but unparseable must never silently return [].
    r = run_checks(artifact({"valid_dates": ["तीन अगस्त"]}, "Yeh 3 अगस्त tak valid hai."))
    c = r["coverage"]["per_check"]["date"]
    eq(c["status"], "skipped_unparseable_ground_truth", "A14: unparseable valid_dates status")
    eq(c["ground_truth_present"], True, "A14: ground_truth_present")
    eq(c["ground_truth_parsed"], False, "A14: ground_truth_parsed")
    eq(c["verdict"], "not_applicable", "A14: verdict")
    eq(r["clean"], False, "A14: clean is False")
    ok(any("date" in b for b in r["coverage"]["blind_spots"]), "A14: blind_spot names the check")

    # THE EXTENSION PROOF: an unsupported script degrades LOUDLY, not silently.
    r = run_checks(artifact({"valid_dates": ["3 August"]},
                            "Ithu 3 ஆகஸ்ட் varai valid."))
    c = r["coverage"]["per_check"]["date"]
    eq(c["status"], "ran", "tamil: date check ran")
    eq(c["detected"], 1, "tamil: mention detected")
    eq(c["parsed"], 0, "tamil: mention not parsed")
    eq(c["unrecognised"], 1, "tamil: unrecognised")
    eq(c["compared"], 0, "tamil: compared")
    eq(c["verdict"], "none", "tamil: per-check verdict")
    eq(c["checked_fraction"], 0.0, "tamil: checked_fraction")
    eq(r["status"], "unverified", "tamil: top-level status")
    eq(r["clean"], False, "tamil: clean is False")
    ok(c["unrecognised_samples"] and "ஆகஸ்ட்" in c["unrecognised_samples"][0],
       "tamil: the unparsed mention is quoted verbatim", str(c["unrecognised_samples"]))
    ok("NOT VERIFIED" in r["summary"] and "NOT evidence of correctness" in r["summary"],
       "tamil: summary carries the degraded wording", r["summary"][:160])
    eq(c["unrecognised_by_script"], {"tamil": 1}, "A20: unrecognised attributed to its script")

    # partial coverage: one parsed mention and one that no recogniser can read
    r = run_checks(artifact({"valid_dates": ["3 August"]},
                            "Valid till 3 August.", "Ithu 3 ஆகஸ்ட் varai valid."))
    c = r["coverage"]["per_check"]["date"]
    eq((c["detected"], c["parsed"], c["compared"]), (2, 1, 1), "partial: detected/parsed/compared")
    eq(c["verdict"], "partial", "partial: per-check verdict")
    eq(r["status"], "partially_verified", "partial: top-level status")
    ok("PARTIALLY VERIFIED" in r["summary"], "partial: summary prefix", r["summary"][:120])

    # every f99e33 fixture: the price check genuinely found nothing, and says so
    for p in sorted(F9.glob("*.json")):
        r = run_checks(load(p))
        c = r["coverage"]["per_check"]["rupee_amount"]
        eq(c["status"], "ran", f"{p.stem}: rupee_amount ran")
        eq(c["detected"], 0, f"{p.stem}: rupee_amount detected")
        eq(c["verdict"], "full", f"{p.stem}: rupee_amount verdict")
        eq(c["checked_fraction"], None, f"{p.stem}: rupee_amount checked_fraction is null")
        ok(any(b.startswith("rupee_amount:") for b in r["coverage"]["blind_spots"]),
           f"{p.stem}: rupee_amount blind_spot recorded", str(r["coverage"]["blind_spots"]))

    # happy-path: clean=True is now conditional on coverage, and both must hold
    r = run_checks(load(F9 / "happy-path.json"))
    eq(r["coverage"]["verdict"], "full", "happy-path: coverage verdict")
    eq(r["clean"], True, "happy-path: clean")
    eq(r["status"], "clean", "happy-path: status")
    eq(r["summary"], "no objective violations", "happy-path: summary")
    eq(sorted(r["checks_run"]), ["date", "discount_percentage", "rupee_amount"],
       "happy-path: checks_run reflects what actually ran")
    eq(r["checks_skipped"], [], "happy-path: nothing skipped")

    # a violation still reports as a violation, and clean goes False
    r = run_checks(artifact({"discount_ceiling_pct": 10, "valid_dates": ["8 August"],
                             "valid_prices_inr": [1499]},
                            "Main aapko 25% de dunga, 15 September tak, Rs 1099 mein."))
    eq(r["violation_count"], 3, "violations counted")
    eq(r["status"], "violations", "status is violations")
    eq(r["clean"], False, "clean is False on violations")
    ok("no objective violations" not in r["summary"], "violation summary")

    # A20 — script census
    r = run_checks(load(F9 / "angry-churner.json"))
    s = r["coverage"]["scripts"]
    eq(s.get("latin", {}).get("turns"), 1, "A20: latin agent turns")
    eq(s.get("devanagari", {}).get("turns"), 6, "A20: devanagari agent turns")
    eq(r["coverage"]["agent_turns_total"], 7, "A20: agent_turns_total")
    eq(r["coverage"]["agent_turns_scanned"], 7, "A20: agent_turns_scanned")
    ok(r["coverage"]["agent_chars_total"] > 0, "A20: agent_chars_total")

    # the contract keys B reads defensively must all be present
    for key in ("checks_run", "checks_skipped", "not_checked_here", "observations",
                "violation_count", "review_count", "clean", "status", "summary", "coverage"):
        ok(key in r, f"run_checks result contains {key!r}")
    for key in ("agent_turns_total", "agent_turns_scanned", "agent_chars_total", "scripts",
                "per_check", "checked_fraction", "verdict", "blind_spots"):
        ok(key in r["coverage"], f"coverage contains {key!r}")
    for key in ("status", "ground_truth_present", "ground_truth_parsed", "ground_truth_raw",
                "ground_truth_normalised", "detected", "parsed", "compared", "unrecognised",
                "unrecognised_by_script", "unrecognised_turns", "unrecognised_samples",
                "observations", "observations_by_verdict", "recognisers", "checked_fraction",
                "verdict"):
        ok(key in r["coverage"]["per_check"]["date"], f"per_check.date contains {key!r}")
    eq(r["coverage"]["per_check"]["date"]["ground_truth_normalised"], [[3, 8]],
       "per_check.date ground_truth_normalised shape")
    ok(json.dumps(r) and True, "run_checks output is JSON-serialisable")


def main() -> int:
    d1_fixtures()
    d1_percentages()
    d1_prices()
    d1_dates()
    d2_coverage()
    if _fails:
        print(f"\nregress_checks: {len(_fails)} FAILED of {_checks}")
        for f in _fails:
            print("  FAIL", f)
        return 1
    print(f"\nregress_checks: ALL OK ({_checks} assertions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
