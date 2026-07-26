/**
 * GET /api/runs
 *
 * Every run under `runs/` (scratch dirs like `_spike*` excluded), newest first.
 * Partial runs — conversations but no scorecards, audio but no conversations —
 * list rather than throw.
 */

import { getAllRunSummaries } from '@/lib/runs';
import type { RunSummary } from '@/lib/types';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET() {
  try {
    const runs: RunSummary[] = getAllRunSummaries();
    return Response.json(runs, {
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (e) {
    return Response.json(
      { error: `failed to read runs: ${(e as Error).message}` },
      { status: 500 }
    );
  }
}
