#!/usr/bin/env python3
"""scripts/probe_evidence_norm.py — is the judge fabricating quotes, or is the audit wrong?

Read-only. Costs nothing, calls no API, mutates nothing. Run:

    PYTHONPATH=. uv run --python 3.12 python scripts/probe_evidence_norm.py

For every entry in evidence_audit.rejected_detail across every scorecard in a run, this
replays the quote against the transcript under a LADDER of progressively more forgiving
match techniques, and reports the FIRST one that locates it:

    raw -> judge._norm -> NFC -> NFD -> NFKC -> casefold -> strip ZWJ/ZWNJ ->
    strip combining marks -> fold Devanagari danda/punctuation -> strip all punctuation ->
    fuzzy (difflib best window)

It also reports WHERE it was found (the cited turn / any turn of the right speaker / any
turn at all), because "not verbatim in turn N" has three very different causes:
    (a) the text differs by codepoints          -> normalisation artefact, audit is wrong
    (b) the text is verbatim but in another turn -> mis-cited index, audit is arguably right
    (c) the text is nowhere                      -> fabrication, audit is right

CAVEAT, stated up front: judge.py stores rejected quotes truncated to 160 chars
(`e.quote[:160]`). Truncation is a PREFIX, so "prefix not found" still proves "full quote
not found" — a NEGATIVE result is sound. A POSITIVE result on a truncated quote only proves
the prefix matched; those rows are flagged `truncated=True`.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from judge.judge import _norm as judge_norm  # noqa: E402  the exact function under suspicion
from judge.rubric import BY_KEY              # noqa: E402  evidence_from per dimension

RUN_ID = sys.argv[1] if len(sys.argv) > 1 else "20260725-185028-f99e33"
RUN = ROOT / "runs" / RUN_ID

ZW = {"‌", "‍", "​", "﻿", "­"}
# Devanagari danda / double danda, plus the ASCII and fullwidth stops they get swapped with.
DANDA = {"।", "॥"}
PUNCT_FOLD = str.maketrans({c: " " for c in ".,!?;:।॥\"'()-–—…"})


# ── the technique ladder ─────────────────────────────────────────────────────────────────

def t_raw(s: str) -> str:
    return s


def t_norm(s: str) -> str:
    return judge_norm(s)


def t_nfc(s: str) -> str:
    return judge_norm(unicodedata.normalize("NFC", s))


def t_nfd(s: str) -> str:
    return judge_norm(unicodedata.normalize("NFD", s))


def t_nfkc(s: str) -> str:
    return judge_norm(unicodedata.normalize("NFKC", s))


def t_casefold(s: str) -> str:
    return judge_norm(unicodedata.normalize("NFKC", s)).casefold()


def t_zw(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    return judge_norm("".join(c for c in s if c not in ZW))


def t_nomarks(s: str) -> str:
    """Strip every combining mark. Destroys Devanagari vowel signs — deliberately the most
    aggressive Unicode-level test. If a quote only matches HERE, the difference was matks."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return judge_norm(s)


def t_danda(s: str) -> str:
    """Fold Devanagari danda to an ASCII full stop (and vice versa) — the classic
    'model typed . where the transcript has ।' failure."""
    s = unicodedata.normalize("NFC", s)
    s = "".join("." if c in DANDA else c for c in s)
    return judge_norm(s)


def t_nopunct(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = "".join(c for c in s if c not in ZW)
    return judge_norm(s.translate(PUNCT_FOLD))


LADDER = [
    ("raw substring", t_raw),
    ("judge _norm() (current)", t_norm),
    ("NFC + _norm", t_nfc),
    ("NFD + _norm", t_nfd),
    ("NFKC + _norm", t_nfkc),
    ("NFKC + casefold", t_casefold),
    ("strip ZWJ/ZWNJ", t_zw),
    ("strip combining marks", t_nomarks),
    ("fold danda->period", t_danda),
    ("strip all punctuation", t_nopunct),
]

FUZZ_THRESHOLD = 0.90


def best_fuzzy(q: str, text: str) -> float:
    """Best similarity of q against any same-length window of text, punctuation-folded."""
    a, b = t_nopunct(q), t_nopunct(text)
    if not a or not b:
        return 0.0
    if len(a) >= len(b):
        return SequenceMatcher(None, a, b).ratio()
    best = 0.0
    step = max(1, len(a) // 8)
    for i in range(0, len(b) - len(a) + 1, step):
        best = max(best, SequenceMatcher(None, a, b[i:i + len(a)]).ratio())
    return best


# ── codepoint forensics ──────────────────────────────────────────────────────────────────

def cp(s: str) -> str:
    return " ".join(f"U+{ord(c):04X}" for c in s)


def hexdump(label: str, s: str) -> str:
    rows = [f"    {label}:"]
    for c in s:
        try:
            name = unicodedata.name(c)
        except ValueError:
            name = "<unnamed>"
        rows.append(f"      U+{ord(c):04X}  {c!r:<10} {name}")
    return "\n".join(rows)


def first_divergence(q: str, text: str) -> tuple[int, str] | None:
    """Align the quote against its best-matching window in text; return the first index
    where they differ plus a rendered hexdump of the neighbourhood."""
    if q in text:
        return None
    # anchor on the longest common prefix against the best offset
    sm = SequenceMatcher(None, q, text)
    m = sm.find_longest_match(0, len(q), 0, len(text))
    if m.size == 0:
        return None
    qi, ti = m.a + m.size, m.b + m.size
    qc = q[qi] if qi < len(q) else ""
    tc = text[ti] if ti < len(text) else ""
    ctx_q, ctx_t = q[max(0, qi - 6):qi + 4], text[max(0, ti - 6):ti + 4]
    out = [
        f"    first divergence at quote index {qi} (transcript index {ti})",
        f"    quote      ...{ctx_q!r}  -> {cp(ctx_q)}",
        f"    transcript ...{ctx_t!r}  -> {cp(ctx_t)}",
        f"    differing char: quote {('U+%04X %r' % (ord(qc), qc)) if qc else '<end>'}"
        f"   vs transcript {('U+%04X %r' % (ord(tc), tc)) if tc else '<end>'}",
    ]
    if qc:
        out.append(hexdump("quote char", qc))
    if tc:
        out.append(hexdump("transcript char", tc))
    return qi, "\n".join(out)


# ── probe ────────────────────────────────────────────────────────────────────────────────

def probe_one(quote: str, cited_turn, turns: list[dict], want_speaker: str) -> dict:
    res: dict = {"quote": quote, "cited_turn": cited_turn, "want_speaker": want_speaker}

    def hit(tech_fn, idxs) -> list[int]:
        tq = tech_fn(quote)
        return [i for i in idxs if tq and tq in tech_fn(turns[i].get("text") or "")]

    cited = [cited_turn] if isinstance(cited_turn, int) and 0 <= cited_turn < len(turns) else []
    right = [i for i, t in enumerate(turns)
             if want_speaker == "any" or t.get("speaker") == want_speaker]
    allt = list(range(len(turns)))

    for name, fn in LADDER:
        in_cited = hit(fn, cited)
        in_right = hit(fn, right)
        in_all = hit(fn, allt)
        if in_cited or in_right or in_all:
            res.update(
                technique=name,
                found_in_cited_turn=bool(in_cited),
                found_in_right_speaker_turns=in_right,
                found_in_any_turn=in_all,
                speakers=[turns[i].get("speaker") for i in in_all],
            )
            return res

    # nothing matched exactly under any normalisation — how close is the closest text?
    scored = sorted(((best_fuzzy(quote, t.get("text") or ""), i) for i in allt), reverse=True)
    top = scored[0] if scored else (0.0, None)
    res.update(
        technique=f"fuzzy {top[0]:.3f} @ turn {top[1]}" if top[0] >= FUZZ_THRESHOLD else None,
        fuzzy_best=round(top[0], 4), fuzzy_turn=top[1],
        found_in_cited_turn=False, found_in_right_speaker_turns=[], found_in_any_turn=[],
    )
    return res


def main() -> int:
    convs = RUN / "conversations"
    cards = RUN / "scorecards"
    if not cards.is_dir():
        print(f"no scorecards in {RUN}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for cpath in sorted(cards.glob("*.json")):
        card = json.loads(cpath.read_text())
        pid = card.get("persona_id", cpath.stem)
        art = json.loads((convs / f"{pid}.json").read_text())
        turns = art.get("turns") or []

        rejected = (card.get("evidence_audit") or {}).get("rejected_detail") or []
        print(f"\n{'=' * 92}\n{pid}  —  {len(rejected)} rejected of "
              f"{(card.get('evidence_audit') or {}).get('total')}\n{'=' * 92}")

        for r in rejected:
            dim = r.get("dimension")
            want = BY_KEY[dim].evidence_from if dim in BY_KEY else "any"
            q = r.get("quote") or ""
            truncated = len(q) >= 160
            out = probe_one(q, r.get("turn"), turns, want)
            out.update(persona=pid, dimension=dim, audit_reason=r.get("reason"),
                       truncated=truncated)
            rows.append(out)

            print(f"\n  [{dim}] cited turn {r.get('turn')}  (evidence_from={want})"
                  f"{'  [QUOTE TRUNCATED AT 160 CHARS]' if truncated else ''}")
            print(f"    audit said : {r.get('reason')}")
            print(f"    quote      : {q[:110]}")
            if out.get("technique"):
                print(f"    FOUND BY   : {out['technique']}")
                print(f"    in cited turn? {out['found_in_cited_turn']}   "
                      f"right-speaker turns {out['found_in_right_speaker_turns']}   "
                      f"any turn {out['found_in_any_turn']} "
                      f"({out.get('speakers')})")
            else:
                print(f"    FOUND BY   : NOTHING. best fuzzy {out['fuzzy_best']} "
                      f"@ turn {out['fuzzy_turn']}")

            # codepoint forensics against the cited turn, whenever one exists
            ct = r.get("turn")
            if isinstance(ct, int) and 0 <= ct < len(turns):
                d = first_divergence(judge_norm(q), judge_norm(turns[ct].get("text") or ""))
                if d:
                    print(d[1])
                else:
                    print("    (no divergence vs cited turn under _norm — exact prefix match)")

    # ── is an NFC/NFD divergence even PRESENT in the corpus? ─────────────────────────────
    print(f"\n\n{'=' * 92}\nUNICODE FORM AUDIT (is the stated hypothesis' mechanism even here?)"
          f"\n{'=' * 92}")
    for path in sorted(convs.glob("*.json")):
        art = json.loads(path.read_text())
        for t in art.get("turns") or []:
            s = t.get("text") or ""
            if unicodedata.normalize("NFC", s) != s:
                print(f"  {art.get('persona_id')} turn {t.get('idx')} ({t.get('speaker')}) "
                      f"is NOT in NFC:")
                n = unicodedata.normalize("NFC", s)
                sm = SequenceMatcher(None, s, n)
                for tag, i1, i2, j1, j2 in sm.get_opcodes():
                    if tag == "equal":
                        continue
                    print(f"      stored  {s[i1:i2]!r}  {cp(s[i1:i2])}")
                    print(f"      NFC     {n[j1:j2]!r}  {cp(n[j1:j2])}")

    # ── summary ──────────────────────────────────────────────────────────────────────────
    print(f"\n\n{'=' * 92}\nSUMMARY\n{'=' * 92}")
    buckets: dict[str, list[dict]] = {
        "UNICODE-FORM": [],      # only NFC/NFD/NFKC rungs recovered it
        "PUNCTUATION": [],       # verbatim at the cited turn bar a punctuation swap
        "MIS-CITED": [],         # verbatim SOMEWHERE, but not the turn the model named
        "TRUNCATED": [],         # 160-char prefix matches; the real divergence is unrecoverable
        "ABSENT": [],            # nothing found it — fabrication
    }
    unicode_rungs = {"NFC + _norm", "NFD + _norm", "NFKC + _norm", "NFKC + casefold",
                     "strip ZWJ/ZWNJ", "strip combining marks"}
    for r in rows:
        tech = r.get("technique")
        if not tech:
            buckets["ABSENT"].append(r)
        elif not r["found_in_cited_turn"]:
            buckets["MIS-CITED"].append(r)
        elif tech in unicode_rungs:
            buckets["UNICODE-FORM"].append(r)
        elif tech == "raw substring":
            # the audit rejected it, yet the stored text matches raw at the cited turn — the
            # only way both are true is that the stored quote was truncated at 160 chars.
            buckets["TRUNCATED" if r["truncated"] else "UNICODE-FORM"].append(r)
        else:
            buckets["PUNCTUATION"].append(r)

    print(f"  rejected items examined : {len(rows)}\n")
    for label, group in buckets.items():
        print(f"  {label:<14} {len(group)}")
        for r in group:
            print(f"      {r['persona']:<17} {r['dimension']:<22} cited turn "
                  f"{r['cited_turn']:<3} via {r.get('technique') or 'NOTHING'}"
                  + (f"  [really in turn {r['found_in_any_turn']} "
                     f"{r.get('speakers')}]" if not r["found_in_cited_turn"] else ""))

    # ── fix simulation: LAYERED and NARROW, each layer measured on its own ───────────────
    # Layer A alone shows how much is pure punctuation-rendering. Layer B alone shows how much
    # is a mis-cited index that the existing relocation fallback would already have caught if
    # it were reachable. Layer C is deliberately included to show it OVER-CORRECTS.
    print(f"\n{'=' * 92}\nFIX SIMULATION (layered)\n{'=' * 92}")
    import judge.judge as J

    def norm_A(s: str) -> str:
        """NFC + fold danda<->period. Narrow: danda and full stop are the SAME mark in two
        scripts. Does NOT touch ? ! , — those carry meaning."""
        s = unicodedata.normalize("NFC", s or "")
        s = "".join(c for c in s if c not in ZW)
        s = s.replace("’", "'").replace("“", '"').replace("”", '"')
        s = "".join("." if c in DANDA else c for c in s)
        return " ".join(s.split()).strip().casefold()

    def norm_C(s: str) -> str:
        """Layer A + ignore the quote's terminal punctuation. UNSAFE — see output."""
        return norm_A(s).rstrip(".?!,;: ")

    def audit_with_relocation(items, turns, want):
        """Layer B: the existing 'uniquely locatable' fallback, made REACHABLE when the model
        supplies an in-range but wrong index. judge.audit_evidence `continue`s before it."""
        out = []
        for it in items:
            q = J._norm(str(it.get("quote") or "").strip().strip('"').strip())
            idx = it.get("turn")
            idx = int(idx) if isinstance(idx, int) else None
            ok_at = (idx is not None and 0 <= idx < len(turns)
                     and q in J._norm(turns[idx].get("text") or "")
                     and (want == "any" or turns[idx].get("speaker") == want))
            if ok_at:
                out.append((True, idx, "verified at cited turn"))
                continue
            cands = [i for i, t in enumerate(turns)
                     if (want == "any" or t.get("speaker") == want)
                     and q in J._norm(t.get("text") or "")]
            if len(cands) == 1:
                out.append((True, cands[0], f"relocated to turn {cands[0]}"))
            elif not cands:
                out.append((False, idx, f"in no {want} turn"))
            else:
                out.append((False, idx, f"ambiguous {cands}"))
        return out

    orig = J._norm
    variants = [
        # Baseline. Anything "rescued" HERE passes under the shipped matcher, which is only
        # possible because judge.py stored a 160-char PREFIX — proof those rows are untestable
        # from disk, not proof they were valid.
        ("0  current _norm ", orig, False),
        ("A  NFC+danda fold", norm_A, False),
        ("A+B  +relocation ", norm_A, True),
        ("A+B+C +term.punct", norm_C, True),
    ]
    try:
        for label, fn, relocate in variants:
            J._norm = fn
            n_ok = 0
            lines = []
            for cpath in sorted(cards.glob("*.json")):
                card = json.loads(cpath.read_text())
                pid = card.get("persona_id", cpath.stem)
                turns = json.loads((convs / f"{pid}.json").read_text()).get("turns") or []
                for r in (card.get("evidence_audit") or {}).get("rejected_detail") or []:
                    dim = r.get("dimension")
                    want = BY_KEY[dim].evidence_from if dim in BY_KEY else "any"
                    item = [{"turn": r.get("turn"), "quote": r.get("quote")}]
                    if relocate:
                        ok, at, why = audit_with_relocation(item, turns, want)[0]
                    else:
                        c = J.audit_evidence(item, turns, want)[0]
                        ok, at, why = c.ok, c.turn, c.reason
                    n_ok += ok
                    lines.append(f"      {pid:<17} {dim:<22} cited {r.get('turn'):<3} -> "
                                 f"{'OK  ' if ok else 'REJ '} turn {at}  ({why})")
            print(f"\n  {label}   rescued {n_ok}/{len(rows)}")
            print("\n".join(lines))
    finally:
        J._norm = orig

    print("\n  NOTE on layer C: it 'rescues' angry-churner/language_handling by matching the "
          "\n  quote \"Hindi!\" (the CUSTOMER's answer, turn 1) against the AGENT's question "
          "\n  \"...English or Hindi?\" in turn 0. Two opposite utterances, one match. Stripping "
          "\n  terminal ? and ! manufactures evidence. Do not ship layer C.")

    (RUN / "evidence_norm_probe.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\n  machine-readable: {RUN / 'evidence_norm_probe.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
