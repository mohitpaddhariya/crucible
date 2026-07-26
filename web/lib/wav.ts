/**
 * Raw PCM → playable WAV.
 *
 * Everything under `runs/<id>/audio/` is headerless PCM: 16000 Hz, mono,
 * signed 16-bit little-endian. No browser will play that in an `<audio>` tag,
 * so we prepend the canonical 44-byte RIFF/WAVE header and serve `audio/wav`.
 *
 * Pure TypeScript arithmetic — no dependency, no shelling out.
 */

import { PCM_BITS_PER_SAMPLE, PCM_CHANNELS, PCM_SAMPLE_RATE } from './types';

export const WAV_HEADER_BYTES = 44;
export const BYTES_PER_SAMPLE = PCM_BITS_PER_SAMPLE / 8; // 2
export const BYTES_PER_SECOND = PCM_SAMPLE_RATE * PCM_CHANNELS * BYTES_PER_SAMPLE; // 32000

/** Seconds of a PCM byte count, at the fixed 16 kHz / mono / 16-bit format. */
export function pcmDurationS(byteLength: number): number {
  return byteLength / BYTES_PER_SECOND;
}

/** Bytes needed to hold `seconds` of silence, rounded to a whole sample frame. */
export function silenceBytes(seconds: number): number {
  const frames = Math.round(seconds * PCM_SAMPLE_RATE);
  return frames * PCM_CHANNELS * BYTES_PER_SAMPLE;
}

/**
 * The canonical 44-byte WAV header for `dataLength` bytes of our PCM.
 *
 *   offset  size  field
 *   0       4     "RIFF"
 *   4       4     chunk size = 36 + dataLength
 *   8       4     "WAVE"
 *   12      4     "fmt "
 *   16      4     subchunk1 size = 16 (PCM)
 *   20      2     audio format = 1 (PCM, uncompressed)
 *   22      2     channels = 1
 *   24      4     sample rate = 16000
 *   28      4     byte rate = 16000 * 1 * 2 = 32000
 *   32      2     block align = 1 * 2 = 2
 *   34      2     bits per sample = 16
 *   36      4     "data"
 *   40      4     dataLength
 */
export function wavHeader(dataLength: number): Buffer {
  const h = Buffer.alloc(WAV_HEADER_BYTES);
  const byteRate = PCM_SAMPLE_RATE * PCM_CHANNELS * BYTES_PER_SAMPLE;
  const blockAlign = PCM_CHANNELS * BYTES_PER_SAMPLE;

  h.write('RIFF', 0, 'ascii');
  h.writeUInt32LE(36 + dataLength, 4);
  h.write('WAVE', 8, 'ascii');
  h.write('fmt ', 12, 'ascii');
  h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20);
  h.writeUInt16LE(PCM_CHANNELS, 22);
  h.writeUInt32LE(PCM_SAMPLE_RATE, 24);
  h.writeUInt32LE(byteRate, 28);
  h.writeUInt16LE(blockAlign, 32);
  h.writeUInt16LE(PCM_BITS_PER_SAMPLE, 34);
  h.write('data', 36, 'ascii');
  h.writeUInt32LE(dataLength, 40);
  return h;
}

/** header + pcm, as a single playable WAV buffer. */
export function pcmToWav(pcm: Buffer): Buffer {
  // An odd byte count would mean a torn sample frame; drop the stray byte
  // rather than emit a WAV whose data chunk doesn't divide by blockAlign.
  const clean = pcm.length % BYTES_PER_SAMPLE === 0 ? pcm : pcm.subarray(0, pcm.length - 1);
  return Buffer.concat([wavHeader(clean.length), clean]);
}

/** Concatenate PCM chunks with `gapS` of digital silence between them. */
export function concatPcmWithGaps(chunks: Buffer[], gapS: number): Buffer {
  if (chunks.length === 0) return Buffer.alloc(0);
  const gap = Buffer.alloc(silenceBytes(gapS));
  const parts: Buffer[] = [];
  chunks.forEach((c, i) => {
    if (i > 0 && gap.length) parts.push(gap);
    parts.push(c);
  });
  return Buffer.concat(parts);
}

// ---------------------------------------------------------------------------
// HTTP: Content-Length + Range, so Safari can scrub.
// ---------------------------------------------------------------------------

export type ParsedRange = { start: number; end: number } | null | 'unsatisfiable';

/**
 * Parse a single-range `Range: bytes=a-b` header against a body of `size` bytes.
 * Returns null for "no/unsupported range, send the whole thing", or
 * 'unsatisfiable' for a 416.
 */
export function parseRange(header: string | null, size: number): ParsedRange {
  if (!header) return null;
  const m = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!m) return null; // multi-range or garbage → just send the whole body
  const [, rawStart, rawEnd] = m;
  if (rawStart === '' && rawEnd === '') return null;

  let start: number;
  let end: number;
  if (rawStart === '') {
    // Suffix range: last N bytes.
    const suffix = Number(rawEnd);
    if (!Number.isFinite(suffix) || suffix <= 0) return 'unsatisfiable';
    start = Math.max(0, size - suffix);
    end = size - 1;
  } else {
    start = Number(rawStart);
    end = rawEnd === '' ? size - 1 : Number(rawEnd);
  }
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  if (start > end || start >= size) return 'unsatisfiable';
  end = Math.min(end, size - 1);
  return { start, end };
}

/**
 * Build the response for a fully-materialised WAV buffer, honouring Range.
 * Handles HEAD by returning the headers with no body.
 */
export function wavResponse(wav: Buffer, req: Request, filename: string): Response {
  const size = wav.length;
  const isHead = req.method === 'HEAD';
  const base: Record<string, string> = {
    'Content-Type': 'audio/wav',
    'Accept-Ranges': 'bytes',
    'Content-Disposition': `inline; filename="${filename}"`,
    'Cache-Control': 'no-store',
    'X-Audio-Duration-S': pcmDurationS(Math.max(0, size - WAV_HEADER_BYTES)).toFixed(3),
  };

  const range = parseRange(req.headers.get('range'), size);

  if (range === 'unsatisfiable') {
    return new Response(null, {
      status: 416,
      headers: { ...base, 'Content-Range': `bytes */${size}` },
    });
  }

  if (range) {
    const { start, end } = range;
    const chunk = wav.subarray(start, end + 1);
    return new Response(isHead ? null : toBody(chunk), {
      status: 206,
      headers: {
        ...base,
        'Content-Range': `bytes ${start}-${end}/${size}`,
        'Content-Length': String(chunk.length),
      },
    });
  }

  return new Response(isHead ? null : toBody(wav), {
    status: 200,
    headers: { ...base, 'Content-Length': String(size) },
  });
}

/**
 * Node Buffer → a Web `Response` body, with no copy.
 * The cast is only to reconcile the DOM lib's `BodyInit`, which is narrower
 * than what the runtime actually accepts for a typed array view.
 */
function toBody(buf: Buffer): BodyInit {
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength) as unknown as BodyInit;
}
