/**
 * Word-level diff between what a persona SAID and what the agent's ASR HEARD.
 *
 * The hard part is that the two sides are usually written in *different scripts*.
 * The persona speaks romanised Hindi and the agent's ASR returns Devanagari:
 *
 *   said  : "Arre, maine socha tha ki abhi series nahi aa rahi, toh thoda save kar loon."
 *   heard : "अरे, मैंने सथोड़ा save कर लूँ।"
 *
 * A naive string diff marks every token as changed and hides the actual finding,
 * which is that the *middle of the sentence never reached the agent*. So each token
 * is reduced to a script-independent consonant skeleton before matching:
 *
 *   "maine"  -> m,n         -> "mn"
 *   "मैंने"   -> m,(anusvara)n,n -> "mn"     ✓ same word
 *   "socha"  -> s,c(h drop) -> "sc"
 *   "थोड़ा"   -> th->t, d    -> "td"          ✗ genuinely different
 *
 * Vowels carry almost no information across transliteration schemes, aspiration
 * (kh/gh/th/dh/ch/sh) is inconsistently romanised, and z/j, f/ph, q/k, w/v are
 * routinely swapped — so all of those are folded away. What survives is a rough
 * consonant fingerprint that is stable across scripts.
 *
 * This is deliberately lossy. It is a *presentation* aid: it decides which words
 * to paint red. Both original strings are always rendered verbatim next to each
 * other, so a mis-alignment can soften the highlight but can never hide or
 * invent transcription loss.
 */

/** Devanagari consonants → rough Latin. Vowels/matras are dropped entirely. */
const DEVANAGARI_CONSONANTS: Record<string, string> = {
  'क': 'k', // क
  'ख': 'kh', // ख
  'ग': 'g', // ग
  'घ': 'gh', // घ
  'ङ': 'ng', // ङ
  'च': 'c', // च
  'छ': 'ch', // छ
  'ज': 'j', // ज
  'झ': 'jh', // झ
  'ञ': 'ny', // ञ
  'ट': 't', // ट
  'ठ': 'th', // ठ
  'ड': 'd', // ड
  'ढ': 'dh', // ढ
  'ण': 'n', // ण
  'त': 't', // त
  'थ': 'th', // थ
  'द': 'd', // द
  'ध': 'dh', // ध
  'न': 'n', // न
  'प': 'p', // प
  'फ': 'ph', // फ
  'ब': 'b', // ब
  'भ': 'bh', // भ
  'म': 'm', // म
  'य': 'y', // य
  'र': 'r', // र
  'ल': 'l', // ल
  'ळ': 'l', // ळ
  'व': 'v', // व
  'श': 'sh', // श
  'ष': 'sh', // ष
  'स': 's', // स
  'ह': 'h', // ह
  // Precomposed nukta forms, in case the text is not decomposed.
  'क़': 'k', // क़
  'ख़': 'kh', // ख़
  'ग़': 'g', // ग़
  'ज़': 'j', // ज़  (folded to j; romanised "z" folds to j too)
  'ड़': 'r', // ड़
  'ढ़': 'rh', // ढ़
  'फ़': 'ph', // फ़
  'य़': 'y', // य़
};

const ANUSVARA = 'ं';
const CHANDRABINDU = 'ँ';

/** U+FFFD — the ASR emitted bytes that could not be decoded at all. */
export const REPLACEMENT_CHAR = '�';

const LATIN_VOWELS = 'aeiou';

/**
 * Reduce a token to a script-independent consonant skeleton.
 * Returns "" for pure-vowel tokens ("aa", "आ") and pure punctuation.
 */
export function skeleton(token: string): string {
  const lower = token.normalize('NFC').toLowerCase();
  let raw = '';

  for (const ch of lower) {
    const code = ch.codePointAt(0) ?? 0;

    // Devanagari block
    if (code >= 0x0900 && code <= 0x097f) {
      const mapped = DEVANAGARI_CONSONANTS[ch];
      if (mapped) {
        raw += mapped;
      } else if (ch === ANUSVARA || ch === CHANDRABINDU) {
        raw += 'n';
      } else if (code >= 0x0966 && code <= 0x096f) {
        raw += String(code - 0x0966); // Devanagari digits
      }
      // vowels, matras, virama, nukta, danda → dropped
      continue;
    }

    if (ch >= 'a' && ch <= 'z') {
      if (!LATIN_VOWELS.includes(ch)) raw += ch;
      continue;
    }
    if (ch >= '0' && ch <= '9') {
      raw += ch;
      continue;
    }
    if (ch === '%') raw += '%';
    // everything else dropped
  }

  // Fold the transliteration ambiguities, and drop aspiration.
  let folded = '';
  for (const ch of raw) {
    let c = ch;
    if (c === 'z') c = 'j';
    else if (c === 'f') c = 'p';
    else if (c === 'q') c = 'k';
    else if (c === 'w') c = 'v';
    else if (c === 'x') c = 'ks';

    // "h" straight after another consonant is aspiration, not a phoneme.
    if (c === 'h' && folded.length > 0) continue;
    folded += c;
  }

  // Collapse doubled consonants ("arre" → "are" → "r").
  let collapsed = '';
  for (const ch of folded) {
    if (ch !== collapsed[collapsed.length - 1]) collapsed += ch;
  }
  return collapsed;
}

/** Lowercased token with punctuation stripped — used for zero-skeleton tokens. */
function normaliseToken(token: string): string {
  return token
    .normalize('NFC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}%]/gu, '');
}

interface Tok {
  /** Verbatim, punctuation and all — this is what gets rendered. */
  text: string;
  norm: string;
  key: string;
}

function tokenize(text: string): Tok[] {
  return text
    .split(/\s+/)
    .filter((t) => t.length > 0)
    .map((t) => ({ text: t, norm: normaliseToken(t), key: skeleton(t) }));
}

function tokensMatch(a: Tok, b: Tok): boolean {
  if (a.key && a.key === b.key) return true;
  // Pure-vowel or punctuation-only tokens: fall back to literal equality.
  if (!a.key && !b.key) return a.norm.length > 0 && a.norm === b.norm;
  if (!a.key || !b.key) return false;
  // ASR frequently glues two words together ("सथोड़ा" = "स" + "थोड़ा").
  if (a.key.length >= 2 && b.key.length >= 2) {
    if (a.key.includes(b.key) || b.key.includes(a.key)) return true;
  }
  return false;
}

/** Longest common subsequence over the fuzzy token equality above. */
function lcsPairs(a: Tok[], b: Tok[]): Array<[number, number]> {
  const n = a.length;
  const m = b.length;
  if (n === 0 || m === 0) return [];

  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0)
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = tokensMatch(a[i], b[j])
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const pairs: Array<[number, number]> = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (tokensMatch(a[i], b[j])) {
      pairs.push([i, j]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++;
    } else {
      j++;
    }
  }
  return pairs;
}

export type DiffOp =
  /** Present on both sides — the agent heard this word. */
  | { type: 'equal'; said: string[]; heard: string[] }
  /** The persona said it; the ASR never produced it. */
  | { type: 'drop'; said: string[] }
  /** The ASR produced it; the persona never said it. */
  | { type: 'add'; heard: string[] };

export interface WordDiff {
  ops: DiffOp[];
  saidCount: number;
  heardCount: number;
  matchedCount: number;
  droppedCount: number;
  addedCount: number;
  /** 0–1: share of the persona's words that survived into the agent's transcript. */
  captured: number;
  /** True when the two strings are word-for-word the same after normalisation. */
  identical: boolean;
  /** The ASR output contains U+FFFD — bytes it could not decode at all. */
  hasUndecodableBytes: boolean;
}

/**
 * Diff what was said against what was heard.
 * Both inputs are rendered verbatim by the caller; this only classifies tokens.
 */
export function wordDiff(said: string, heard: string): WordDiff {
  const a = tokenize(said ?? '');
  const b = tokenize(heard ?? '');
  const pairs = lcsPairs(a, b);

  const ops: DiffOp[] = [];
  let ai = 0;
  let bi = 0;

  const flushDrops = (until: number) => {
    if (until > ai) {
      ops.push({ type: 'drop', said: a.slice(ai, until).map((t) => t.text) });
      ai = until;
    }
  };
  const flushAdds = (until: number) => {
    if (until > bi) {
      ops.push({ type: 'add', heard: b.slice(bi, until).map((t) => t.text) });
      bi = until;
    }
  };

  for (const [pa, pb] of pairs) {
    flushDrops(pa);
    flushAdds(pb);
    const last = ops[ops.length - 1];
    if (last && last.type === 'equal') {
      last.said.push(a[pa].text);
      last.heard.push(b[pb].text);
    } else {
      ops.push({ type: 'equal', said: [a[pa].text], heard: [b[pb].text] });
    }
    ai = pa + 1;
    bi = pb + 1;
  }
  flushDrops(a.length);
  flushAdds(b.length);

  const matchedCount = pairs.length;
  const droppedCount = a.length - matchedCount;
  const addedCount = b.length - matchedCount;

  return {
    ops,
    saidCount: a.length,
    heardCount: b.length,
    matchedCount,
    droppedCount,
    addedCount,
    captured: a.length === 0 ? 1 : matchedCount / a.length,
    identical: droppedCount === 0 && addedCount === 0 && a.length > 0,
    hasUndecodableBytes: (heard ?? '').includes(REPLACEMENT_CHAR),
  };
}
