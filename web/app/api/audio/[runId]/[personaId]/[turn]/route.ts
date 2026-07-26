/**
 * GET /api/audio/[runId]/[personaId]/[turn]
 *
 * One turn's audio as a playable WAV.
 *
 * On disk the turn is raw headerless PCM (16 kHz, mono, s16le) which no browser
 * will play in an `<audio>` tag, so a canonical 44-byte RIFF/WAVE header is
 * prepended here and the result is served as `audio/wav` with `Content-Length`
 * and Range support (Safari will not scrub without it).
 *
 * Level 0 runs have no `audio/` directory at all: that is a clean 404.
 */

import { readTurnPcm } from '@/lib/audio';
import { safeSegment } from '@/lib/paths';
import { resolveTurnSpeaker } from '@/lib/runs';
import type { Speaker } from '@/lib/types';
import { pcmToWav, wavResponse } from '@/lib/wav';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Params = { params: Promise<{ runId: string; personaId: string; turn: string }> };

export async function GET(req: Request, ctx: Params) {
  const { runId, personaId, turn } = await ctx.params;

  if (!safeSegment(runId) || !safeSegment(personaId)) {
    return Response.json({ error: 'invalid run or persona id' }, { status: 400 });
  }

  // `turn` may carry an explicit speaker: `3` or `3-persona`.
  const m = /^(\d+)(?:[-_.](agent|persona))?$/.exec(turn);
  if (!m) {
    return Response.json(
      { error: `invalid turn: ${turn} (expected a turn index, or "full")` },
      { status: 400 }
    );
  }
  const turnIdx = Number(m[1]);
  const speaker: Speaker | null = (m[2] as Speaker | undefined) ?? resolveTurnSpeaker(runId, personaId, turnIdx);

  if (!speaker) {
    return Response.json(
      { error: `no audio for turn ${turnIdx} of ${personaId} in run ${runId}` },
      { status: 404 }
    );
  }

  const pcm = readTurnPcm(runId, personaId, turnIdx, speaker);
  if (!pcm || pcm.length === 0) {
    return Response.json(
      { error: `no audio for turn ${turnIdx} of ${personaId} in run ${runId}` },
      { status: 404 }
    );
  }

  const wav = pcmToWav(pcm);
  return wavResponse(wav, req, `${runId}_${personaId}_turn_${turnIdx}_${speaker}.wav`);
}

export const HEAD = GET;
