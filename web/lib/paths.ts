/**
 * Filesystem locations + path-traversal defence.
 *
 * `runId` and `personaId` arrive straight off the URL, so every path built from
 * them goes through `safeSegment()` AND a final containment check.
 */

import fs from 'fs';
import path from 'path';

/** Repo root — `web/` is one level down from it. */
export const ROOT = path.join(process.cwd(), '..');
export const RUNS_DIR = path.join(ROOT, 'runs');

/** Allowed characters in a run id or persona id. No slashes, no dots-only. */
const SEGMENT_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/**
 * Returns the segment if it is safe to interpolate into a path, else null.
 * Rejects `..`, absolute paths, slashes, backslashes, NUL, URL-encoded escapes
 * (they arrive decoded, so `%2e%2e` shows up as `..`) and leading dots.
 */
export function safeSegment(value: string | undefined | null): string | null {
  if (typeof value !== 'string') return null;
  const s = value.trim();
  if (!s) return null;
  if (!SEGMENT_RE.test(s)) return null;
  if (s.includes('..')) return null;
  if (s !== path.basename(s)) return null;
  return s;
}

/** Belt-and-braces: assert `child` really lives under `parent` after resolution. */
export function isInside(parent: string, child: string): boolean {
  const p = path.resolve(parent);
  const c = path.resolve(child);
  return c === p || c.startsWith(p + path.sep);
}

/** Absolute path to a run directory, or null if the id is unsafe/missing. */
export function runDir(runId: string): string | null {
  const id = safeSegment(runId);
  if (!id) return null;
  const dir = path.join(RUNS_DIR, id);
  if (!isInside(RUNS_DIR, dir)) return null;
  return dir;
}

/** Absolute path to a persona's audio directory, or null. */
export function personaAudioDir(runId: string, personaId: string): string | null {
  const dir = runDir(runId);
  const persona = safeSegment(personaId);
  if (!dir || !persona) return null;
  const audioDir = path.join(dir, 'audio', persona);
  if (!isInside(dir, audioDir)) return null;
  return audioDir;
}

/**
 * Absolute path to one turn's PCM file, or null.
 * Naming convention on disk: `turn_<idx>_<speaker>.pcm`.
 */
export function turnPcmPath(
  runId: string,
  personaId: string,
  turnIdx: number,
  speaker: 'agent' | 'persona'
): string | null {
  const audioDir = personaAudioDir(runId, personaId);
  if (!audioDir) return null;
  if (!Number.isInteger(turnIdx) || turnIdx < 0 || turnIdx > 100000) return null;
  const file = path.join(audioDir, `turn_${turnIdx}_${speaker}.pcm`);
  if (!isInside(audioDir, file)) return null;
  return file;
}

/** Existence check that never throws. */
export function exists(p: string | null): boolean {
  if (!p) return false;
  try {
    return fs.existsSync(p);
  } catch {
    return false;
  }
}

export function isDir(p: string | null): boolean {
  if (!p) return false;
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

/** Byte length of a file, or null if it is missing/unreadable. */
export function fileSize(p: string | null): number | null {
  if (!p) return null;
  try {
    const st = fs.statSync(p);
    return st.isFile() ? st.size : null;
  } catch {
    return null;
  }
}

/** `readdir` that returns [] instead of throwing. */
export function listDir(p: string | null): string[] {
  if (!p) return [];
  try {
    return fs.readdirSync(p);
  } catch {
    return [];
  }
}

/** JSON read that returns null instead of throwing on missing/corrupt files. */
export function readJson<T = unknown>(p: string | null): T | null {
  if (!p) return null;
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8')) as T;
  } catch {
    return null;
  }
}
