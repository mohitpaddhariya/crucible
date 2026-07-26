/**
 * Turn-level and whole-conversation audio assembly.
 *
 * Disk holds raw PCM per turn. This module works out which turns exist, in what
 * order, and stitches them into one playable WAV with a beat of silence between
 * speakers — the artifact that makes "two AI agents actually talked" undeniable.
 */

import fs from 'fs';

import { listDir, personaAudioDir } from './paths';
import { readConversation, turnAudioFile } from './runs';
import type { AudioTimeline, Speaker, TimelineEntry } from './types';
import { PCM_SAMPLE_RATE } from './types';
import { concatPcmWithGaps, pcmDurationS, silenceBytes, WAV_HEADER_BYTES } from './wav';

/** Silence inserted between consecutive turns in the stitched WAV. */
export const TURN_GAP_S = 0.5;

export type TurnAudioRef = {
  idx: number;
  speaker: Speaker;
  file: string;
  size: number;
};

const PCM_NAME_RE = /^turn_(\d+)_(agent|persona)\.pcm$/;

/**
 * Every turn with audio for this persona, in `idx` order.
 *
 * Built from the conversation artifact when there is one (it is authoritative
 * about turn order and speaker), and from the directory listing otherwise — a
 * run that is still in flight has audio on disk before its conversation JSON
 * is written, and that audio should still play.
 */
export function listTurnAudio(runId: string, personaId: string): TurnAudioRef[] {
  const refs = new Map<number, TurnAudioRef>();

  const conv = readConversation(runId, personaId);
  if (conv) {
    for (const t of conv.turns) {
      const a = turnAudioFile(runId, personaId, t.idx, t.speaker);
      if (a) refs.set(t.idx, { idx: t.idx, speaker: t.speaker, file: a.file, size: a.size });
    }
  }

  // Sweep the directory too: pick up turns the conversation JSON doesn't know
  // about yet (in-flight runs) without disturbing what it does know.
  const dir = personaAudioDir(runId, personaId);
  for (const name of listDir(dir)) {
    const m = PCM_NAME_RE.exec(name);
    if (!m) continue;
    const idx = Number(m[1]);
    if (refs.has(idx)) continue;
    const speaker = m[2] as Speaker;
    const a = turnAudioFile(runId, personaId, idx, speaker);
    if (a) refs.set(idx, { idx, speaker, file: a.file, size: a.size });
  }

  return [...refs.values()].sort((a, b) => a.idx - b.idx);
}

/** Read one turn's PCM bytes, or null when the file is gone/unreadable. */
export function readTurnPcm(
  runId: string,
  personaId: string,
  turnIdx: number,
  speaker: Speaker
): Buffer | null {
  const a = turnAudioFile(runId, personaId, turnIdx, speaker);
  if (!a) return null;
  try {
    return fs.readFileSync(a.file);
  } catch {
    return null;
  }
}

/**
 * Timeline of the stitched conversation: where each turn starts and how long it
 * runs, in the same seconds the `<audio>` element reports. Computed from real
 * PCM byte lengths, never from metadata.
 */
export function buildTimeline(
  runId: string,
  personaId: string,
  refs: TurnAudioRef[] = listTurnAudio(runId, personaId)
): AudioTimeline {
  const gapBytes = silenceBytes(TURN_GAP_S);
  const turns: TimelineEntry[] = [];
  let offsetBytes = 0;

  refs.forEach((r, i) => {
    if (i > 0) offsetBytes += gapBytes;
    turns.push({
      turnIdx: r.idx,
      speaker: r.speaker,
      startS: round3(pcmDurationS(offsetBytes)),
      durationS: round3(pcmDurationS(r.size)),
    });
    offsetBytes += r.size;
  });

  return {
    runId,
    personaId,
    sampleRate: PCM_SAMPLE_RATE,
    gapS: TURN_GAP_S,
    totalDurationS: round3(pcmDurationS(offsetBytes)),
    byteLength: offsetBytes === 0 ? 0 : offsetBytes + WAV_HEADER_BYTES,
    turns,
  };
}

/**
 * The stitched PCM for a whole conversation (no WAV header yet), plus the refs
 * that actually made it in — a turn whose file vanished mid-read is skipped
 * rather than fatal, and the caller rebuilds the timeline from `used` so the
 * offsets can never drift out of step with the bytes.
 */
export function stitchConversationPcm(refs: TurnAudioRef[]): {
  pcm: Buffer;
  used: TurnAudioRef[];
} {
  const chunks: Buffer[] = [];
  const used: TurnAudioRef[] = [];
  for (const r of refs) {
    try {
      const buf = fs.readFileSync(r.file);
      chunks.push(buf);
      used.push({ ...r, size: buf.length });
    } catch {
      // skipped
    }
  }
  return { pcm: concatPcmWithGaps(chunks, TURN_GAP_S), used };
}

const round3 = (n: number) => Math.round(n * 1000) / 1000;
