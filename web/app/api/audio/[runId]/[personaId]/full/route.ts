/**
 * GET /api/audio/[runId]/[personaId]/full
 *
 * The whole conversation stitched into ONE playable WAV — every turn in `idx`
 * order, ~0.5 s of silence between speakers, a single 44-byte RIFF/WAVE header
 * on the front. This is the file that makes "two AI agents actually talked"
 * undeniable, so it is served with `Content-Length` and Range support and can
 * be scrubbed in Safari.
 *
 * `?meta=1` returns the timeline JSON instead of audio, so the UI can highlight
 * the turn that is currently playing:
 *   { runId, personaId, sampleRate, gapS, totalDurationS, byteLength,
 *     turns: [{ turnIdx, speaker, startS, durationS }] }
 *
 * A Level 0 run (no `audio/` directory) 404s cleanly.
 */

import { buildTimeline, listTurnAudio, stitchConversationPcm } from '@/lib/audio';
import { safeSegment } from '@/lib/paths';
import { pcmToWav, wavResponse } from '@/lib/wav';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Params = { params: Promise<{ runId: string; personaId: string }> };

export async function GET(req: Request, ctx: Params) {
  const { runId, personaId } = await ctx.params;

  if (!safeSegment(runId) || !safeSegment(personaId)) {
    return Response.json({ error: 'invalid run or persona id' }, { status: 400 });
  }

  const wantsMeta = new URL(req.url).searchParams.get('meta') !== null;
  const refs = listTurnAudio(runId, personaId);

  if (refs.length === 0) {
    return Response.json(
      { error: `no audio for ${personaId} in run ${runId}` },
      { status: 404 }
    );
  }

  if (wantsMeta) {
    return Response.json(buildTimeline(runId, personaId, refs), {
      headers: { 'Cache-Control': 'no-store' },
    });
  }

  const { pcm, used } = stitchConversationPcm(refs);
  if (pcm.length === 0) {
    return Response.json(
      { error: `no readable audio for ${personaId} in run ${runId}` },
      { status: 404 }
    );
  }

  const wav = pcmToWav(pcm);
  const timeline = buildTimeline(runId, personaId, used);
  const res = wavResponse(wav, req, `${runId}_${personaId}_full.wav`);
  // Cheap for the player: total length and turn count without a second request.
  res.headers.set('X-Turn-Count', String(timeline.turns.length));
  return res;
}

export const HEAD = GET;
