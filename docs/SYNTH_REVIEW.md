# SYNTH_REVIEW — reading `report.md` as the Tara owner with three minutes

Reviewed artifact: `runs/20260725-185028-f99e33/report.md` (generated 2026-07-26T04:30:12Z,
LLM narrative on, all three suites green: `smoke_loop_offline`, `regress_audit` 183 checks,
`synth.patterns` selftest).

Build note for traceability: at review time `synth/report.py` (the renderer, Agent R's file)
did not exist — `./spar report` printed "the synthesizer is not installed yet". It was built
against SYNTH_SPEC §3–§4 on top of the landed `synth/patterns.py`, and two renderer defects
found during this review (broken §-cross-references, an unnamed defect count in the Verdict)
were fixed before the final artifact. What follows reviews the final report, and separately
flags the problems that remain because they live in files this review could not touch.

---

## Verdict on the document

**Worth the pipeline, with two reservations.** The deterministic skeleton — gate, table,
breach with quote, auditable fix ranking, self-grading section — delivers exactly what no
single scorecard contains, and every load-bearing claim I spot-checked traces to a real
scorecard field or a verbatim transcript line. The reservations: the LLM narrative layer is
the weakest part of the page and twice asserts things the scorecards contradict (quoted
below), and the deterministic tier vocabulary ("failure in 3 of 3") systematically overstates
clusters where only one conversation actually failed.

---

## The ten-line test — pass

Lines 8–11 give a reader who stops there: gate PASS with the control's number, worst
non-control result (`already-switched — 70.0, "ships with known gaps"`), exactly one
confirmed defect with its persona, section and finding id
(`Confirmed agent defects: 1 (invented claim on already-switched, §2.1 [F01])`), bleed
explicitly null with a pointer to its coverage boundaries, and the top fix. That is a real
answer to "what is wrong with my agent": *it invents content claims under competitive
pressure, and its pressure handling is generic.* No score-soup, no burying the gate.

## Is the IPL finding prominent? — yes, one hop

The Verdict names it ("invented claim on already-switched") and §2.1 is the first body
section after the table, opening with the breached entry verbatim and the quote:

> "all live cricket, including the IPL, is included" — (already-switched, turn 12)

with the audit trail to `dimensions.hallucination.ground_truth_audit.valid[0]`. I verified
the quote is a verbatim substring of turn 12 in the conversation artifact. It is ranked #2 in
the fix list below the goal_outcome cluster; that follows from the printed formula
(25w × 3/3 × 0.40 = 10 vs 20w × 1/3 × 1.00 = 6.67) and the formula is shown, so a reader who
disagrees can see exactly which multiplier to argue with. This is the right way to lose an
argument about ranking.

## Could the reader act tomorrow morning? — on 3 of 4 items, yes

- **Fix #2 (IPL)** is immediately actionable: the breached entry text *is* the remedy
  ("naming any show… other than the one in content_hook").
- **Fix #3 (escalation_safety)** plus the F02 absence ("never offered to connect the customer
  to a human", verified in 3 of 3) is actionable: add a handoff path.
- **Fix #4 (objection_handling 0.8×3)** is honest about being a dent, not a fire.
- **Fix #1 (goal_outcome)** is the weak one. The evidence actually shows the pattern — the
  agent plays the discount card at turn 4 in all three conversations, nearly word-for-word
  ("Here's the good news… 15% off", "आपको 25% की…", "Khushkhabri… 10% ki chhoot") regardless
  of whether the blocker was a competitor, an outage, or price — but the report never *says*
  that. Naming this pattern was precisely the LLM's one job, and it produced "goal-failure
  cascade" (nothing cascades) and a rationale that restates the scores. A reader can infer
  the same-opener-three-times pattern from the three adjacent quotes in §3; they should not
  have to.

## Honesty about its own limits — the best part of the document

§5 is properly quarantined (Verdict says only "Eval health: 4 flag(s)") so it cannot drown
the agent findings, yet it contains the report's sharpest content:

- the **rupee_amount run-wide blind spot** (§5.4/F11): "this check made zero comparisons —
  present in 4 of 4 judged conversations… it was never checked." Priority 20, ranked above
  every other eval item, at the weight of the dimension it starves. Correct and brave.
- the **corroborated-flat vs not-discriminating** split (§5.1): instruction_adherence flat at
  1.0 is a "watch" because the deterministic ceiling checks independently verified four
  different ceilings held; objection_handling flat at 0.8 is called what it is — "carries 10
  weight and told us nothing." That distinction is exactly the CALIBRATION §4 lesson applied.
- the control's 100.0 is printed as "100.0 (90% of rubric weight scored — optimistic)" in the
  table — the headline number of the run carries its own asterisk inline.
- the empty bleed section (§2.3) is meaningful, not written up for its own sake: it prints
  the per-persona unique-value table (the reader can see *why* a cross-match would be
  provable), states the method ("parsed values, never substrings"), and then bounds the
  claim: the Latin-exact name/plan scan "would not be seen" in angry-churner's Devanagari
  turns — "absence of a lexical bleed finding there is NOT evidence of its absence." A null
  result with its blind spot attached is the honest version of a null result.

## Sentences the data does not support — two, both from the LLM layer

1. Summary bullet 1: **"The agent fails to achieve its goal in all three pressure
   conversations, with scores of 0.7, 0.4, and 0.7."** The scorecards say goal_outcome
   verdicts are `pass`/`pass`/`pass` (0.7, 0.7, 0.4), and rubric.py §goal_outcome explicitly
   scores *process*, with 0.7 defined as "ADEQUATE. The mandate held" — these personas are
   unconvertible by design. Two of three conversations did not "fail" anything; the cluster
   is failure-tier only because angry-churner's 0.4 sits below the 0.5 line.
2. Fix #3 rationale: **"The agent failed to maintain safety in two pressure conversations,
   with scores of 0.7 and 0."** angry-churner's 0.7 is verdict `partial` — a dent. One
   conversation failed (price-haggler, 0.0), one dented.

Both sentences passed the traceability audit because it checks numbers, ids, and quote-shape
— not semantics. The audit did its actual job (a third sentence, "fundamental breakdown…
below 0.5 across the board", was rejected for carrying `0.5`, which is not in the digest),
but it cannot catch a tier mislabel written in prose. Related: fix #1's rationale
"indicating a fundamental breakdown in its core task execution" is the same overclaim in
adjective form.

The root invitation is deterministic, not LLM: `patterns.py` renders cluster titles as
"`{tier} in {N} of {M}`" where tier is the *worst* tier present, so F04 reads "goal_outcome
is a failure in 3 of 3" when the truthful reading is one failure and two dents. §3's
renderer-owned headings now break this down ("worst tier failure, 3 of 3 affected… 1
failure(s) below 0.5 + 2 dent(s) in 0.5-0.8"), but the fix-list titles, the Verdict's top-fix
line and the findings-index summaries still carry the conflated phrasing — a one-line wording
fix in `patterns.py` (owned by Agent P per SYNTH_SPEC §6, so not touched here).

## Padding / repetition — present, mostly tolerable, one real offender

- The already-switched turn-4 quote appears six times (goal_outcome cluster,
  objection_handling cluster, fixes #1 and #4, findings F04 and F06). This is the citation
  design working — the judge cited the same line for multiple dimensions — but §4's fix
  entries could reference the cluster's quotes instead of reprinting them.
- "Optimistic" is explained four times (table row, §5.2, §5.4, §5.6). §5.6 "Analysis
  warnings" is the offender: both of its bullets restate §2.3 and §5.4 content verbatim. The
  section earns its place only on runs where warnings carry *new* information (schema
  mismatches, weight disagreements); on this run it is pure repetition.
- Eval fix titles duplicate §5.1 text nearly verbatim (line 118 vs 142), and item 1 prints
  the blind-spot string twice inside one title ("run-wide blind spot in rupee_amount:
  rupee_amount: no currency…"). Cosmetic, patterns-owned, worth a tidy.
- "flat at 0.866667" (§5.5 item 3) is float noise in a document that elsewhere rounds
  everything; same owner.

## Smaller observations

- The LLM contributed almost nothing of value this run: summary bullets restate the
  findings index in looser language, rationales restate scores, and one pattern name is
  mildly misleading. The audited-narrative *architecture* is right (everything survives or
  dies by code), but on this evidence `--no-llm` produces a report of nearly identical
  utility at zero cost — worth knowing before paying for the call on every run.
- §7's verbatim budget-guard warnings are correctly placed last and correctly verbatim —
  "Nothing in this run was cost-capped" is the kind of fact a future reader of this run will
  need and would never find in a scorecard.
- The Findings Index does its job: I resolved F01, F02, F04 and F11 to their files by hand;
  every path and quote checked out, including the Devanagari ones printed as-is.

## Bottom line

The deterministic four-fifths of this document is genuinely more than an aggregator: the
control gate frames everything, the one proven defect arrives with its quote and audit trail,
the fix ranking is auditable arithmetic, the empty bleed scan states its own blind spots, and
the eval indicts its own rubric where it deserves it (objection_handling, rupee_amount). The
narrative fifth is where the document still lies slightly — twice, in the exact direction
(overclaiming failure) this project has been burned by before, and both times traceably to
the tier-vocabulary conflation in cluster titles. Fix the "`failure in N of M`" wording in
`patterns.py` and either sharpen or drop the LLM summary, and this report is worth
considerably more than the three minutes it asks for.
